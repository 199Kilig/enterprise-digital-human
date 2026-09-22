@echo off
REM ===========================================================================
REM  Enterprise Digital Human - one click dev launcher
REM  Starts: backend (FastAPI/uvicorn :8010) + frontend (Vite :5173) + browser
REM
REM  NOTE: this file is intentionally ASCII-only. cmd.exe parses .bat files with
REM  the local code page (GBK) before "chcp 65001" takes effect, so UTF-8 Chinese
REM  text here gets split into garbage lines. Chinese paths are fine (they come
REM  from the file system at runtime, not from these bytes).
REM
REM  Usage: double click, or:  start.bat [/noopen]
REM ===========================================================================
setlocal EnableExtensions
title Digital Human - launcher

cd /d "%~dp0"
set "ROOT=%~dp0"
set "BACKEND=%ROOT%backend"
set "FRONTEND=%ROOT%frontend"
set "BACKEND_PORT=8010"
set "FRONTEND_PORT=5173"
set "LIP_PORT=8002"
set "OPEN_BROWSER=1"
if /I "%~1"=="/noopen" set "OPEN_BROWSER=0"

REM Marker served ONLY by this project's vite dev server (frontend/index.html).
REM Used to tell "our own dev server is already up" apart from "another app took
REM the port" - reusing the latter is how you end up staring at someone else's UI.
set "DEV_MARKER=src/main.tsx"

echo ============================================================
echo   Realtime Digital Human - pipeline console
echo   root: %ROOT%
echo ============================================================
echo.

REM ---------------- prerequisites ----------------
if not exist "%BACKEND%\.venv\Scripts\python.exe" (
    echo [X] backend venv not found:
    echo     %BACKEND%\.venv
    echo     one time setup:  cd backend
    echo                      uv venv
    echo                      uv pip install -e .
    goto :fail
)
where node >nul 2>nul
if errorlevel 1 (
    echo [X] node.js not found in PATH
    goto :fail
)

REM ---------------- [1/3] frontend deps ----------------
if exist "%FRONTEND%\node_modules" goto :deps_ok
echo [1/3] first run: installing frontend deps ^(1-3 min^) ...
pushd "%FRONTEND%"
call npm install --no-fund --no-audit
call npm install-scripts approve esbuild >nul 2>nul
popd
if not exist "%FRONTEND%\node_modules" (
    echo [X] npm install failed - see output above
    goto :fail
)
goto :deps_done
:deps_ok
echo [1/3] frontend deps: OK
:deps_done

REM ---------------- [2/3] backend :8010 ----------------
REM load backend\.env (gitignored) so the child process inherits API keys
if exist "%BACKEND%\.env" (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%BACKEND%\.env") do set "%%A=%%B"
    echo       loaded backend\.env
)

netstat -ano | findstr /C:":%BACKEND_PORT% " | findstr /C:"LISTENING" >nul
if errorlevel 1 goto :backend_start
echo [2/3] port %BACKEND_PORT% already listening - reuse existing backend
goto :backend_wait

:backend_start
echo [2/3] starting backend on %BACKEND_PORT% ...
start "dh-backend-%BACKEND_PORT%" /min /d "%BACKEND%" cmd /c "set PYTHONPATH=src&& .venv\Scripts\python.exe -m uvicorn api.routes:app --host 127.0.0.1 --port %BACKEND_PORT%"

:backend_wait
set /a BTRY=0
:backend_loop
curl.exe -s -o nul -f http://127.0.0.1:%BACKEND_PORT%/api/v1/health
if not errorlevel 1 goto :backend_ready
set /a BTRY+=1
if %BTRY% GEQ 40 goto :backend_timeout
REM RUNBOOK pit 22: never use "timeout /t" here - under git-bash/MSYS the GNU
REM timeout shadows it and aborts the script. ping gives the same ~1s pause
REM without PATH or stdin assumptions.
ping -n 2 127.0.0.1 >nul
goto :backend_loop

:backend_timeout
echo [X] backend did not answer on %BACKEND_PORT% within 40s
echo     check the minimized window "dh-backend-%BACKEND_PORT%"
echo     if another app owns that port in silence, close it (or see stop.bat)
goto :fail

:backend_ready
echo       backend ready: http://127.0.0.1:%BACKEND_PORT%/api/v1/health

REM ---------------- [3/3] frontend :5173 ----------------
REM vite.config.ts pins strictPort, so the dev server never drifts to 5174:
REM either it owns 5173 or it fails. What we DO have to check is who owns the
REM port, because blindly reusing an occupied port is how a stale/foreign UI
REM shows up (that is exactly the "why does it look like the old interface"
REM trap). Only reuse a listener that serves our own dev marker.
netstat -ano | findstr /C:":%FRONTEND_PORT% " | findstr /C:"LISTENING" >nul
if errorlevel 1 goto :frontend_start

call :check_dev %FRONTEND_PORT%
if errorlevel 1 (
    echo [X] port %FRONTEND_PORT% is taken, but it is NOT this project's dev server.
    echo     Reusing it would show you a different or outdated UI - refusing to continue.
    echo     Fix:  run stop.bat, or close whatever occupies %FRONTEND_PORT%, then start again.
    goto :fail
)
echo [3/3] port %FRONTEND_PORT% busy but verified as our dev server - reusing it
set "FRONT_URL=http://localhost:%FRONTEND_PORT%"
goto :frontend_ready

:frontend_start
echo [3/3] starting frontend on %FRONTEND_PORT% ...
start "dh-frontend-%FRONTEND_PORT%" /min /d "%FRONTEND%" cmd /c "npm run dev"

set /a FTRY=0
:frontend_loop
call :check_dev %FRONTEND_PORT%
if not errorlevel 1 goto :frontend_ok
set /a FTRY+=1
if %FTRY% GEQ 60 goto :frontend_timeout
ping -n 2 127.0.0.1 >nul
goto :frontend_loop

:frontend_timeout
echo [X] frontend did not answer on %FRONTEND_PORT% within 60s
echo     check the minimized window "dh-frontend-%FRONTEND_PORT%"
echo     a port clash here usually means something else grabbed %FRONTEND_PORT%
goto :fail

:frontend_ok
set "FRONT_URL=http://localhost:%FRONTEND_PORT%"

:frontend_ready
echo       frontend ready: %FRONT_URL%

REM optional: cloud lip service (drives the avatar's mouth). Not fatal if down -
REM the page still works, the avatar just stays idle. Checked via the SSH tunnel
REM on 127.0.0.1:8002 -> cloud GPU; restore with backend\deploy\restore-cloud-lip.sh
set "LIP_STATE=NOT reachable - avatar will not talk"
curl.exe -s -m 3 http://127.0.0.1:%LIP_PORT%/health >nul 2>nul
if not errorlevel 1 set "LIP_STATE=connected"

echo.
echo ============================================================
echo   ALL UP
echo     home    : %FRONT_URL%/
echo     console : %FRONT_URL%/console
echo     studio  : %FRONT_URL%/studio
echo     metrics : %FRONT_URL%/metrics
echo     ledger  : %FRONT_URL%/ledger
echo     backend : http://127.0.0.1:%BACKEND_PORT%/api/v1/health
echo     lip svc : %LIP_STATE%
echo.
echo   Stop everything with:  stop.bat
echo   This window can be closed safely.
echo.
echo   UI looks outdated?  Press Ctrl+Shift+R in the browser ^(hard refresh^) -
echo   a normal F5 can serve the cached page after a frontend change.
echo   Avatar not talking?  Lip service is down; in git-bash run:
echo     bash backend/deploy/restore-cloud-lip.sh ^<ssh-port^>
echo ============================================================
REM Land on the learning dashboard (home), not the chat page: the dashboard is
REM the product front door, and it is also where UI changes are most visible.
if "%OPEN_BROWSER%"=="1" start "" "%FRONT_URL%/"
echo.
pause
exit /b 0

:fail
echo.
echo *** STARTUP FAILED ***
pause
exit /b 1

REM ---------------- helpers ----------------
REM errorlevel 0 when the port serves OUR vite dev server (dev marker present).
:check_dev
curl.exe -s -m 3 http://localhost:%~1/ 2>nul | findstr /C:"%DEV_MARKER%" >nul
if errorlevel 1 exit /b 1
exit /b 0
