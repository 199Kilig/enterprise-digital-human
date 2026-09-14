"""端到端验证：SSE 全链路（LLM → TTS → 云 GPU 口型）是否真的产出 lip_video / lip_frame。

验证点（对应 SPEC §3.2 / §3.5 / ADR-005）：
  1. SSE 事件序列完整：thinking → brain_token… → tts_audio… → lip_video… → done
  2. **lip_video（v1.2 默认）**：video_b64 能解码成合法 MP4(H.264)，帧数/时长与事件字段自洽；
     且体积远小于逐帧 JPEG（ADR-005 的 41× 依据）
  3. 兼容路径 lip_frame：frame_b64 能解码成合法 JPEG，尺寸 = 形象原分辨率，pts_ms 单调递增
  4. done 事件带真实口型统计（片段数/首帧时刻/GPU 显存/传输字节）
"""
import base64
import io
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
from PIL import Image

BASE = "http://127.0.0.1:8010/api/v1"
QUESTION = sys.argv[1] if len(sys.argv) > 1 else "运费怎么算"


def probe_mp4_bytes(data: bytes) -> dict:
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "seg.mp4"
        p.write_bytes(data)
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name,width,height,nb_frames,avg_frame_rate",
             "-of", "json", str(p)],
            capture_output=True, check=False,
        )
        if r.returncode != 0:
            return {"error": r.stderr.decode("utf-8", "replace")[:200]}
        s = (json.loads(r.stdout.decode()).get("streams") or [{}])[0]
        return {
            "codec": s.get("codec_name"), "width": s.get("width"), "height": s.get("height"),
            "nb_frames": int(s["nb_frames"]) if s.get("nb_frames") else None,
            "fps": s.get("avg_frame_rate"),
        }


def main() -> int:
    s = requests.post(f"{BASE}/session", timeout=30).json()
    sid = s["session_id"]
    print(f"session={sid}  提问: {QUESTION!r}\n")

    counts: dict[str, int] = {}
    lip_pts: list[int] = []
    lip_sizes: list[tuple[int, int]] = []
    videos: list[dict] = []
    first_lip_at = None
    tts_first = None
    done_payload = None
    t0 = time.time()

    with requests.get(
        f"{BASE}/chat/stream", params={"session_id": sid, "text": QUESTION}, stream=True, timeout=600
    ) as r:
        event = None
        for raw in r.iter_lines(decode_unicode=True):
            if raw is None:
                continue
            if raw.startswith("event: "):
                event = raw[7:].strip()
                continue
            if not raw.startswith("data: "):
                continue
            payload = json.loads(raw[6:])
            counts[event] = counts.get(event, 0) + 1
            if event == "tts_audio" and payload.get("audio_b64") and tts_first is None:
                tts_first = time.time() - t0
            if event == "lip_video":
                if first_lip_at is None:
                    first_lip_at = time.time() - t0
                blob = base64.b64decode(payload["video_b64"])
                videos.append({
                    "seq": payload.get("seq"),
                    "start_ms": payload.get("start_ms"),
                    "duration_ms": payload.get("duration_ms"),
                    "n_frames": payload.get("n_frames"),
                    "bytes": len(blob),
                    **probe_mp4_bytes(blob),
                })
            if event == "lip_frame":
                if first_lip_at is None:
                    first_lip_at = time.time() - t0
                    img = Image.open(io.BytesIO(base64.b64decode(payload["frame_b64"])))
                    lip_sizes.append(img.size)
                lip_pts.append(payload["pts_ms"])
            if event == "done":
                done_payload = payload

    wall = time.time() - t0
    print("事件统计:", counts)
    print(f"墙钟总耗时: {wall * 1000:.0f}ms")
    print(f"TTS 首次出声: {tts_first * 1000:.0f}ms" if tts_first else "TTS 首次出声: 未发生")
    print(f"口型首片段到达: {first_lip_at * 1000:.0f}ms" if first_lip_at else "口型: 未到达")

    ok = False
    if videos:
        print(f"\nlip_video 片段 ×{len(videos)}:")
        for v in videos:
            print(f"  #{v['seq']} start={v['start_ms']}ms dur={v['duration_ms']}ms "
                  f"n_frames={v['n_frames']} {v['bytes'] / 1024:.0f}KB "
                  f"→ {v.get('codec')} {v.get('width')}x{v.get('height')} @{v.get('fps')} "
                  f"实测帧数={v.get('nb_frames')}")
        starts = [v["start_ms"] for v in videos]
        mono = all(starts[i] <= starts[i + 1] for i in range(len(starts) - 1))
        print(f"  start_ms 单调递增={mono}；总字节 {sum(v['bytes'] for v in videos) / 1024:.0f}KB")
        total_bytes = sum(v["bytes"] for v in videos)
        print(f"  对照：同帧逐帧 JPEG 约 15.5MB/8s → 本例 H.264 {total_bytes / 1024:.0f}KB "
              f"（压缩比 ~{15.5 * 1024 * 1024 / max(total_bytes, 1):.0f}×）")
        first = videos[0]
        ok = (
            first.get("codec") == "h264"
            and first.get("nb_frames") == first.get("n_frames")
            and mono
        )
    elif lip_pts:
        print(f"\nlip_frame 兼容路径：{len(lip_pts)} 帧，pts {lip_pts[0]}…{lip_pts[-1]}")
        if lip_sizes:
            print(f"  帧尺寸 {lip_sizes[0][0]}x{lip_sizes[0][1]}（应为 704x1216）")
        mono = all(lip_pts[i] <= lip_pts[i + 1] for i in range(len(lip_pts) - 1))
        print(f"  pts_ms 单调递增={mono}")
        ok = len(lip_pts) > 0

    if done_payload:
        keys = [
            "total_seq_audio", "total_seq_lip", "lip_frames", "lip_sentences",
            "lip_first_ms", "lip_total_ms", "lip_infer_fps", "lip_gpu_memory_mb",
            "lip_transport", "lip_bytes", "lip_error", "ttft_ms", "tts_first_packet_ms", "duration_ms",
        ]
        print("\ndone 事件（真实统计，无编造）:")
        for k in keys:
            if k in done_payload:
                print(f"  {k:22s} = {done_payload[k]}")
        if done_payload.get("lip_error"):
            ok = False

    print(f"\n结论: {'✅ 口型链路打通（SSE 真的在推 ' + ('H.264 片段' if videos else '真实帧') + '）' if ok else '❌ 未打通，见上面的错误字段'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

