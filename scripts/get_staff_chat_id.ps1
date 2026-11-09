# One-off helper: find the staff group chat_id for the test bot.
# Reads TELEGRAM_BOT_TOKEN from .env (never from console history).
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\get_staff_chat_id.ps1

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Mask([string]$s) {
    if (-not $s -or $s.Length -lt 8) { return "***" }
    return $s.Substring(0, 4) + "***" + $s.Substring($s.Length - 3)
}

$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root ".env"

if (-not (Test-Path $envFile)) {
    Write-Host "FAIL: .env not found at $envFile"
    Write-Host "Create it and add the line:  TELEGRAM_BOT_TOKEN=<token>"
    exit 1
}

$token = $null
Get-Content $envFile -Encoding UTF8 | ForEach-Object {
    if ($_ -match '^\s*TELEGRAM_BOT_TOKEN\s*=\s*(.+?)\s*$') { $token = $Matches[1] }
}

if (-not $token) {
    Write-Host "FAIL: TELEGRAM_BOT_TOKEN not found in .env"
    exit 1
}

# Strip accidental wrapping: single/double quotes, backticks.
$token = $token.Trim('"', "'", '`').Trim()
# Cut an inline comment if the user wrote  TOKEN=...  # comment
if ($token -match '^(.+?)\b\s+#') { $token = $Matches[1].Trim() }

# Bot API token format: <digits>:<characters>. Validate BEFORE calling API.
if ($token -notmatch '^\d{6,12}:[A-Za-z0-9_\-]{20,}$') {
    Write-Host ("FAIL: token looks malformed: " + (Mask $token))
    Write-Host "Expected format from BotFather:  1234567890:AAExxxxxxxx-xxxxxxxxxxx"
    Write-Host "Check the .env line: no quotes, no extra words, single line:"
    Write-Host "  TELEGRAM_BOT_TOKEN=<exact token string>"
    exit 1
}

Write-Host ("Token accepted by format check: " + (Mask $token))

function ApiGet([string]$method) {
    try {
        return Invoke-RestMethod "https://api.telegram.org/bot$token/$method"
    } catch {
        $desc = $null
        try {
            $sr = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
            $desc = ($sr.ReadToEnd() | ConvertFrom-Json).description
        } catch { $desc = $_.Exception.Message }
        Write-Host "FAIL: Telegram API $method -> $desc"
        if ($desc -match "Not Found") {
            Write-Host "-> 404 Not Found almost always means the token is invalid/corrupted."
            Write-Host "   Re-copy the EXACT token from BotFather into .env (one line, no extra chars)."
        }
        exit 3
    }
}

# 1. Webhook check: getUpdates only works when no webhook is registered.
$wh = ApiGet "getWebhookInfo"
if ($wh.result.url) {
    Write-Host "Webhook is registered -> $($wh.result.url)"
    Write-Host "Deleting webhook so getUpdates can see updates..."
    $del = ApiGet "deleteWebhook"
    if ($del.ok) { Write-Host "Webhook deleted." }
} else {
    Write-Host "No webhook registered. OK."
}

# 2. Fetch updates and print all group chats (id < 0).
$updates = (ApiGet "getUpdates").result

$groups = @{}
foreach ($u in $updates) {
    $chat = $u.message.chat
    if ($null -ne $chat -and $chat.id -lt 0) {
        $groups[$chat.id] = $chat.title
    }
}

if ($groups.Count -eq 0) {
    Write-Host ""
    Write-Host "Token is valid, but no group messages found in the update queue."
    Write-Host "-> Write ANY message into the staff group, then run this script again."
    if ($wh.result.pending_update_count -gt 0) {
        Write-Host "(queued updates reported by Telegram: $($wh.result.pending_update_count))"
    }
    exit 2
}

Write-Host ""
Write-Host "=== STAFF GROUP CHAT IDS ==="
foreach ($id in $groups.Keys) {
    Write-Host "ID: $id   GROUP: $($groups[$id])"
}
Write-Host ""
Write-Host "Add to .env:  TEST_STAFF_CHAT_ID=<negative ID above>"
