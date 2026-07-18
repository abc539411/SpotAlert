# deploy.ps1 — Deploy SpotAlert to Steam Deck and restart the service
# Run from the project root: .\deploy.ps1

$HOSTKEY  = 'ssh-ed25519 255 SHA256:TfZ8zX7bUIW/C1MNzzQjxBIX1uOMcesh8nWrKS9hmwg'
$DECK     = 'deck@192.168.4.135'

# Password lives in deploy.local.ps1 (gitignored, not committed). It must
# set $DECK_PASSWORD. See deploy.local.ps1.example for the expected format.
$localConfig = Join-Path $PSScriptRoot 'deploy.local.ps1'
if (-not (Test-Path $localConfig)) {
    throw "Missing $localConfig — copy deploy.local.ps1.example to deploy.local.ps1 and fill in `$DECK_PASSWORD."
}
. $localConfig
$PASSWORD = $DECK_PASSWORD

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
    'monitor_runner.py',
    'lightroom.py',
    'system_status.py',
    'push.py',
    'requirements.txt',
    'flightradar24api\api.py',
    'static\index.html',
    'static\app.js',
    'static\sw.js',
    'static\manifest.json'
)

Write-Host "`nDeploying SpotAlert to Steam Deck..." -ForegroundColor Cyan

# Auto-bump the Service Worker's cache version on every deploy. Without this,
# sw.js's byte content never changes between deploys, so browsers never detect
# an update and keep serving whatever index.html/app.js snapshot they cached
# at the CURRENT version — every other file this script copies can change
# freely and users (including live testing here) silently keep seeing stale
# pages indefinitely, no matter how many times this script runs. Bumping here
# makes "the fix isn't showing up" categorically impossible to cause by
# forgetting a manual version edit.
$swPath = Join-Path $SRC 'static\sw.js'
$swContent = Get-Content $swPath -Raw
if ($swContent -match "const CACHE\s*=\s*'spotalert-v(\d+)'") {
    $nextVer = [int]$Matches[1] + 1
    $swContent = $swContent -replace "const CACHE\s*=\s*'spotalert-v\d+'", "const CACHE        = 'spotalert-v$nextVer'"
    Set-Content -Path $swPath -Value $swContent -NoNewline
    Write-Host "  Bumped Service Worker cache to spotalert-v$nextVer" -ForegroundColor DarkGray
} else {
    Write-Host "  WARNING: could not find CACHE version string in sw.js to bump" -ForegroundColor Yellow
}

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
# process. Root cause (finally confirmed): a "python main.py" process can
# survive independent of systemd entirely — e.g. started manually at some
# point in the past — and just keeps re-winning the bind race against every
# subsequent systemd-managed instance, forever, since `systemctl stop` only
# ever touches the process systemd itself is tracking. This previously showed
# up as "the deploy succeeded and the PID looks fresh, but the server keeps
# behaving like the old code" — the freshly restarted process was crash-
# looping on EADDRINUSE in the background the whole time while the true
# orphan kept serving traffic. The `lsof` loop below already detects this
# case; the fix is to actually kill whatever it finds still holding the port
# after the wait instead of giving up and starting the new instance anyway
# (which would just lose the bind race and crash-loop). __pycache__ is also
# cleared as a harmless extra precaution, though it was not the actual cause.
# Each sudo call below pipes its own password via -S rather than relying on
# sudo's timestamp cache from an earlier call in the same script — that
# caching isn't guaranteed reliable over a plink/SSH session (observed once:
# "sudo: a terminal is required to read the password" on a later call despite
# an earlier -S call succeeding moments before).
$restartScript = @'
echo '__PW__' | sudo -S systemctl stop spotalert
for i in $(seq 1 20); do
    echo '__PW__' | sudo -S lsof -i :8088 >/dev/null 2>&1 || break
    sleep 1
done
for PID in $(echo '__PW__' | sudo -S lsof -t -i :8088 2>/dev/null); do
    echo '__PW__' | sudo -S kill -9 "$PID" 2>/dev/null
done
sleep 1
find /home/deck/spotalert -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null
echo '__PW__' | sudo -S systemctl start spotalert
sleep 8
echo '__PW__' | sudo -S systemctl status spotalert --no-pager | head -5
echo Remaining processes on port 8088, should be exactly one:
echo '__PW__' | sudo -S lsof -i :8088
'@ -replace '__PW__', $PASSWORD

& $PLINK -ssh $DECK -pw $PASSWORD -hostkey $HOSTKEY $restartScript

# Post-start health verification, done from here rather than in the embedded
# bash script above (PID-comparison logic over plink/SSH proved too fragile
# to quote reliably — see git history). If the server was mid-restart-race
# (a second-order variant of the stale-orphan bug: the old process hadn't
# fully released the socket when the new one tried to bind, so a LATER
# systemd-spawned replacement can win while `systemctl status` reports an
# earlier, non-serving PID as healthy), retry the kill+restart once more.
Write-Host "`nVerifying server health..." -ForegroundColor Cyan
$healthy = $false
for ($i = 0; $i -lt 3; $i++) {
    try {
        $resp = Invoke-WebRequest -Uri "http://192.168.4.135:8088/api/me" -TimeoutSec 5 -UseBasicParsing
        if ($resp.StatusCode -eq 200) { $healthy = $true; break }
    } catch {}
    Write-Host "  Not responding yet, retrying..."
    Start-Sleep -Seconds 3
}
if ($healthy) {
    Write-Host "Server is responding." -ForegroundColor Green
} else {
    Write-Host "Server still not responding - forcing a clean restart..." -ForegroundColor Yellow
    $forceScript = @'
for PID in $(echo '__PW__' | sudo -S lsof -t -i :8088 2>/dev/null); do
    echo '__PW__' | sudo -S kill -9 "$PID" 2>/dev/null
done
sleep 1
echo '__PW__' | sudo -S systemctl restart spotalert
sleep 8
echo '__PW__' | sudo -S systemctl status spotalert --no-pager | head -5
'@ -replace '__PW__', $PASSWORD
    & $PLINK -ssh $DECK -pw $PASSWORD -hostkey $HOSTKEY $forceScript
    try {
        $resp = Invoke-WebRequest -Uri "http://192.168.4.135:8088/api/me" -TimeoutSec 5 -UseBasicParsing
        if ($resp.StatusCode -eq 200) { Write-Host "Server is responding after retry." -ForegroundColor Green }
    } catch {
        Write-Host "Server STILL not responding - needs manual investigation." -ForegroundColor Red
    }
}

Write-Host "`nDone." -ForegroundColor Green
