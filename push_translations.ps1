# push_translations.ps1 - Push your locally-edited name-translation cache back
# up to the Steam Deck test server. Validates the JSON first so a typo can't
# corrupt the live cache file. No server restart needed - the backend reads
# this file fresh on every /api/translate-names request, no in-memory cache.
# Run from the project root: .\push_translations.ps1

$HOSTKEY  = 'ssh-ed25519 255 SHA256:TfZ8zX7bUIW/C1MNzzQjxBIX1uOMcesh8nWrKS9hmwg'
$DECK     = 'deck@192.168.4.135'
$PASSWORD = 'REDACTED-DECK-PASSWORD'
$SRC      = $PSScriptRoot
$PSCP     = 'C:\Program Files\PuTTY\pscp.exe'

$REMOTE_PATH = '/home/deck/spotalert/static/translations/names_zh.json'
$LOCAL_PATH  = Join-Path $SRC 'static\translations\names_zh.json'

if (-not (Test-Path $LOCAL_PATH)) {
    Write-Host "No local copy found at $LOCAL_PATH - run .\pull_translations.ps1 first." -ForegroundColor Red
    exit 1
}

Write-Host "Validating JSON..." -ForegroundColor Cyan
try {
    Get-Content $LOCAL_PATH -Raw -Encoding utf8 | ConvertFrom-Json -ErrorAction Stop | Out-Null
} catch {
    Write-Host "INVALID JSON - not pushing. Fix the syntax error and try again:" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
Write-Host "Valid." -ForegroundColor Green

Write-Host "Pushing names_zh.json to the test server..." -ForegroundColor Cyan
& $PSCP -pw $PASSWORD -hostkey $HOSTKEY $LOCAL_PATH "${DECK}:${REMOTE_PATH}"
if ($LASTEXITCODE -ne 0) {
    Write-Host "FAILED to push the file." -ForegroundColor Red
    exit 1
}

Write-Host "Done - live immediately, no restart needed." -ForegroundColor Green
