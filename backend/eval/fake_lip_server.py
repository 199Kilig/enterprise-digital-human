"""假口型服务（本地 8002）：在没有云 GPU 时验证 ADR-005 的链路协议。

**它不产生任何口型**——只是把一段**真实的 MuseTalk 产物**
（backend/eval/reports/media/lip_demo_8s_crf26.mp4，云端 4090 实跑出来的 H.264）
按 SPEC §2.1 v1.2 的响应格式回放，用来验证：
  后端客户端（musetalk_engine）解析 → 编排层（routes）发 lip_video 事件 → 前端收到可播的 MP4

⚠️ 用途仅限协议联调；任何延迟/帧率数字都不得取自本服务（那是假的）。
   真实数字只认云 GPU 服务 `deploy/lip_service.py` 的输出。

启动：python backend/eval/fake_lip_server.py [--port 8002]
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# 中文 Windows 控制台默认 GBK，本文件启动横幅含 ⚠️（U+26A0），直接 print 会抛
# UnicodeEncodeError 导致服务当场退出（已实测复现）。这里显式转 UTF-8，
# 配合 .bat 里的 chcp 65001 可正常显示；errors="replace" 保证任何环境都不崩。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001 — 老 Python 或无 reconfigure 时忽略
    pass

CANNED = Path(__file__).resolve().parent / "reports" / "media" / "lip_demo_8s_crf26.mp4"
FPS = 25


class Handler(BaseHTTPRequestHandler):
    canned_path: Path = CANNED
    canned_b64: str = ""
    canned_frames: int = 0
    # 按目标帧数截断的缓存：{n_frames: base64}
    seg_cache: dict[int, str] = {}

    def log_message(self, fmt: str, *args) -> None:  # 静音，避免刷屏
        pass

    @classmethod
    def segment_for(cls, n_frames: int) -> str:
        """把真实产物截断成 n_frames 帧的片段（缓存）。

        必须真截断：否则响应里 n_frames 与实际 MP4 帧数不符，
        验证脚本会（正确地）报"字段不自洽"——假服务给假证据比不测更糟。
        """
        if n_frames >= cls.canned_frames:
            return cls.canned_b64
        cached = cls.seg_cache.get(n_frames)
        if cached:
            return cached
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory(prefix="fakeseg_") as td:
            out = Path(td) / f"seg_{n_frames}.mp4"
            cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(cls.canned_path),
                   "-frames:v", str(n_frames), "-c:v", "libx264", "-preset", "veryfast",
                   "-crf", "26", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
            r = subprocess.run(cmd, capture_output=True, check=False)
            if r.returncode != 0 or not out.exists():
                print(f"[fake-lip] ⚠️ 截断失败({n_frames}帧)，回退整段：{r.stderr.decode('utf-8', 'replace')[:120]}")
                return cls.canned_b64
            b64 = base64.b64encode(out.read_bytes()).decode("ascii")
        cls.seg_cache[n_frames] = b64
        return b64

    def _json(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/health":
            self._json(200, {
                "status": "ok",
                "avatar": "FAKE(实为回放真实产物)",
                "version": "fake",
                "device": "cpu",
                "transport_default": "h264",
                "gpu_free_mb": 0,
                "gpu_total_mb": 0,
                "frames_cached": self.canned_frames,
            })
            return
        self._json(404, {"detail": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/api/v1/lip/infer":
            self._json(404, {"detail": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            req = json.loads(raw.decode("utf-8"))
        except Exception:  # noqa: BLE001
            req = {}
        pcm_b64 = req.get("audio_pcm_16k_b64", "")
        audio_s = len(base64.b64decode(pcm_b64)) / 32000.0 if pcm_b64 else 0.0
        transport = (req.get("transport") or "h264").lower()

        # 按音频时长等比回放：从整段真产物里**真截断**出与音频等长的片段
        want_frames = max(1, int(round(audio_s * FPS)))
        n = min(want_frames, self.canned_frames) if self.canned_frames else want_frames
        seg_b64 = self.segment_for(n)
        size_kb = len(base64.b64decode(seg_b64)) / 1024
        print(f"[fake-lip] 收到 {audio_s:.2f}s 音频 → 回放 {n} 帧（{size_kb:.0f}KB，真产物截断）", flush=True)

        self._json(200, {
            "transport": "h264",
            "video_b64": seg_b64,
            "duration_ms": int(n * 1000 / FPS),
            "n_frames": n,
            "fps": FPS,
            "gpu_memory_mb": 0,
            "infer_fps": 0.0,   # 假服务不产生性能数字
            "wall_ms": 5,
            "stats": {"fake": True, "note": "回放真实产物，非实时推理"},
        })


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8002)
    ap.add_argument("--canned", default=str(CANNED))
    a = ap.parse_args()

    path = Path(a.canned)
    if not path.exists():
        print(f"❌ 找不到真实产物 {path}（需要云端实跑的 H.264 片段）")
        return 1
    data = path.read_bytes()
    Handler.canned_b64 = base64.b64encode(data).decode("ascii")
    # 用 ffprobe 拿真实帧数（不猜）
    import subprocess

    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=nb_frames", "-of", "csv=p=0", str(path)],
                           capture_output=True, check=True)
        Handler.canned_frames = int(r.stdout.decode().strip())
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ ffprobe 读取帧数失败（{exc}），按 25fps×8s 兜底")
        Handler.canned_frames = 200

    print(f"假口型服务启动 :{a.port}  回放 {path.name}（{len(data) / 1024:.0f}KB，{Handler.canned_frames} 帧）")
    print("⚠️  仅用于协议联调：本服务不产生口型，**性能/延迟数字一律无效**；")
    print("    字节数用同参数(veryfast/CRF26)重编码截断，可作量级参考，精确值只认云 GPU 实测（ADR-005：387KB/200帧）")
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
