@echo off
REM ============================================================
REM  Start SSH tunnel: local 8002 -> cloud GPU lip service (8002)
REM
REM  Use after changing network or rebooting: the tunnel dies with
REM  the old network path and must be rebuilt.
REM  Prerequisite: the cloud lip service is already running
REM  (see docs/RUNBOOK * 3.10 / 3.12).
REM
REM  Measured: this SSH channel carries about 1 MB/s (hard cap,
REM  see docs/RUNBOOK pitfall 15). That is why lip frames are sent
REM  as H.264 segments, not per-frame JPEGs.
REM
REM  NOTE: ASCII-only on purpose. cmd.exe parses .bat in the OEM
REM  codepage (GBK on zh-CN Windows); UTF-8 Chinese/emoji would turn
REM  into mojibake and can break parsing.
REM ============================================================
setlocal
set "HOST=connect.nmb1.seetacloud.com"
set "SSHPORT=47618"
set "USER=root"
set "LIPPORT=8002"
set "PROBE=%TEMP%\lip_health.json"

echo [1/4] Freeing port %LIPPORT% ...
call :freeport
if errorlevel 1 goto stillbusy

echo [2/4] Opening tunnel  localhost:%LIPPORT%  --^>  %HOST%:%LIPPORT%
start "lip-tunnel" /min ssh -N -L %LIPPORT%:127.0.0.1:%LIPPORT% -p %SSHPORT% %USER%@%HOST% -o StrictHostKeyChecking=accept-new -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3

echo [3/4] Probing http://127.0.0.1:%LIPPORT%/health  (retries up to ~10s) ...
REM Do NOT use "timeout /t N": inside git-bash/MSYS that name resolves to GNU
REM coreutils timeout, which rejects /t and aborts the script. curl's own
REM retry is dependency-free and waits only as long as needed.
curl -s --max-time 8 --retry 4 --retry-delay 2 --retry-connrefused -o "%PROBE%" http://127.0.0.1:%LIPPORT%/health

echo [4/4] Result:
type "%PROBE%" 2>nul
echo.
findstr /C:"status" "%PROBE%" >nul 2>&1
if errorlevel 1 goto fail
findstr /C:"FAKE" "%PROBE%" >nul 2>&1
if not errorlevel 1 goto fakewarn
echo ------------------------------------------------------------
echo OK: tunnel is UP and the CLOUD service answered.
echo ------------------------------------------------------------
pause
exit /b 0

:fakewarn
echo ------------------------------------------------------------
echo *** WARNING: port %LIPPORT% is serving the LOCAL FAKE service. ***
echo The cloud tunnel did NOT take effect, so what you see in the UI
echo is a replayed clip, NOT cloud inference. Kill the fake service
echo (or run start-lip-tunnel.bat again after it is stopped) and retry.
echo ------------------------------------------------------------
pause
exit /b 1

:fail
echo ------------------------------------------------------------
echo No healthy lip service answered on port %LIPPORT%. Usual causes:
echo   a) the cloud instance is powered OFF -- port %SSHPORT% accepts TCP
echo      then resets the SSH handshake; that is normal (RUNBOOK pitfall 18)
echo   b) the cloud lip service is not running (RUNBOOK 3.12)
echo   c) your network blocks outbound port %SSHPORT%
echo Close the tunnel: close the minimized ssh window, or kill the
echo process listening on %LIPPORT%.
echo ------------------------------------------------------------
pause
exit /b 1

:freeport
REM Reliable kill: PowerShell reports every listener owner (netstat can list
REM the same port twice for IPv4/IPv6 and taskkill may miss one).
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort %LIPPORT% -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Write-Host ('      killing PID ' + $_); Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }" 2>nul
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%LIPPORT% " ^| findstr LISTENING') do exit /b 1
exit /b 0

:stillbusy
echo [x] Port %LIPPORT% is STILL in use after the kill attempt -- refusing to
echo     continue, because a leftover service would make the UI silently show
echo     the wrong mouth. Check: netstat -ano ^| findstr ":%LIPPORT% "
pause
exit /b 1
