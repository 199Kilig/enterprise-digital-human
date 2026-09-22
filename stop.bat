@echo off
REM ===========================================================================
REM  Enterprise Digital Human - stop dev services (backend :8010, frontend :5173)
REM  Kills whatever is LISTENING on those ports (the real owner process),
REM  not the npm wrapper - npm kill alone leaves the vite child alive.
REM  ASCII only on purpose (see start.bat header).
REM ===========================================================================
setlocal EnableExtensions
title Digital Human - stop

echo ============================================================
echo   stopping dev services
echo ============================================================
echo.

REM 4173 is vite preview (used to check the built bundle); 5174 would only appear
REM if strictPort were ever relaxed. Killing them keeps later "which UI am I looking
REM at" confusion off the table.
for %%P in (8010 5173 4173 5174) do call :killport %%P

echo.
echo ports still listening:
netstat -ano | findstr /C:":8010 " | findstr /C:"LISTENING"
netstat -ano | findstr /C:":5173 " | findstr /C:"LISTENING"
netstat -ano | findstr /C:":4173 " | findstr /C:"LISTENING"
netstat -ano | findstr /C:":5174 " | findstr /C:"LISTENING"
echo done.
echo.
pause
exit /b 0

:killport
set "P=%~1"
set "FOUND="
for /f "tokens=5" %%A in ('netstat -ano ^| findstr /C:":%P% " ^| findstr /C:"LISTENING"') do (
    if not "%%A"=="0" (
        echo   port %P% -^> killing PID %%A
        taskkill /F /PID %%A >nul 2>nul
        set "FOUND=1"
    )
)
if not defined FOUND echo   port %P% - not in use
exit /b 0
