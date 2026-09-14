"""定位「后端调口型服务 2s vs 直连 6ms」的差异。

四个组合逐一计时，把变量拆开：
  A. requests + 127.0.0.1
  B. urllib  + 127.0.0.1
  C. requests + localhost
  D. urllib  + localhost     ← 后端实际用的组合
"""
import base64
import json
import time
import urllib.request

import numpy as np
import requests

pcm = (np.random.randn(16000) * 3000).astype("<i2").tobytes()   # 1.0s @16k
body = json.dumps({"audio_pcm_16k_b64": base64.b64encode(pcm).decode(),
                   "fps": 25, "transport": "h264"}).encode()


def via_requests(host: str) -> float:
    t = time.perf_counter()
    r = requests.post(f"http://{host}:8002/api/v1/lip/infer", json=json.loads(body), timeout=120)
    r.raise_for_status()
    return (time.perf_counter() - t) * 1000


def via_urllib(host: str) -> float:
    t = time.perf_counter()
    req = urllib.request.Request(f"http://{host}:8002/api/v1/lip/infer", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
        resp.read()
    return (time.perf_counter() - t) * 1000


for label, fn, host in [
    ("A requests + 127.0.0.1", via_requests, "127.0.0.1"),
    ("B urllib   + 127.0.0.1", via_urllib, "127.0.0.1"),
    ("C requests + localhost", via_requests, "localhost"),
    ("D urllib   + localhost", via_urllib, "localhost"),
]:
    times = [fn(host) for _ in range(3)]
    print(f"{label}: " + "  ".join(f"{x:7.1f}ms" for x in times))

print("\n对照：若 B 快而 D 慢 → 病根是 localhost 的解析/连接（IPv6 回退或 DNS）。")
