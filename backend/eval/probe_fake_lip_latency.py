"""直接测假口型服务的单次响应耗时（排除后端、排除 TTS）。

目的：1s 分片经"后端→假服务"往返到底几毫秒。
若首次数百 ms、后续几 ms → 缓存生效，2s 在别处；
若每次都 ~2s → 假服务自身就是瓶颈（编码未命中缓存）。
"""
import base64
import json
import time

import numpy as np
import requests

URL = "http://127.0.0.1:8002/api/v1/lip/infer"
pcm = (np.random.randn(16000) * 3000).astype("<i2").tobytes()  # 1.0s @16k
body = {"audio_pcm_16k_b64": base64.b64encode(pcm).decode(), "fps": 25, "transport": "h264"}

for i in range(4):
    t = time.perf_counter()
    r = requests.post(URL, json=body, timeout=120)
    dt = (time.perf_counter() - t) * 1000
    d = r.json()
    print(f"第{i + 1}次: {dt:8.1f}ms  n_frames={d['n_frames']}  "
          f"payload={len(d['video_b64']) * 3 / 4 / 1024:.0f}KB  wall_ms(服务自报)={d['wall_ms']}")

print("\n结论：若第 2 次起仍 ~2000ms，则假服务的 ffmpeg 截断未走缓存，瓶颈在此。")
