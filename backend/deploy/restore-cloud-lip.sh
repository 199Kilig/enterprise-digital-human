#!/bin/bash
# ============================================================================
# 云 GPU 口型服务 + 本机隧道 一键恢复（AutoDL 实例开机后执行）
# ----------------------------------------------------------------------------
# 为什么要有脚本：实例重启后有三样东西会同时消失/变化，手工做必漏一样——
#   ① 云上 lip_service 进程（GPU 上的服务不会自己回来）
#   ② 本机 SSH 隧道进程
#   ③ AutoDL **会重新分配 SSH 端口**（关机再开机后端口号可能变，旧端口连不通）
# 踩过的坑（RUNBOOK §3.12/§3.13）：云上是**旧版** lip_service.py（只回逐帧 JPEG，
# 前端不渲染），照着文档以为"服务起来了"，结果嘴不动。所以本脚本第 2 步强制核对 md5。
#
# 用法：
#   bash restore-cloud-lip.sh <ssh端口> [主机]
#   bash restore-cloud-lip.sh 47618                    # 主机默认 connect.nmb1.seetacloud.com
#
# 退出码：
#   0 全绿 | 1 实例不可达（去控制台开机/换端口） | 2 代码同步失败 | 3 云服务未就绪 | 4 隧道未通
# ============================================================================
set -u
PORT="${1:-47618}"
HOST="${2:-connect.nmb1.seetacloud.com}"
REMOTE_DIR=/root/autodl-tmp/digital-human/MuseTalk
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
LOCAL_DEPLOY="$REPO/backend/deploy"
SSH_OPTS=(-o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15)

say() { printf "\n[%s] %s\n" "$1" "$2"; }

say 1 "探测实例 SSH（$HOST:$PORT）"
if ! timeout 25 ssh "${SSH_OPTS[@]}" -p "$PORT" root@"$HOST" "echo OK" >/dev/null 2>&1; then
  echo "  ✗ SSH 不可达。"
  echo "    症状对照：'Connection reset by peer' 或 'timed out during banner exchange' = TCP 通、"
  echo "    SSH 服务端无响应 → AutoDL 实例已关机，或开机后端口被重新分配（端口不固定）。"
  echo "    → 到 AutoDL 控制台确认实例状态；开机后复制新的连接串，重跑："
  echo "        bash restore-cloud-lip.sh <新端口>"
  exit 1
fi
echo "  ✓ SSH 可达"

say 2 "校验云上代码版本（防止跑到旧版：旧版只回逐帧 JPEG，前端不渲染）"
for f in lip_service.py lip_encode.py; do
  l=$(md5sum "$LOCAL_DEPLOY/$f" | cut -d' ' -f1)
  r=$(timeout 25 ssh "${SSH_OPTS[@]}" -p "$PORT" root@"$HOST" "md5sum $REMOTE_DIR/$f 2>/dev/null | cut -d' ' -f1" 2>/dev/null || true)
  if [ "$l" != "$r" ]; then
    echo "  → $f 不一致（本地 $l / 云上 ${r:-缺失}），上传"
    timeout 120 scp -o BatchMode=yes -o StrictHostKeyChecking=accept-new -P "$PORT" \
      "$LOCAL_DEPLOY/$f" root@"$HOST":"$REMOTE_DIR/$f" >/dev/null || { echo "  ✗ scp 失败"; exit 2; }
    r2=$(timeout 25 ssh "${SSH_OPTS[@]}" -p "$PORT" root@"$HOST" "md5sum $REMOTE_DIR/$f | cut -d' ' -f1" 2>/dev/null || true)
    [ "$l" = "$r2" ] || { echo "  ✗ 上传后校验仍不一致"; exit 2; }
  fi
  echo "  ✓ $f 一致"
done

say 3 "启动云侧口型服务（模型加载约 21-36s）"
# 判据必须是「服务能不能用」，不能是「进程存不存在」：
#   ① pgrep -f 'uvicorn lip_service' 会匹配到执行这条命令的 bash **自身**（那条命令行里
#      就含这个字符串）→ 永远返回"已存在"→ 服务永远起不来（2026-09-22 实测，见 RUNBOOK 坑 25）
#   ② 这台机器的 ss/netstat 报不出 8002（实测输出为空），端口监听也不能作判据
# 于是只剩 health 探测可靠。
timeout 90 ssh "${SSH_OPTS[@]}" -p "$PORT" root@"$HOST" \
  "curl -s -m 5 http://127.0.0.1:8002/health | grep -q 'status.:.ok' && echo '已就绪，跳过启动' || { cd $REMOTE_DIR && OMP_NUM_THREADS=16 setsid nohup /root/miniconda3/bin/python -m uvicorn lip_service:app --host 0.0.0.0 --port 8002 > /root/lip_server.log 2>&1 < /dev/null & echo '已拉起，等待加载'; }" 2>&1 | tail -1
# 轮询等待就绪（先探再等：服务已在跑时零等待；冷启动最多 60s）
H=""
for _ in $(seq 1 12); do
  H=$(timeout 15 ssh "${SSH_OPTS[@]}" -p "$PORT" root@"$HOST" "curl -s -m 5 http://127.0.0.1:8002/health" 2>/dev/null || true)
  echo "$H" | grep -q 'status.:.ok' && break
  sleep 5
done
echo "  云侧 health: ${H:-（无响应）}"
echo "$H" | grep -q '"status":"ok"' || { echo "  ✗ 云服务未就绪 → ssh 上去看 /root/lip_server.log"; exit 3; }

say 4 "建本机隧道（本机 8002 → 云 8002）"
# 判据同样用 health 而不是 pgrep：本机 8002 能应答就说明隧道（或直连）已就位
if curl -s -m 4 http://127.0.0.1:8002/health 2>/dev/null | grep -q 'status.:.ok'; then
  echo "  本机 8002 已可用，跳过建隧道"
else
  nohup ssh -N -L 8002:127.0.0.1:8002 -p "$PORT" root@"$HOST" -o BatchMode=yes \
    -o StrictHostKeyChecking=accept-new -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
    >/dev/null 2>&1 &
  sleep 5
fi
T=$(curl -s -m 6 http://127.0.0.1:8002/health 2>/dev/null || true)
echo "$T" | grep -q '"status":"ok"' || { echo "  ✗ 隧道未通（检查端口转发是否被 ExitOnForwardFailure 拒绝）"; exit 4; }
echo "  ✓ 隧道通"

say OK "就绪：云服务 + 隧道都在。下面应显示 lip_service.reachable=true"
curl -s -m 6 http://127.0.0.1:8010/api/v1/health 2>/dev/null | head -c 220 || echo "（本机后端未启动：先跑 start.bat 或起 uvicorn :8010）"
echo
