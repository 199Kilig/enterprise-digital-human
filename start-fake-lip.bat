@echo off
REM ============================================================
REM  FAKE lip service -- protocol integration ONLY (cloud GPU off)
REM
REM  It does NOT generate any lip motion. It replays a REAL MuseTalk
REM  clip (backend\eval\reports\media\lip_demo_8s_crf26.mp4) using the
REM  SPEC 2.1 response shape, to verify the pipeline wiring only.
REM  Any performance / latency number from it is INVALID.
REM
REM  For the real service, power on the cloud instance and run
REM  deploy/lip_service.py (see docs/RUNBOOK * 3.12).
REM
REM  NOTE: ASCII-only on purpose. cmd.exe parses .bat in the OEM
REM  codepage (GBK on zh-CN Windows); UTF-8 Chinese/emoji would turn
REM  into mojibake and can break parsing.
REM ============================================================
setlocal
cd /d "%~dp0"
set "PY=backend\.venv\Scripts\python.exe"
set "SRV=backend\eval\fake_lip_server.py"
set "PORT=8002"

if not exist "%PY%" goto nopy
if not exist "%SRV%" goto nosrv

echo [1/2] Freeing port %PORT% ...
call :freeport
if errorlevel 1 goto stillbusy

echo [2/2] Starting fake lip service on :%PORT%  (Ctrl+C to stop)
echo       WARNING: replays a fixed clip, NOT real inference.
echo.
REM chcp 65001: console to UTF-8 so the Python banner (Chinese + symbols)
REM displays instead of raising UnicodeEncodeError on a GBK console.
chcp 65001 >nul
"%PY%" "%SRV%" --port %PORT%
echo.
echo Fake lip service exited.
pause
exit /b 0

:freeport
REM Reliable kill: PowerShell reports every listener owner (netstat can list
REM the same port twice for IPv4/IPv6 and taskkill may miss one).
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Write-Host ('      killing PID ' + $_); Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }" 2>nul
REM Verify the port is really free (fail loudly instead of silently continuing)
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%PORT% " ^| findstr LISTENING') do exit /b 1
exit /b 0

:nopy
echo [x] Python venv not found: %PY%
echo     Create it first:  cd backend  ^&^&  uv venv .venv  ^&^&  uv pip install -e .
pause
exit /b 1

:nosrv
echo [x] Server script not found: %SRV%
pause
exit /b 1

:stillbusy
echo [x] Port %PORT% is STILL in use after the kill attempt.
echo     Check manually:  netstat -ano ^| findstr ":%PORT% "
echo     Then:            taskkill /F /PID ^<pid^>
pause
exit /b 1
