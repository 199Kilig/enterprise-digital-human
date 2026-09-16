"""打断端到端验证（FR-06）：真实 SSE 流播报中途插话，断言流立即停止且**不发 done**。

与 `tests/test_interrupt_path.py` 的分工：
  - 测试文件：验状态机与 `stop_requested` 标志（无外部依赖，秒级）
  - 本脚本：验**真实链路**（真实 LLM + TTS），回答"用户插话后数字人是否真的闭嘴"

⚠️ 会真实调用 DeepSeek 与 DashScope（消耗额度），与 `verify_e2e_latency.py` 同性质。

判据（对齐 DESIGN-打断 §5.1：打断信号传播 < 200ms）：
  1. `/interrupt` 之后**不再收到新的 tts_audio**（允许 ≤1 条在途竞争）—— 后端停止产出
  2. **不收到 done**（被打断的一轮不发 done，状态由 /interrupt_done 收尾）
  3. 流在 interrupt 后 **< 2000ms** 内结束

用法：
    python backend/eval/verify_interrupt_e2e.py                    # 默认 127.0.0.1:8010
    python backend/eval/verify_interrupt_e2e.py --question "你们家运费怎么算"\n
    先决条件：后端已起（start.bat）且已预热（prewarm_asr.py 不需要，本脚本走文本通道）。
"""
from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
import time
import urllib.parse
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

DEFAULT_Q = "你们的退货流程是怎么走的，能详细说说吗？"  # 需要长一些，才有多片 tts_audio 可打断


def _post(url: str, payload: dict, timeout: float = 30.0) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
        return json.loads(r.read().decode("utf-8"))


def _read_sse(url: str, out: "queue.Queue[tuple[float, str, str]]", timeout: float = 60.0) -> None:
    """后台线程读 SSE，把 (相对时刻, 事件名, data) 推进队列；结束时放哨兵。"""
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            event = ""
            for raw in r:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if line.startswith("event: "):
                    event = line[7:]
                elif line.startswith("data: "):
                    out.put(((time.perf_counter() - t0) * 1000, event, line[6:]))
    except Exception as exc:  # noqa: BLE001
        out.put(((time.perf_counter() - t0) * 1000, "__error__", str(exc)))
    finally:
        out.put(((time.perf_counter() - t0) * 1000, "__end__", ""))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8010")
    ap.add_argument("--question", default=DEFAULT_Q)
    ap.add_argument("--hard-timeout", type=float, default=40.0)
    a = ap.parse_args()
    base = a.url.rstrip("/")

    sess = _post(base + "/api/v1/session", {})
    sid = sess["session_id"]
    print(f"session: {sid[:8]}…  提问：{a.question}")

    q: "queue.Queue[tuple[float, str, str]]" = queue.Queue()
    url = f"{base}/api/v1/chat/stream?session_id={sid}&text={urllib.parse.quote(a.question)}"
    threading.Thread(target=_read_sse, args=(url, q), daemon=True).start()

    events: list[tuple[float, str]] = []
    interrupt_at: float | None = None
    first_audio_at: float | None = None
    deadline = time.perf_counter() + a.hard_timeout
    done_seen = False

    while time.perf_counter() < deadline:
        try:
            at_ms, etype, data = q.get(timeout=0.5)
        except queue.Empty:
            continue
        if etype == "__end__":
            events.append((at_ms, "__end__"))
            break
        if etype == "__error__":
            print(f"[X] SSE 读取失败：{data}")
            return 1
        events.append((at_ms, etype))
        if etype == "tts_audio" and first_audio_at is None:
            first_audio_at = at_ms
            # 第一片音频到达（= 数字人已开口）→ 立刻插话打断
            t_int = time.perf_counter()
            r = _post(f"{base}/api/v1/session/{sid}/interrupt", {})
            interrupt_at = (time.perf_counter() - t_int + at_ms)
            print(f"  在 {at_ms:.0f}ms（首片音频）触发打断 → /interrupt 返回 {r.get('status')}")
        if etype == "done":
            done_seen = True

    if interrupt_at is None:
        print("[X] 整轮结束前没有收到任何 tts_audio，无法验证打断（LLM/TTS 是否正常？）")
        return 1

    after = [(t, e) for t, e in events if t > interrupt_at]
    audio_after = [e for _, e in after if e == "tts_audio"]
    end_at = next((t for t, e in events if e == "__end__"), None)
    stop_ms = None if end_at is None else end_at - interrupt_at

    print(f"\n事件总数 {len(events)}；interrupt 之后收到 {len(after)} 条：{[e for _, e in after][:12]}")
    print(f"interrupt 后 tts_audio 条数 = {len(audio_after)}（≤1 为在途竞争）")
    print(f"是否收到 done = {done_seen}（被打断的一轮不应发 done）")
    print(f"打断 → 流结束 = {'超时未结束' if stop_ms is None else f'{stop_ms:.0f} ms'}")

    ok = len(audio_after) <= 1 and not done_seen and stop_ms is not None and stop_ms < 2000
    print("\n判定：" + ("PASS —— 打断真的让后端停止产出了" if ok else "FAIL —— 后端仍在继续产出，见上面各条"))
    if not ok:
        print("  排查：① routes.py 的 while 是否含 `if ctx.stop_requested: break`；"
              "② /interrupt 是否置位 ctx.stop_requested；③ 是否忘了重启后端（改后端必须重启）")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
