"""生成数字人「待机（idle）」循环素材 —— 云 GPU 静音驱动 + 选最静窗口 + 往复拼接。

================================================================================
为什么要这么绕（别简化成"随便截一段循环播放"）：

1. MuseTalk 是**音频驱动**的口型模型 —— 待机时没有音频就没有输出，
   所谓"实时渲染待机动作"在这套模型上不存在（说它"不做实时待机"不是偷懒）。
2. 但拿"说话产物"（如 avatar_v01.mp4，数字人正在讲话的 60s 片）当待机，
   观感就是**它一直在自言自语**（用户实测反馈："一直循环播放一个 1 分钟的视频"）。
3. 用静音驱动虽然能出片，但模型对静音**仍会做无意义的小幅口型**（实测嘴部帧间差
   与说话产物同量级，只小一半），而且任意窗口首尾都不相接（接缝差是窗口内差异的 9 倍）。

所以流程是：静音驱动出候选 → **挑最静的一段** → **往复拼接**让末帧接回首帧 → 全 I 帧编码。
================================================================================

四步：
  ① 静音（16k/单声道）POST 给口型服务 → 拿到候选 H.264 片段
  ② 抽帧后滑动窗口打分（窗口内帧间差 + 循环接缝差）挑最静的 --window 帧
  ③ 往复（ping-pong）拼接并去掉重复端点：序列 = [f0..fn] + [f(n-1)..f1]
     → 末帧≈首帧，循环点不跳
  ④ 全 I 帧 + crf 18 编码：P 帧的长预测会让**解码后**的末帧偏离源帧，
     接缝反而比源 PNG 大 5 倍（实测 crf 23 时接缝 1.87 vs 帧间差 0.35）

用法：
  # 云口型服务经 SSH 隧道（本机 :8002）已在跑时：
  python make_idle.py --out ../../frontend/public/media/avatar_idle.mp4
  # 只校验现有素材的动幅度/接缝（不改文件）：
  python make_idle.py --check-only --out ../../frontend/public/media/avatar_idle.mp4

校验口径（一定要看，否则可能无声地做出"嘴一直在动"的素材）：
  嘴部帧间差 mean 越小越静；循环接缝差应落在帧间差区间内（≈max 附近即可，别大出一个数量级）。
  参考实测：待机 idle mean 1.715 / max 1.913 / 接缝 1.869（说话产物同口径 mean 3.272）。
"""

from __future__ import annotations

import argparse
import base64
import json
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

SAMPLE_RATE = 16000
FPS = 25
# 嘴部区域（相对画面：y0, y1, x0, x1）——待机的"动"主要体现在这里
MOUTH_Y0, MOUTH_Y1, MOUTH_X0, MOUTH_X1 = 0.28, 0.52, 0.28, 0.72


def _gray(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("L"), dtype=np.float32)


def _mouth(arr: np.ndarray) -> np.ndarray:
    h, w = arr.shape
    return arr[int(h * MOUTH_Y0) : int(h * MOUTH_Y1), int(w * MOUTH_X0) : int(w * MOUTH_X1)]


def _extract(video: Path, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video), "-vsync", "0", str(out_dir / "f_%04d.png")],
        check=True,
    )
    return sorted(out_dir.glob("f_*.png"))


def infer_silent(service: str, seconds: float, dst: Path, crf: int = 18) -> dict:
    """用静音驱动云 GPU 口型服务，把返回的 H.264 片段写进 dst。"""
    pcm = np.zeros(int(SAMPLE_RATE * seconds), dtype="<i2")
    body = json.dumps(
        {
            "audio_pcm_16k_b64": base64.b64encode(pcm.tobytes()).decode(),
            "fps": FPS,
            "transport": "h264",
            "crf": crf,
        }
    ).encode()
    req = urllib.request.Request(
        service.rstrip("/") + "/api/v1/lip/infer",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        payload = json.loads(resp.read())
    if payload.get("transport") != "h264" or not payload.get("video_b64"):
        raise SystemExit(
            f"口型服务未返回 H.264 片段（transport={payload.get('transport')}）。"
            "旧版服务只回逐帧 JPEG，请先按 RUNBOOK §3.12 更新云侧代码。"
        )
    dst.write_bytes(base64.b64decode(payload["video_b64"]))
    return {k: v for k, v in payload.items() if k != "video_b64"}


def pick_quietest(frames: list[Path], window: int) -> tuple[int, float, float, float]:
    """滑动窗口挑最静的一段：得分 = 窗口内帧间差均值 + 0.5×首末接缝差。"""
    mouths = [_mouth(_gray(f)) for f in frames]
    diffs = [float(np.abs(mouths[i] - mouths[i - 1]).mean()) for i in range(1, len(mouths))]
    best: tuple[int, float, float, float] | None = None
    for start in range(0, len(mouths) - window + 1):
        inner = float(np.mean(diffs[start : start + window - 1]))
        seam = float(np.abs(mouths[start + window - 1] - mouths[start]).mean())
        score = inner + 0.5 * seam
        if best is None or score < best[0]:
            best = (start, inner, seam, score)
    assert best is not None, "帧数不足以放下一个窗口"
    return best[0], best[1], best[2], best[3]


def build_loop(frames: list[Path], start: int, window: int, work: Path, dst: Path) -> int:
    """往复拼接 + 全 I 帧编码。"""
    picked = frames[start : start + window]
    order = picked + picked[::-1][1:-1]  # 去掉重复端点 → 末帧≈首帧
    seq_dir = work / "seq"
    seq_dir.mkdir(parents=True, exist_ok=True)
    for i, f in enumerate(order, start=1):
        shutil.copy(f, seq_dir / f"seq_{i:04d}.png")
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-framerate", str(FPS), "-i", str(seq_dir / "seq_%04d.png"),
            "-c:v", "libx264", "-crf", "18", "-g", "1", "-pix_fmt", "yuv420p", "-r", str(FPS),
            str(dst),
        ],
        check=True,
    )
    return len(order)


def report(video: Path, work: Path) -> None:
    frames = _extract(video, work / "check")
    mouths = [_mouth(_gray(f)) for f in frames]
    diffs = [float(np.abs(mouths[i] - mouths[i - 1]).mean()) for i in range(1, len(mouths))]
    seam = float(np.abs(mouths[-1] - mouths[0]).mean())
    ok = seam <= max(diffs) * 1.2 if diffs else False
    print(f"帧数={len(frames)} 时长={len(frames) / FPS:.2f}s 体积={video.stat().st_size / 1024:.0f} KB")
    print(f"嘴部帧间差 mean={np.mean(diffs):.3f} max={max(diffs):.3f}")
    print(f"循环接缝差={seam:.3f} → {'无缝（落在帧间差区间内）' if ok else '⚠️ 仍有可见跳变'}")


def main() -> None:
    repo = Path(__file__).resolve().parents[2]
    ap = argparse.ArgumentParser(description="生成待机(idle)循环素材")
    ap.add_argument("--service", default="http://127.0.0.1:8002", help="口型服务（默认经 SSH 隧道）")
    ap.add_argument("--seconds", type=float, default=4.0, help="静音驱动时长（候选帧池）")
    ap.add_argument("--window", type=int, default=25, help="选窗帧数（25 = 1 秒）")
    ap.add_argument(
        "--out",
        default=str(repo / "frontend" / "public" / "media" / "avatar_idle.mp4"),
        help="输出路径",
    )
    ap.add_argument("--check-only", action="store_true", help="只校验 --out 的现有素材")
    args = ap.parse_args()

    dst = Path(args.out).resolve()
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        if not args.check_only:
            print(f"[1/4] 静音驱动 {args.service} → 候选 {args.seconds}s ...")
            raw = work / "raw.mp4"
            info = infer_silent(args.service, args.seconds, raw)
            print(f"      n_frames={info.get('n_frames')} duration_ms={info.get('duration_ms')}")

            print(f"[2/4] 抽帧 + 挑最静 {args.window} 帧窗口 ...")
            frames = _extract(raw, work / "raw_frames")
            start, inner, seam, _ = pick_quietest(frames, args.window)
            print(f"      窗口起点={start}（帧号）窗口内帧间差={inner:.3f} 该窗接缝={seam:.3f}")

            print("[3/4] 往复拼接 → 无缝序列 ...")
            n = build_loop(frames, start, args.window, work, dst)
            print(f"      {n} 帧 / {n / FPS:.2f}s → {dst}")

        print("[4/4] 校验（嘴部动幅度 / 循环接缝）")
        if not dst.exists():
            raise SystemExit(f"素材不存在：{dst}")
        report(dst, work)


if __name__ == "__main__":
    main()
