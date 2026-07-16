# pull_translations.ps1 - Pull the Baidu name-translation cache down from the
# Steam Deck test server for editing, then opens it locally.
# Run from the project root: .\pull_translations.ps1

$HOSTKEY  = 'ssh-ed25519 255 SHA256:TfZ8zX7bUIW/C1MNzzQjxBIX1uOMcesh8nWrKS9hmwg'
$DECK     = 'deck@192.168.4.135'
$PASSWORD = 'REDACTED-DECK-PASSWORD'
$SRC      = $PSScriptRoot
$PSCP     = 'C:\Program Files\PuTTY\pscp.exe'

$REMOTE_PATH = '/home/deck/spotalert/static/translations/names_zh.json'
$LOCAL_DIR   = Join-Path $SRC 'static\translations'
$LOCAL_PATH  = Join-Path $LOCAL_DIR 'names_zh.json'

if (-not (Test-Path $LOCAL_DIR)) {
    New-Item -ItemType Directory -Path $LOCAL_DIR -Force | Out-Null
}

Write-Host "Pulling names_zh.json from the test server..." -ForegroundColor Cyan
& $PSCP -pw $PASSWORD -hostkey $HOSTKEY "${DECK}:${REMOTE_PATH}" $LOCAL_PATH
if ($LASTEXITCODE -ne 0) {
    Write-Host "FAILED to pull the file." -ForegroundColor Red
    exit 1
}

Write-Host "Pulled to $LOCAL_PATH" -ForegroundColor Green
Write-Host "Opening for editing... run .\push_translations.ps1 when you're done." -ForegroundColor Cyan
Start-Process $LOCAL_PATH
