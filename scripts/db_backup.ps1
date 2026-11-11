# db_backup.ps1 — logical backup tool for TGbots (Stage 8.2).
#
# Policy: docs/DATABASE.md §14 — full daily backup, 30-day rotation,
# monthly restore test (scripts/db_restore_check.ps1). Tool decision
# DB-OD-5 stays OPEN; this script is provider-neutral (works locally now
# and on a future VPS with plain pg_dump).
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\db_backup.ps1
#   powershell ... -File scripts\db_backup.ps1 -Prune   # + rotate (keep last 30)
#
# Data sources (in priority order, password is NEVER printed or written):
#   1) DATABASE_URL from process environment;
#   2) DATABASE_URL from .env in the repo root;
#   3) default postgresql://tgbots:tgbots_dev@localhost:5432/tgbots
# Dumps go to backups\YYYYMMDD_HHMMSS_tgbots.dump (PostgreSQL custom
# format, -Fc). backups\ is git-ignored — real dumps never enter the repo.
#
# Exit codes: 0 = dump created and non-empty; 1 = any failure.
param(
    [switch]$Prune
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$repoRoot = (Get-Location).Path
$backupDir = Join-Path $repoRoot "backups"
$keepLast = 30   # DATABASE.md §14: 30-day rotation window

# ─── Locate pg_dump / psql (PATH first, then standard install dir) ─────
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

$pgDump = Resolve-PgTool "pg_dump.exe"
if (-not $pgDump) {
    Write-Error "pg_dump.exe not found in PATH nor in '$($pgDirs[1])'. Install PostgreSQL client tools."
    exit 1
}

# ─── DATABASE_URL resolution + parsing (password stays in variable) ────
$defaultUrl = "postgresql://tgbots:tgbots_dev@localhost:5432/tgbots"
$dbUrl = $env:DATABASE_URL
$source = "process environment"
if (-not $dbUrl) {
    $envFile = Join-Path $repoRoot ".env"
    if (Test-Path $envFile) {
        $line = (Get-Content $envFile) | Where-Object { $_ -match '^\s*DATABASE_URL=(\S+)\s*$' } | Select-Object -First 1
        if ($line) {
            $dbUrl = $Matches[1]
            $source = ".env"
        }
    }
}
if (-not $dbUrl) {
    $dbUrl = $defaultUrl
    $source = "default (no DATABASE_URL in env or .env)"
}

# Strip the async driver suffix used by SQLAlchemy (postgresql+asyncpg://…).
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
$DbName = $Matches["db"]

Write-Output ("DB backup source: {0}; host={1}:{2} db={3} user={4}" -f $source, $PgHost, $PgPort, $DbName, $PgUser)

# ─── Dump (custom format) ──────────────────────────────────────────────
if (-not (Test-Path $backupDir)) {
    New-Item -ItemType Directory -Path $backupDir | Out-Null
}
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$outFile = Join-Path $backupDir ("{0}_tgbots.dump" -f $stamp)

$env:PGPASSWORD = $DbPassword   # passed to the child process via env only
try {
    & $pgDump -h $PgHost -p $PgPort -U $PgUser -d $DbName -Fc -f $outFile
    $exit = $LASTEXITCODE
} finally {
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
}
if ($exit -ne 0) {
    Write-Error "pg_dump failed (exit code $exit). Dump: $outFile"
    exit 1
}

# ─── Post-conditions: file exists and is non-empty ─────────────────────
if (-not (Test-Path $outFile)) {
    Write-Error "pg_dump reported success but the dump file is missing: $outFile"
    exit 1
}
$size = (Get-Item $outFile).Length
if ($size -le 0) {
    Write-Error "Dump file is empty (0 bytes): $outFile"
    exit 1
}

Write-Output ("BACKUP OK: {0} ({1} bytes)" -f $outFile, $size)

# ─── Rotation: delete old dumps ONLY with explicit -Prune ──────────────
if ($Prune) {
    $all = Get-ChildItem -Path $backupDir -Filter "*_tgbots.dump" | Sort-Object Name -Descending
    $stale = $all | Select-Object -Skip $keepLast
    foreach ($f in $stale) {
        Write-Output "ROTATE: removing $($f.Name)"
        Remove-Item $f.FullName
    }
    Write-Output ("ROTATION: kept last {0} dumps (now {1} total)." -f $keepLast, ($all.Count - $stale.Count))
} else {
    Write-Output ("NOTE: rotation skipped (run with -Prune to keep only the last {0} dumps)." -f $keepLast)
}

exit 0
