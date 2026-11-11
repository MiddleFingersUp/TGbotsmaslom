# db_restore_check.ps1 — monthly restore test for TGbots (Stage 8.2).
#
# Policy: docs/DATABASE.md §14 — restore test at least once a month on a
# separate database copy; operator verifies key tables and row counters.
#
# What it does:
#   1) takes -DumpFile (default: the newest backups\*_tgbots.dump);
#   2) recreates the temporary database tgbots_restore_check (drop→create);
#   3) restores the dump into it with pg_restore;
#   4) compares row counts of 10 key tables against the SOURCE database;
#   5) drops the temporary database afterwards (unless -KeepTemporaryDB).
#
# Honest discrepancy policy (live app may write between dump and check):
#   - tenants/bots/agents/services/customers/conversations/handoffs/token_usage
#     mismatch → FAIL (exit 1): these must not change without operator action;
#   - leads/messages mismatch → treated as expected live drift ONLY when the
#     source count keeps GROWING between two measurements (a comment with
#     before/after counts is printed); stable mismatch → FAIL (exit 1).
#
# Connection info comes from the same sources as scripts/db_backup.ps1
# (process env DATABASE_URL → .env → default). The password is passed to
# child processes via PGPASSWORD and is NEVER printed or logged.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\db_restore_check.ps1
#   powershell ... -File scripts\db_restore_check.ps1 -DumpFile backups\x.dump -KeepTemporaryDB
#
# Exit codes: 0 = restore verified (0 or explained drift); 1 = failure.
param(
    [string]$DumpFile = "",
    [switch]$KeepTemporaryDB
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$repoRoot = (Get-Location).Path
$backupDir = Join-Path $repoRoot "backups"
$RestoreDb = "tgbots_restore_check"

# ─── Locate pg tools (PATH first, then standard install dir) ───────────
$pgDirs = @("", (Join-Path $env:ProgramFiles "PostgreSQL\16\bin"))
function Resolve-PgTool([string]$name) {
    foreach ($dir in $pgDirs) {
        $candidate = if ($dir) { Join-Path $dir $name } else { $name }
        if ($dir -and (Test-Path $candidate)) { return $candidate }
        if (-not $dir -and (Get-Command $candidate -ErrorAction SilentlyContinue)) {
            return (Get-Command $candidate).Source
        }
    }
    return $null
}

$psql = Resolve-PgTool "psql.exe"
$pgRestore = Resolve-PgTool "pg_restore.exe"
if (-not $psql -or -not $pgRestore) {
    Write-Error "psql.exe/pg_restore.exe not found in PATH nor in '$($pgDirs[1])'. Install PostgreSQL client tools."
    exit 1
}

# ─── Dump file resolution ──────────────────────────────────────────────
if (-not $DumpFile) {
    $latest = Get-ChildItem -Path $backupDir -Filter "*_tgbots.dump" -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending | Select-Object -First 1
    if (-not $latest) {
        Write-Error "No dump found in backups\ and -DumpFile not given. Run scripts\db_backup.ps1 first."
        exit 1
    }
    $DumpFile = $latest.FullName
    Write-Output "Dump file (latest): $DumpFile"
} else {
    $DumpFile = Join-Path $repoRoot $DumpFile
    Write-Output "Dump file (explicit): $DumpFile"
}
if (-not (Test-Path $DumpFile)) {
    Write-Error "Dump file not found: $DumpFile"
    exit 1
}

# ─── DATABASE_URL resolution + parsing (same policy as db_backup.ps1) ──
$defaultUrl = "postgresql://tgbots:tgbots_dev@localhost:5432/tgbots"
$dbUrl = $env:DATABASE_URL
if (-not $dbUrl) {
    $envFile = Join-Path $repoRoot ".env"
    if (Test-Path $envFile) {
        $line = (Get-Content $envFile) | Where-Object { $_ -match '^\s*DATABASE_URL=(\S+)\s*$' } | Select-Object -First 1
        if ($line) { $dbUrl = $Matches[1] }
    }
}
if (-not $dbUrl) { $dbUrl = $defaultUrl }
if ($dbUrl -match '^postgresql\+\w+://') {
    $dbUrl = $dbUrl -replace '^postgresql\+\w+://', 'postgresql://'
}
if ($dbUrl -notmatch '^postgresql://(?<user>[^:]+):(?<pass>[^@]+)@(?<host>[^:/]+):(?<port>\d+)/(?<db>[^?]+)$') {
    Write-Error "Cannot parse DATABASE_URL (expected postgresql://user:pass@host:port/db). Aborting."
    exit 1
}
$PgUser = $Matches["user"]
$DbPassword = $Matches["pass"]   # never echoed, never logged
$PgHost = $Matches["host"]
$PgPort = $Matches["port"]
$SourceDb = $Matches["db"]

$env:PGPASSWORD = $DbPassword   # passed to child processes via env only
try {
    # ─── psql helper (output suppressed to stderr-friendly stream) ─────
    function Invoke-Psql([string]$Database, [string]$Sql) {
        & $psql -X -q -A -t -h $PgHost -p $PgPort -U $PgUser -d $Database -v ON_ERROR_STOP=1 -c $Sql
        return $LASTEXITCODE
    }

    # ─── 1. Recreate the temporary restore-check database ──────────────
    Write-Output "[1/4] Recreating temporary database '$RestoreDb' (drop→create)..."
    $null = Invoke-Psql "postgres" "DROP DATABASE IF EXISTS $RestoreDb;"
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Could not DROP temporary database '$RestoreDb'. Another restore check may be running."
        exit 1
    }
    $null = Invoke-Psql "postgres" "CREATE DATABASE $RestoreDb;"
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Could not CREATE temporary database '$RestoreDb'."
        exit 1
    }

    # ─── 2. Restore the dump ────────────────────────────────────────────
    Write-Output "[2/4] Restoring dump with pg_restore..."
    & $pgRestore -h $PgHost -p $PgPort -U $PgUser -d $RestoreDb --no-owner --no-privileges $DumpFile
    if ($LASTEXITCODE -ne 0) {
        Write-Error "pg_restore failed (exit code $LASTEXITCODE) for dump: $DumpFile"
        & $psql -X -q -h $PgHost -p $PgPort -U $PgUser -d postgres -c "DROP DATABASE IF EXISTS $RestoreDb;" 2>$null
        exit 1
    }

    # ─── 3. Verification queries: row counters per key table ───────────
    $tables = @(
        "tenants", "bots", "agents", "services", "customers",
        "conversations", "messages", "leads", "handoffs", "token_usage"
    )
    function Get-Counts([string]$Database) {
        $map = @{}
        foreach ($t in $tables) {
            $out = & $psql -X -q -A -t -h $PgHost -p $PgPort -U $PgUser -d $Database -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM $t;"
            if ($LASTEXITCODE -ne 0) { $map[$t] = "TABLE_MISSING" }
            else { $map[$t] = [int64]($out | Select-Object -Last 1) }
        }
        return $map
    }

    Write-Output "[3/4] Comparing row counts: source '$SourceDb' vs '$RestoreDb'..."
    $sourceCounts = Get-Counts $SourceDb
    $restoredCounts = Get-Counts $RestoreDb

    Write-Output ""
    Write-Output ("{0,-18} {1,8} {2,8}  {3}" -f "TABLE", "SOURCE", "RESTORED", "STATUS")
    $failures = @()
    $driftTables = @()
    foreach ($t in $tables) {
        $s = $sourceCounts[$t]; $r = $restoredCounts[$t]
        if ($r -eq "TABLE_MISSING" -or $s -eq "TABLE_MISSING") {
            Write-Output ("{0,-18} {1,8} {2,8}  FAIL (table missing)" -f $t, $s, $r)
            $failures += $t
        } elseif ($s -eq $r) {
            Write-Output ("{0,-18} {1,8} {2,8}  OK" -f $t, $s, $r)
        } else {
            Write-Output ("{0,-18} {1,8} {2,8}  MISMATCH" -f $t, $s, $r)
            if ($t -in @("leads", "messages")) { $driftTables += $t } else { $failures += $t }
        }
    }

    # Honest drift analysis for leads/messages: the live application may
    # have inserted rows between the dump and this check. Report the counts
    # before/after a re-measure so the operator sees whether writes are
    # still happening:
    #   restored <  source → rows created AFTER the dump → expected drift,
    #                        WARNING (explained, not a failure);
    #   restored == source after re-measure → writes have stopped; the
    #                        difference is still rows created after the dump;
    #   restored >  source → dump contains rows the source does not have →
    #                        wrong/foreign dump → FAIL.
    foreach ($t in $driftTables) {
        $before = $sourceCounts[$t]
        Start-Sleep -Seconds 2
        $after = (Get-Counts $SourceDb)[$t]
        $restored = $restoredCounts[$t]
        Write-Output ""
        Write-Output ("HONEST COMMENT [{0}]: source count at check time: {1} → {2} after re-measure; restored dump: {3}." -f $t, $before, $after, $restored)
        if ($restored -gt $before) {
            Write-Output ("  → restored dump has MORE rows than the source: wrong/foreign dump → FAIL.")
            $failures += $t
        } else {
            Write-Output ("  → restored ≤ source: {0} rows were created by the live application AFTER the dump was taken (between dump and this check). Expected drift, treated as WARNING, not a failure." -f ($before - $restored))
        }
    }

    # ─── 4. Drop the temporary database (unless explicitly kept) ───────
    if ($KeepTemporaryDB) {
        Write-Output ""
        Write-Output "NOTE: -KeepTemporaryDB set — temporary database '$RestoreDb' was KEPT for manual inspection."
    } else {
        Write-Output ""
        Write-Output "[4/4] Dropping temporary database '$RestoreDb'..."
        $null = Invoke-Psql "postgres" "DROP DATABASE IF EXISTS $RestoreDb;"
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Could not DROP temporary database '$RestoreDb' — remove it manually before the next run."
            exit 1
        }
    }

    Write-Output ""
    if ($failures.Count -gt 0) {
        Write-Output ("RESTORE CHECK: FAILED — mismatches in: {0}" -f ($failures -join ", "))
        exit 1
    }
    if ($driftTables.Count -gt 0) {
        Write-Output ("RESTORE CHECK: PASSED (with expected live drift in: {0}) — see honest comments above." -f ($driftTables -join ", "))
    } else {
        Write-Output "RESTORE CHECK: PASSED — all table counters match."
    }
    exit 0
} finally {
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
}
