# deploy.ps1 — Deploy local SpotAlert files to Steam Deck and restart the service
# Run from the project root: .\deploy.ps1

$HOSTKEY  = 'ssh-ed25519 255 SHA256:TfZ8zX7bUIW/C1MNzzQjxBIX1uOMcesh8nWrKS9hmwg'
$DECK     = 'deck@192.168.4.135'
$PASSWORD = 'REDACTED-DECK-PASSWORD'
$SRC      = $PSScriptRoot
$DEST     = '/home/deck/spotalert'
$PSCP     = 'C:\Program Files\PuTTY\pscp.exe'
$PLINK    = 'C:\Program Files\PuTTY\plink.exe'

# Files to deploy (relative to project root)
$FILES = @(
    'main.py',
    'monitor.py',
    'bot.py',
    'settings.py',
    'military.py',
    'lookup.py',
    'lightroom.py',
    'stats.py',
    'spot_recommendation.py',
    'weather.py',
    'storage\store.py',
    'backfill.py'
)

Write-Host "`nDeploying SpotAlert to Steam Deck..." -ForegroundColor Cyan

foreach ($file in $FILES) {
    $src_path  = Join-Path $SRC $file
    $dest_path = "$DEST/$($file -replace '\\', '/')"
    Write-Host "  Copying $file..."
    & $PSCP -pw $PASSWORD -hostkey $HOSTKEY $src_path "${DECK}:${dest_path}" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  FAILED: $file" -ForegroundColor Red
    }
}

Write-Host "`nRestarting service..." -ForegroundColor Cyan
& $PLINK -ssh $DECK -pw $PASSWORD -hostkey $HOSTKEY "echo '$PASSWORD' | sudo -S systemctl restart spotalert && sleep 3 && echo '$PASSWORD' | sudo -S systemctl status spotalert --no-pager | head -5"

Write-Host "`nDone." -ForegroundColor Green
