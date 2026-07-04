# deploy.ps1 — Deploy SpotAlert to Steam Deck and restart the service
# Run from the project root: .\deploy.ps1

$HOSTKEY  = 'ssh-ed25519 255 SHA256:TfZ8zX7bUIW/C1MNzzQjxBIX1uOMcesh8nWrKS9hmwg'
$DECK     = 'deck@192.168.4.135'
$PASSWORD = 'REDACTED-DECK-PASSWORD'
$SRC      = $PSScriptRoot
$DEST     = '/home/deck/spotalert'
$PSCP     = 'C:\Program Files\PuTTY\pscp.exe'
$PLINK    = 'C:\Program Files\PuTTY\plink.exe'

$FILES = @(
    'main.py',
    'monitor.py',
    'military.py',
    'jetphotos.py',
    'weather.py',
    'web.py',
    'store.py',
    'control_store.py',
    'auth.py',
    'lightroom.py',
    'system_status.py',
    'requirements.txt',
    'static\index.html',
    'static\app.js',
    'static\sw.js',
    'static\manifest.json'
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
# Stop first and wait for port 8088 to actually free up before starting the new
# process. The monitor/military async loops don't respond to SIGTERM instantly,
# so a plain `systemctl restart` can start the new process while the old one is
# still bound to the port, causing a bind failure that only self-heals after a
# few retries. Polling here makes the restart deterministic.
$restartScript = @'
echo '__PW__' | sudo -S systemctl stop spotalert
for i in $(seq 1 20); do
    echo '__PW__' | sudo -S lsof -i :8088 >/dev/null 2>&1 || break
    sleep 1
done
echo '__PW__' | sudo -S systemctl start spotalert
sleep 2
echo '__PW__' | sudo -S systemctl status spotalert --no-pager | head -5
'@ -replace '__PW__', $PASSWORD

& $PLINK -ssh $DECK -pw $PASSWORD -hostkey $HOSTKEY $restartScript

Write-Host "`nDone." -ForegroundColor Green
