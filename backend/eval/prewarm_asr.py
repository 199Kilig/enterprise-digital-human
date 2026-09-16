"""ASR 模型预热：建会话 + 打一片 600ms 静音，触发 FunASR 懒加载（首次 ~22s）。

为什么需要（RUNBOOK 坑 8）：`asr/streaming.py` 的模型是**进程内单例懒加载**，
第一条真实语音会额外等模型加载 ~22s；前端表现为"点完说话卡很久"，且若单块识别超过
`ASR_TIMEOUT_S`(3s) 会直接返回 ASR_TIMEOUT。演示/真人实测前先跑本脚本。

用法：
    python backend/eval/prewarm_asr.py                # 默认 http://127.0.0.1:8010
    python backend/eval/prewarm_asr.py --probe-only   # 只探测健康与预热状态，不发送音频

注意：URL 必须写 `127.0.0.1`（RUNBOOK 坑 20：本机 `localhost` 解析约 2s/次）。
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import urllib.error
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

SR = 16000
WARM_OK_MS = 1000  # 预热后单片耗时上限（实测稳态 ~250ms）


def _post(url: str, payload: dict, timeout: float = 180.0) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
        return json.loads(r.read().decode("utf-8"))


def _get(url: str, timeout: float = 10.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8010")
    ap.add_argument("--ms", type=int, default=600, help="预热音频时长（须 ≥600ms 才会送模型）")
    ap.add_argument("--probe-only", action="store_true")
    a = ap.parse_args()
    base = a.url.rstrip("/")

    try:
        health = _get(base + "/api/v1/health")
    except (urllib.error.URLError, OSError) as exc:
        print(f"[X] 后端不可达：{base}/api/v1/health -> {exc}")
        print("    先运行 start.bat（或手动起 uvicorn）")
        return 1
    print(f"health: {json.dumps(health, ensure_ascii=False)[:160]}")

    if a.probe_only:
        print("--probe-only：只探测，未发送音频")
        return 0

    sess = _post(base + "/api/v1/session", {})
    sid = sess["session_id"]
    pcm = base64.b64encode(b"\x00\x00" * int(SR * a.ms / 1000)).decode("ascii")

    t0 = time.perf_counter()
    r1 = _post(base + "/api/v1/asr/chunk",
               {"session_id": sid, "audio_pcm_16k_b64": pcm, "seq": 0, "end": True})
    d1 = (time.perf_counter() - t0) * 1000
    print(f"第 1 片（含模型加载）: {d1:.0f} ms -> is_final={r1.get('is_final')}")

    t0 = time.perf_counter()
    _post(base + "/api/v1/asr/chunk",
          {"session_id": sid, "audio_pcm_16k_b64": pcm, "seq": 0, "end": True})
    d2 = (time.perf_counter() - t0) * 1000

    ok = d2 < WARM_OK_MS
    print(f"第 2 片（预热后）    : {d2:.0f} ms  -> {'OK，可以开始真人实测' if ok else '仍偏慢，检查 CPU 占用'}")
    if not ok:
        print(f"[!] 预热后单片 {d2:.0f}ms 超过 {WARM_OK_MS}ms：若单块 >3000ms 会触发 ASR_TIMEOUT")
        return 2
    print(f"\n提示：语音实测期间别重启后端进程——重启会丢掉已加载的模型。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
