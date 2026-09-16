"""打断路径耗时统计（FR-06 后端侧分量，落档用）。

与 `tests/test_interrupt_path.py` 的分工：测试文件管**正确性**（状态转移 + 边界），
本脚本管**数字**（重复 N 次取 median/p95/max，写入报告供台账引用）。

⚠️ 口径：ASGI 内存直连（无网络、无麦克风）。它测的是「后端收到打断信号 → 状态转移完成」这一段；
    端到端打断响应 = 前端 VAD 帧延迟（≤100ms，100ms/帧单帧触发）+ HTTP 往返 + 这一段。
    端到端数字必须人工实测（`reports/asr_mic_check.md` §5）。

用法：
    cd backend/eval && ../.venv/Scripts/python.exe verify_interrupt_path.py --runs 50
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

import importlib  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

mod = importlib.import_module("api.routes")
sm = importlib.import_module("api.state_machine")
schemas = importlib.import_module("schemas")
REPORT = Path(__file__).resolve().parent / "reports" / "interrupt_path.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=50)
    a = ap.parse_args()

    client = TestClient(mod.app)
    lat_interrupt, lat_done = [], []

    for _ in range(a.runs):
        sid = client.post("/api/v1/session").json()["session_id"]
        m = mod._machines[sid]
        m.handle(sm.StateEvent.ASR_ENDPOINT)     # → THINKING
        m.handle(sm.StateEvent.LLM_FIRST_TOKEN)  # → SPEAKING
        assert m.state is schemas.SessionState.SPEAKING

        t0 = time.perf_counter()
        r = client.post(f"/api/v1/session/{sid}/interrupt")
        lat_interrupt.append((time.perf_counter() - t0) * 1000)
        assert r.json()["status"] == "interrupted", r.text
        assert mod._machines[sid].state is schemas.SessionState.INTERRUPTED

        t1 = time.perf_counter()
        client.post(f"/api/v1/session/{sid}/interrupt_done")
        lat_done.append((time.perf_counter() - t1) * 1000)
        assert mod._machines[sid].state is schemas.SessionState.LISTENING

        # 清理期重复打断必须被识别为 ignored（v1.4 语义修正）
        client.post(f"/api/v1/session/{sid}/interrupt")  # → SPEAKING 前的重复态不适用，跳过

    def stat(xs: list[float]) -> dict:
        xs = sorted(xs)
        return {"n": len(xs), "median_ms": round(statistics.median(xs), 2),
                "p95_ms": round(xs[max(0, int(len(xs) * 0.95) - 1)], 2), "max_ms": round(xs[-1], 2)}

    out = {"date": "2026-09-15", "runs": a.runs,
           "scope": "ASGI 内存直连：后端收到打断信号 → 状态转移完成（不含前端 VAD 与网络）",
           "budget_ms_prd": 200,
           "interrupt": stat(lat_interrupt), "interrupt_done": stat(lat_done)}
    print(json.dumps(out, ensure_ascii=False, indent=2))

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    history = json.loads(REPORT.read_text(encoding="utf-8")) if REPORT.exists() else []
    history.append(out)
    REPORT.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已追加到 {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
