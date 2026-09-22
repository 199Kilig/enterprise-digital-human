@echo off
REM ===========================================================================
REM  Enterprise Digital Human - stop dev services (backend :8010, frontend :5173)
REM  Kills the process that actually LISTENS on those ports (the real owner, not
REM  the npm wrapper - killing npm alone leaves the vite child alive).
REM
REM  Ownership check: before killing anything we read the target process command
REM  line and require it to match this project. Anything else that happens to sit
REM  on these ports (another project's dev server, an unrelated tool) is reported
REM  and left alone - a blind taskkill on "whoever owns 5173" can kill someone
REM  else's work, and 4173 is a port that unrelated tools also like to grab.
REM
REM  ASCII only on purpose (see start.bat header).
REM ===========================================================================
setlocal EnableExtensions EnableDelayedExpansion
title Digital Human - stop

cd /d "%~dp0"
set "ROOT=%~dp0"

echo ============================================================
echo   stopping dev services
echo ============================================================
echo.

REM Port + ownership marker (ASCII only). The comparison runs INSIDE PowerShell,
REM never via findstr: this repo path contains Chinese characters, and comparing
REM them across a cmd pipe hits code-page problems - measured: a Chinese marker
REM never matched our own vite process, so stop.bat silently refused to kill it.
REM ASCII marker + in-PowerShell -like keeps the whole comparison in Unicode.
REM   8010 uvicorn  -> "-m uvicorn api.routes:app" (cwd is the repo; the repo path
REM                    itself never appears in the command line)
REM   5173/4173/5174 vite (dev and preview alike) ->
REM                    <repo>\frontend\node_modules\.bin\..\vite\bin\vite.js
call :killport 8010 "api.routes:app"
call :killport 5173 "node_modules\.bin"
call :killport 4173 "node_modules\.bin"
call :killport 5174 "node_modules\.bin"

REM cloud lip tunnel: ssh -N -L 8002 ... started by backend\deploy\restore-cloud-lip.sh.
REM Nothing else on this machine forwards 8002, so matching the forward is enough.
set "TUN="
for /f "usebackq delims=" %%A in (`powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'ssh.exe' -and $_.CommandLine -like '*-L 8002*' } | ForEach-Object ProcessId"`) do (
    echo   lip tunnel -^> killing PID %%A
    taskkill /F /PID %%A >nul 2>nul
    set "TUN=1"
)
if not defined TUN echo   lip tunnel - not running

echo.
echo ports still listening:
netstat -ano | findstr /C:":8010 " /C:":5173 " /C:":4173 " /C:":5174 " | findstr /C:"LISTENING"
echo done.
echo.
pause
exit /b 0

:killport
set "P=%~1"
set "MK=%~2"
set "HIT="
for /f "tokens=5" %%A in ('netstat -ano ^| findstr /C:":%P% " ^| findstr /C:"LISTENING"') do call :trykill %P% %%A "%MK%"
if not defined HIT echo   port %P% - nothing of this project here
exit /b 0

:trykill
set "PP=%~1"
set "TPID=%~2"
set "MK=%~3"
set "MR="
for /f "usebackq delims=" %%R in (`powershell -NoProfile -Command "if ((Get-CimInstance Win32_Process -Filter 'ProcessId=%TPID%').CommandLine -like '*%MK%*') { 'MATCH' } else { 'NOMATCH' }"`) do set "MR=%%R"
if /I "!MR!"=="MATCH" (
    echo   port %PP% -^> killing PID %TPID%
    REM /T kills the whole tree: npm wrapper plus its vite node child
    taskkill /F /T /PID %TPID% >nul 2>nul
    set "HIT=1"
    exit /b 0
)
if not defined MR (
    echo   port %PP% - PID %TPID% unreadable, left alone
) else (
    echo   port %PP% - PID %TPID% is NOT this project, left alone
)
exit /b 0
