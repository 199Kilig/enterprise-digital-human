"""V-06 端到端延迟验证（PRD §3 验收标准：用户说完 → 数字人开口 < 2s，首包口径）

测什么：真实走一遍 LLM → TTS 链路，按事件到达时刻打点，
产出「首次出声」以及各段分解（LLM TTFT / LLM 完成 / 首句送合成 / TTS 首包）。

为什么要有它：
- PRD FR-07 要求全链路每段计时、可回放；FR-08 要求量化指标有脚本背书
- 流水线是否真重叠，必须用「首句送合成时刻 < LLM 完成时刻」来证明，
  不能只看"首次出声"（TTS 首包本身就要几百毫秒，短回答下永远晚于 LLM 完成）

口径说明（写进报告，避免误读）：
- 本轮为**文本输入**，故不含 ASR 延迟；接入语音输入后需加上 ASR 首字（V-03 实测 ≈766ms）
- "开口"= 首个 tts_audio 事件到达前端时刻（DESIGN §5.1 端到端口径已统一为"首包"）

用法:
  python verify_e2e_latency.py [--base-url http://127.0.0.1:8010] [--runs 5] [--report-dir reports]
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_QUESTIONS = [
    "你们家运费怎么算？",
    "退货要几天到账？",
    "我买的衣服不合适，想换尺码怎么操作？",
    "订单一直没发货，能帮我查一下吗？",
    "发票怎么开？",
]


def _post(url: str) -> dict:
    with urllib.request.urlopen(urllib.request.Request(url, method="POST"), timeout=10) as r:
        return json.load(r)


def run_turn(base: str, session_id: str, text: str) -> dict:
    q = urllib.parse.urlencode({"session_id": session_id, "text": text})
    t0 = time.perf_counter()
    marks: dict[str, float | None] = {"first_token": None, "last_token": None, "first_audio": None, "done": None}
    tokens = 0
    audio_bytes = 0
    answer = ""
    done: dict = {}

    with urllib.request.urlopen(f"{base}/api/v1/chat/stream?{q}", timeout=120) as resp:
        event = None
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                try:
                    d = json.loads(line[5:])
                except json.JSONDecodeError:
                    continue
                now = time.perf_counter() - t0
                if event == "brain_token":
                    if marks["first_token"] is None:
                        marks["first_token"] = now
                    marks["last_token"] = now
                    tokens += 1
                    answer += d.get("token", "")
                elif event == "tts_audio":
                    if d.get("audio_b64"):
                        if marks["first_audio"] is None:
                            marks["first_audio"] = now
                        audio_bytes += len(d["audio_b64"]) * 3 // 4
                elif event == "done":
                    marks["done"] = now
                    done = d

    return {
        "question": text,
        "tokens": tokens,
        "answer_chars": len(answer),
        "audio_s": round(audio_bytes / 32000, 2),
        "ttft_ms": done.get("ttft_ms"),
        "llm_done_ms": done.get("llm_done_ms"),
        "tts_start_ms": done.get("tts_start_ms"),
        "first_audio_ms": done.get("first_audio_ms"),
        "first_audio_wall_ms": int(marks["first_audio"] * 1000) if marks["first_audio"] else None,
        "turn_done_ms": int(marks["done"] * 1000) if marks["done"] else None,
        "tts_first_packet_ms": done.get("tts_first_packet_ms"),
        "tts_total_ms": done.get("tts_total_ms"),
        "tts_words": done.get("tts_words"),
        "state_after": done.get("state_after"),
        "overlapped": done.get("overlapped"),
    }


def _agg(vals: list[float | None]) -> dict:
    xs = [v for v in vals if isinstance(v, (int, float))]
    if not xs:
        return {"n": 0}
    return {
        "n": len(xs),
        "mean": round(statistics.mean(xs), 1),
        "median": round(statistics.median(xs), 1),
        "min": round(min(xs), 1),
        "max": round(max(xs), 1),
        "stdev": round(statistics.pstdev(xs), 1) if len(xs) > 1 else 0.0,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="V-06 端到端延迟验证")
    ap.add_argument("--base-url", default="http://127.0.0.1:8010")
    ap.add_argument("--runs", type=int, default=5, help="问题轮数（循环使用内置问题集）")
    ap.add_argument("--report-dir", default="reports")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    session_id = _post(f"{base}/api/v1/session")["session_id"]
    questions = [DEFAULT_QUESTIONS[i % len(DEFAULT_QUESTIONS)] for i in range(args.runs)]

    rows = []
    for i, q in enumerate(questions, 1):
        r = run_turn(base, session_id, q)
        rows.append(r)
        print(
            f"[{i}/{len(questions)}] TTFT {r['ttft_ms']}ms | LLM完成 {r['llm_done_ms']}ms | "
            f"首句送合成 {r['tts_start_ms']}ms | 首次出声 {r['first_audio_ms']}ms | "
            f"重叠 {r['overlapped']} | {q[:16]}"
        )

    agg = {
        "ttft_ms": _agg([r["ttft_ms"] for r in rows]),
        "llm_done_ms": _agg([r["llm_done_ms"] for r in rows]),
        "tts_start_ms": _agg([r["tts_start_ms"] for r in rows]),
        "first_audio_ms": _agg([r["first_audio_ms"] for r in rows]),
        "tts_first_packet_ms": _agg([r["tts_first_packet_ms"] for r in rows]),
        "turn_done_ms": _agg([r["turn_done_ms"] for r in rows]),
    }
    overlap_rate = round(sum(1 for r in rows if r["overlapped"]) / len(rows), 3)

    target_ms = 2000  # PRD §3：端到端（用户说完 → 首包）< 2s
    checks = {
        "first_audio_mean_lt_2s": agg["first_audio_ms"].get("mean", 1e9) < target_ms,
        "pipeline_overlap": overlap_rate == 1.0,
    }
    report = {
        "date": time.strftime("%Y-%m-%d"),
        "verifier": "V-06",
        "scope": "文本输入 → LLM 流式 → TTS 流式 → 首包（不含 ASR；不含口型）",
        "env": "本机（Windows），后端 uvicorn:8010",
        "questions": questions,
        "runs": rows,
        "aggregates": agg,
        "overlap_rate": overlap_rate,
        "checks": checks,
        "target_ms": target_ms,
        "notes": [
            "首次出声 = 用户提交 → 首个 tts_audio 事件；PRD 的'数字人开口'按 DESIGN §5.1 统一为首包口径",
            "不含 ASR：语音输入接入后需叠加 ASR 首字（V-03 实测 ≈766ms），届时需重新评估是否守住 2s",
            "overlapped = 首句送合成时刻 < LLM 完成时刻；这是流水线并行（DESIGN §3.1）的直接证据",
        ],
        "status": "PASSED" if all(checks.values()) else "FAILED",
    }

    out_dir = Path(args.report_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "e2e_latency.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    fa = agg["first_audio_ms"]
    print(f"首次出声   mean {fa['mean']}ms | median {fa['median']}ms | min {fa['min']} | max {fa['max']} | stdev {fa['stdev']}")
    print(f"LLM TTFT   mean {agg['ttft_ms']['mean']}ms")
    print(f"流水线重叠 {overlap_rate * 100:.0f}%（{sum(1 for r in rows if r['overlapped'])}/{len(rows)}）")
    print(f"目标 <{target_ms}ms → {'达标' if report['checks']['first_audio_mean_lt_2s'] else '未达标'}")
    print(f"报告已写入: {out.resolve()}")
    return 0 if report["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
