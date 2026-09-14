@echo off
REM ============================================================
REM  启动本机 -> 云 GPU 口型服务的 SSH 隧道
REM  用途：换网络/重启机器后，重建到云口型服务（8002）的通道
REM  前置：云侧 lip 服务已在跑（见 docs/RUNBOOK-环境记录.md 3.10）
REM  说明：SSH 通道实测约 1MB/s，是当前链路硬限速（RUNBOOK 坑 15）
REM ============================================================
setlocal
set HOST=connect.nmb1.seetacloud.com
set PORT=47618
set USER=root
set LIP_PORT=8002

echo [1/3] 清理可能存在的旧隧道进程...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8002 " ^| findstr LISTENING') do (
    echo       结束占用 8002 的进程 PID=%%p
    taskkill /F /PID %%p >nul 2>&1
)

echo [2/3] 建立隧道 %LIP_PORT% -^> %HOST%:%LIP_PORT% ...
start "lip-tunnel" /min ssh -N -L %LIP_PORT%:127.0.0.1:%LIP_PORT% -p %PORT% %USER%@%HOST% -o StrictHostKeyChecking=accept-new -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3

echo [3/3] 等待并自检...
timeout /t 6 /nobreak >nul
curl -s --max-time 10 http://127.0.0.1:%LIP_PORT%/health
echo.
echo 若上面显示 status:ok 即为成功；无输出说明云侧服务未启动或隧道失败。
echo 关闭隧道：直接关掉那个最小化的 ssh 窗口，或 taskkill 掉 8002 的监听进程。
endlocal
pause
