# deploy_nas.ps1 - Deploy SpotAlert to the Synology NAS as a Docker container
# and (re)build/restart it. Run from the project root: .\deploy_nas.ps1
#
# The Deck's deploy.ps1 is untouched and still works independently - the Deck
# stays available as a dev/fallback target even after the NAS becomes the
# live deployment.
#
# Note: this NAS's SSH setup doesn't support pscp/sftp file transfer (both
# fail with "unexpected end-of-file from server" - some custom shell/session
# restriction on this host) even though plink command execution works fine.
# File sync therefore goes over SMB (the "public" share, confirmed mapped to
# /Volume2/public) via robocopy, and only remote *commands* (docker build/up,
# health checks) go over SSH via plink.

$HOSTKEY   = 'ssh-ed25519 255 SHA256:ZvfO5/+fXwYeGWley/hI4XdjHmFRwEOzYMyNSHJior4'
$NAS_SSH   = 'abc539411@192.168.4.100'
$NAS_PORT  = 9222
$PASSWORD  = 'REDACTED-NAS-PASSWORD'
$SRC       = $PSScriptRoot
$SMB_ROOT  = '\\192.168.4.100\public\docker\spotalert-app'
$DOCKER    = '/Volume2/@apps/DockerEngine/dockerd/bin/docker'
$BUILD_DIR = '/Volume2/public/docker/spotalert-app'
$PLINK     = 'C:\Program Files\PuTTY\plink.exe'

Write-Host "`nDeploying SpotAlert to NAS (Docker)..." -ForegroundColor Cyan

# Ensure an authenticated SMB session exists for the robocopy below.
net use \\192.168.4.100\public /user:abc539411 $PASSWORD 2>&1 | Out-Null

Write-Host "  Syncing code to $SMB_ROOT ..."
# Mirrors the repo into the NAS build context, excluding local-only /
# volume-mounted / dev-only directories: data, lightroom, logs, translations
# (all separately volume-mounted - mirroring them here would either bake a
# stale snapshot into the image or, worse, get overwritten INTO the live
# volumes on next container start if paths ever collided), .git, studio (the
# local-only companion app), docs (personal notes), backups, venv/__pycache__.
robocopy $SRC $SMB_ROOT /MIR /COPY:DAT /NFL /NDL /NJH /NJS `
    /XD "$SRC\.git" "$SRC\studio" "$SRC\docs" "$SRC\data" "$SRC\lightroom" "$SRC\logs" `
        "$SRC\backups" "$SRC\.venv" "$SRC\venv" "$SRC\__pycache__" `
    /XF "*.pyc"
if ($LASTEXITCODE -ge 8) {
    Write-Host "  FAILED: robocopy exit code $LASTEXITCODE" -ForegroundColor Red
    exit 1
}

Write-Host "`nBuilding and starting container..." -ForegroundColor Cyan
$deployScript = @"
cd $BUILD_DIR
$DOCKER compose up -d --build
"@

& $PLINK -ssh $NAS_SSH -P $NAS_PORT -pw $PASSWORD -hostkey $HOSTKEY $deployScript

Write-Host "`nVerifying server health..." -ForegroundColor Cyan
$healthy = $false
for ($i = 0; $i -lt 10; $i++) {
    try {
        $resp = Invoke-WebRequest -Uri "http://192.168.4.100:7478/api/me" -TimeoutSec 5 -UseBasicParsing
        if ($resp.StatusCode -eq 200) { $healthy = $true; break }
    } catch {}
    Write-Host "  Not responding yet, retrying..."
    Start-Sleep -Seconds 3
}
if ($healthy) {
    Write-Host "Server is responding." -ForegroundColor Green
} else {
    Write-Host "Server not responding yet - check 'docker logs spotalert' on the NAS." -ForegroundColor Yellow
}

Write-Host "`nDone." -ForegroundColor Green
