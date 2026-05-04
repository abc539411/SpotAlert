@echo off
setlocal

set HOSTKEY=ssh-ed25519 255 SHA256:TfZ8zX7bUIW/C1MNzzQjxBIX1uOMcesh8nWrKS9hmwg
set HOST=deck@192.168.4.135
set PASSWORD=MyIPod88
set SRC=%~dp0
set DEST=/home/deck/spotalert
set PSCP="C:\Program Files\PuTTY\pscp.exe"
set PLINK="C:\Program Files\PuTTY\plink.exe"

echo.
echo Deploying SpotAlert to Steam Deck...
echo.

for %%F in (
    main.py
    monitor.py
    bot.py
    settings.py
    military.py
    lookup.py
    lightroom.py
    stats.py
    spot_recommendation.py
    weather.py
) do (
    echo   Copying %%F...
    %PSCP% -pw %PASSWORD% -hostkey "%HOSTKEY%" "%SRC%%%F" "%HOST%:%DEST%/%%F" >nul 2>&1
)

echo   Copying storage\store.py...
%PSCP% -pw %PASSWORD% -hostkey "%HOSTKEY%" "%SRC%storage\store.py" "%HOST%:%DEST%/storage/store.py" >nul 2>&1

echo.
echo Restarting service...
%PLINK% -ssh %HOST% -pw %PASSWORD% -hostkey "%HOSTKEY%" "echo '%PASSWORD%' | sudo -S systemctl restart spotalert && sleep 3 && echo '%PASSWORD%' | sudo -S systemctl status spotalert --no-pager | head -5"

echo.
echo Done.
pause
