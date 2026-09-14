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
set "OPEN_BROWSER=1"
if /I "%~1"=="/noopen" set "OPEN_BROWSER=0"

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
timeout /t 1 /nobreak >nul
goto :backend_loop

:backend_timeout
echo [X] backend did not answer on %BACKEND_PORT% within 40s
echo     check the minimized window "dh-backend-%BACKEND_PORT%"
goto :fail

:backend_ready
echo       backend ready: http://127.0.0.1:%BACKEND_PORT%/api/v1/health

REM ---------------- [3/3] frontend :5173 ----------------
netstat -ano | findstr /C:":%FRONTEND_PORT% " | findstr /C:"LISTENING" >nul
if errorlevel 1 goto :frontend_start
echo [3/3] port %FRONTEND_PORT% already listening - reuse existing frontend
set "FRONT_URL=http://localhost:%FRONTEND_PORT%"
goto :frontend_wait

:frontend_start
echo [3/3] starting frontend on %FRONTEND_PORT% ...
start "dh-frontend-%FRONTEND_PORT%" /min /d "%FRONTEND%" cmd /c "npm run dev"

:frontend_wait
set /a FTRY=0
set "FRONT_URL="
:frontend_loop
REM vite silently shifts to the next free port if 5173 is taken - probe both
curl.exe -s -o nul -f http://localhost:%FRONTEND_PORT%/
if not errorlevel 1 set "FRONT_URL=http://localhost:%FRONTEND_PORT%"
if defined FRONT_URL goto :frontend_ready
curl.exe -s -o nul -f http://localhost:5174/
if not errorlevel 1 set "FRONT_URL=http://localhost:5174"
if defined FRONT_URL goto :frontend_ready
set /a FTRY+=1
if %FTRY% GEQ 60 goto :frontend_timeout
timeout /t 1 /nobreak >nul
goto :frontend_loop

:frontend_timeout
echo [X] frontend did not answer on %FRONTEND_PORT% within 60s
echo     check the minimized window "dh-frontend-%FRONTEND_PORT%"
goto :fail

:frontend_ready
echo       frontend ready: %FRONT_URL%

echo.
echo ============================================================
echo   ALL UP
echo     console : %FRONT_URL%/console
echo     metrics : %FRONT_URL%/metrics
echo     ledger  : %FRONT_URL%/ledger
echo     backend : http://127.0.0.1:%BACKEND_PORT%/api/v1/health
echo.
echo   Stop everything with:  stop.bat
echo   This window can be closed safely.
echo ============================================================
if "%OPEN_BROWSER%"=="1" start "" "%FRONT_URL%/console"
echo.
pause
exit /b 0

:fail
echo.
echo *** STARTUP FAILED ***
pause
exit /b 1
