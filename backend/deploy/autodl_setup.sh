#!/usr/bin/env bash
# AutoDL 无卡模式环境配置脚本（2026-09-02）
# 用法：在实例终端执行  bash autodl_setup.sh
# 说明：无卡模式下完成 MuseTalk clone/依赖/权重下载；权重放数据盘 /root/autodl-tmp（持久），
#       系统盘 30G 不够。配置完成后切有卡模式跑 V-01。
set -euo pipefail

WORK=/root/autodl-tmp/digital-human
echo "==> 工作目录: $WORK（数据盘，持久）"
mkdir -p "$WORK" && cd "$WORK"

# 1. clone MuseTalk
if [ ! -d MuseTalk ]; then
  echo "==> clone MuseTalk"
  git clone https://github.com/TMElyralab/MuseTalk.git
fi
cd MuseTalk

# 2. 依赖（清华源加速）
echo "==> 安装依赖（约 5-10 分钟）"
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
pip install modelscope -i https://pypi.tuna.tsinghua.edu.cn/simple

# 3. 权重下载（无需 GPU，无卡模式完成）
#    结构（官方 README#download-weights）：
#      models/musetalk/{musetalk.json, pytorch_model.bin}
#      models/dwpose/dw-ll_ucoco_384.pth
#      models/face-parse-bisent/...
#      models/whisper/...
#    官方仓库提供 download_weights.sh / HF git-lfs；国内优先 ModelScope（DESIGN §5.4 坑2）
echo "==> 下载权重（体积约 8-10G，视网络 10-30 分钟）"
if [ ! -f models/musetalk/pytorch_model.bin ]; then
  # 方式 A：官方脚本（HF 源）
  # bash scripts/download_weights.sh
  # 方式 B：ModelScope（国内快，若仓库存在）
  python - <<'EOF'
from modelscope import snapshot_download
import os
cands = ["TMElyralab/MuseTalk"]
for repo in cands:
    try:
        p = snapshot_download(repo, local_dir="./models")
        print("OK:", p)
        break
    except Exception as e:
        print("跳过", repo, ":", e)
EOF
fi
ls -lh models/musetalk/ 2>/dev/null || echo "!! 权重未下载成功：请按官方 README#download-weights 手动处理"

# 4. 自检
echo "==> 自检"
python -c "import torch; print('torch', torch.__version__)"   # 无卡模式 cuda.is_available()=False 属正常
python -c "import funasr; print('funasr', funasr.__version__)" || echo "funasr 未装（V-03 用，可选）"

echo "==> 环境配置完成，可切有卡模式跑 V-01"
