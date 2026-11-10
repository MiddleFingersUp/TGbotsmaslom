# run_all_tests.ps1 — Hardening test runner for TGbots (Stage 8.1).
#
# Runs the pytest suites sequentially, one command for the whole system:
#   powershell -ExecutionPolicy Bypass -File scripts\run_all_tests.ps1
#   powershell -File scripts\run_all_tests.ps1 -SkipFull   # categories only
#
# Sequence (docs/DEVELOPMENT_PLAN.md §8, Stage 8.1):
#   1) pytest -m security -q      — security / tenant isolation
#   2) pytest -m database -q      — database schema & constraints
#   3) pytest -m agent -q         — agent behavior
#   4) pytest -m unit -q          — full unit baseline
#   5) pytest -q                  — complete regression
#
# Exit code: 0 when ALL five runs pass; 1 otherwise. Failures are
# reported category-by-category in the final summary; output of each
# run is preserved in `test_reports\<category>.log`.
param(
    [switch]$SkipFull
)

$ErrorActionPreference = "Continue"
Set-Location (Join-Path $PSScriptRoot "..")

$python = Join-Path ".venv" "Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

$reportDir = Join-Path (Get-Location) "test_reports"
if (-not (Test-Path $reportDir)) {
    New-Item -ItemType Directory -Path $reportDir | Out-Null
}

$categories = @(
    @{ Name = "security"; Args = @("-m", "security", "-q") },
    @{ Name = "database"; Args = @("-m", "database", "-q") },
    @{ Name = "agent";    Args = @("-m", "agent", "-q") },
    @{ Name = "unit";     Args = @("-m", "unit", "-q") }
)
if (-not $SkipFull) {
    $categories += @{ Name = "full"; Args = @("-q") }
}

Write-Output ("=" * 72)
Write-Output "TGbots hardening run (Stage 8.1) — $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Output ("=" * 72)

$results = @()
foreach ($cat in $categories) {
    $label = if ($cat["Name"] -eq "full") { "pytest -q (full regression)" } else { "pytest -m $($cat["Name"]) -q" }
    Write-Output ""
    Write-Output ("--- {0} ---" -f $label)
    $logFile = Join-Path $reportDir ("{0}.log" -f $cat["Name"])
    & $python -m pytest @($cat["Args"]) 2>&1 | Tee-Object -FilePath $logFile
    $code = $LASTEXITCODE
    $results += [pscustomobject]@{ Category = $cat["Name"]; ExitCode = $code; Log = $logFile }
}

Write-Output ""
Write-Output ("=" * 72)
Write-Output "HARDENING SUMMARY"
Write-Output ("=" * 72)
$failed = $false
foreach ($res in $results) {
    $status = "PASS"
    if ($res.ExitCode -ne 0) { $status = "FAIL"; $failed = $true }
    Write-Output ("{0,9} : {1}   (log: {2})" -f $status, $res.Category, $res.Log)
}
Write-Output ("=" * 72)
if ($failed) {
    Write-Output "RESULT: FAILURES DETECTED — see logs above."
    exit 1
}
Write-Output "RESULT: ALL GREEN"
exit 0
