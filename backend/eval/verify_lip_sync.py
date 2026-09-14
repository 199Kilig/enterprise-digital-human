"""量音画偏差：口型片段到达时刻 vs 它应该开始播的时刻（SPEC §3.5 2b / ADR-005）。

口径（与前端 useLipVideo 完全一致）：
  音频时钟 t=0 定义为本轮**首个 tts_audio 分片被排入播放队列**的时刻；
  片段 #k 应在 音频时钟 = start_ms 时开播；
  偏差 lateMs = 片段到达时的音频时钟 − start_ms（正数=迟到，画面落后于声音）。

为什么必须量而不是估：
  这个偏差由「整句音频合成完 → 才送口型推理」的架构决定，与句子长度、TTS 语速、
  口型生成耗时都相关。用真实数字才能判断是「固定 2s」（可补偿）还是「随时间累积」（不可补偿）。
"""
from __future__ import annotations

import json
import sys
import time

import requests

# 中文 Windows 控制台默认 GBK：本脚本输出含 ✅/⚠️，直接 print 会抛 UnicodeEncodeError
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

BASE = "http://127.0.0.1:8010/api/v1"
QUESTION = sys.argv[1] if len(sys.argv) > 1 else "你们家运费怎么算，下单后多久能发货"


def main() -> int:
    s = requests.post(f"{BASE}/session", timeout=30).json()
    sid = s["session_id"]
    print(f"session={sid}\n提问: {QUESTION!r}\n")

    t0 = time.time()
    audio_t0: float | None = None      # 首个有音频的 tts_audio 到达时刻（音频时钟原点）
    rows: list[dict] = []
    last_audio_ms = 0.0
    last_audio_at = 0.0
    audio_marks: list[tuple[float, float]] = []   # (墙钟秒, 音频内容位置ms)

    with requests.get(f"{BASE}/chat/stream",
                      params={"session_id": sid, "text": QUESTION},
                      stream=True, timeout=600) as r:
        event = None
        for raw in r.iter_lines(decode_unicode=True):
            if raw is None:
                continue
            if raw.startswith("event: "):
                event = raw[7:].strip()
                continue
            if not raw.startswith("data: "):
                continue
            p = json.loads(raw[6:])
            now = time.time() - t0

            if event == "tts_audio" and p.get("audio_b64"):
                if audio_t0 is None:
                    audio_t0 = now
                end = float(p.get("end_ms") or 0)
                if end > last_audio_ms:
                    last_audio_ms = end
                    last_audio_at = now
                    audio_marks.append((now - audio_t0, end))
            elif event == "lip_video":
                if audio_t0 is None:
                    continue
                clock = (now - audio_t0) * 1000.0     # 片段到达时的音频时钟
                start_ms = float(p.get("start_ms") or 0)
                rows.append({
                    "seq": p.get("seq"),
                    "start_ms": start_ms,
                    "arrive_ms": round(now * 1000),
                    "arrive_clock_ms": round(clock),
                    "late_ms": round(clock - start_ms),
                    "duration_ms": p.get("duration_ms"),
                    "n_frames": p.get("n_frames"),
                })

    if not rows:
        print("❌ 没收到 lip_video（口型服务未接线？）")
        return 1

    # 先分清瓶颈在 TTS 还是口型：TTS 合成速率 <1 表示"音频还没生成出来"
    print(f"整句音频总时长 ≈ {last_audio_ms:.0f}ms（音频时钟终点）")
    if audio_marks:
        rate = last_audio_ms / (last_audio_at * 1000.0) if last_audio_at else 0
        print(f"TTS 合成速率 ≈ {rate:.2f}× 实时（{last_audio_ms:.0f}ms 音频用了 {last_audio_at * 1000:.0f}ms）"
              f"{'  ← ⚠️ 慢于实时，口型必然追不上' if rate < 1.0 else '  ← 快于实时，口型有提前量'}")
        if len(audio_marks) >= 4:
            seg = audio_marks[len(audio_marks) // 2 :]
            d_clock = seg[-1][0] - seg[0][0]
            d_audio = (seg[-1][1] - seg[0][1]) / 1000.0
            if d_clock > 0:
                print(f"  后半段实测：{d_clock:.1f}s 墙钟产出 {d_audio:.1f}s 音频 → {d_audio / d_clock:.2f}× 实时")
    print()
    print(f"{'片段':>4} {'应在(音频时钟)':>14} {'实际到达':>10} {'迟到':>8} {'时长':>8} {'帧数':>6}")
    for r_ in rows:
        print(f"{r_['seq']:>4} {r_['start_ms']:>14.0f} {r_['arrive_clock_ms']:>10} "
              f"{r_['late_ms']:>7}ms {r_['duration_ms'] or 0:>7} {r_['n_frames'] or 0:>6}")

    lates = [r_["late_ms"] for r_ in rows]
    print(f"\n迟到：均值 {sum(lates) / len(lates):.0f}ms  最小 {min(lates)}ms  最大 {max(lates)}ms")
    # 口径澄清：迟到为**负**=片段比应播时刻更早到，会被前端按 start_ms 排队等到点播，属于正常且理想
    #           迟到为**正**=片段来得太晚，声音已经播过去了，这才是"嘴跟不上声音"。
    if len(lates) >= 2:
        rest = lates[1:]
        late_pos = [x for x in rest if x > 0]
        print(f"  首片段 {lates[0]}ms（结构性：要等第一片的音频生成出来）")
        print(f"  后续片段：提前 {len([x for x in rest if x <= 0])}/{len(rest)} 片，"
              f"晚到 {len(late_pos)}/{len(rest)} 片"
              + (f"，晚到均值 {sum(late_pos) / len(late_pos):.0f}ms 最大 {max(late_pos)}ms" if late_pos else ""))
        if not late_pos:
            print("  → ✅ 后续片段全部提前到达 → 前端按 start_ms 排队即同步（口型不落后于声音）")
        elif max(late_pos) > 1500:
            print("  ⚠️ 有片段晚到 >1.5s：生成/传输跟不上播放，需降分片粒度或提速")
        elif max(late_pos) > 500:
            print("  ⚠️ 有片段晚到 0.5~1.5s：轻微可辨，可接受但仍有优化空间")
        else:
            print("  → 晚到片段均在 500ms 内（人眼可辨阈值以下）")
    print("\n判据：正值迟到 > 500ms 即人眼可辨；负值=提前，不算问题")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
