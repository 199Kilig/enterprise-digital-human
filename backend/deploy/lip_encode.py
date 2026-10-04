"""口型帧 → H.264(MP4) 编码器（ADR-005 的传输载体）。

为什么单独成文件：该模块**不依赖 torch/cv2**，只吃 numpy BGR 帧 + 调用 ffmpeg，
因此可以在本机直接单元测试（云侧依赖的 cv2/torch 本地没有，但编码路径能验）。

设计要点：
1. **帧走 stdin 管道，不落 rawvideo 临时文件**——200 帧 704×1216 的裸数据是 ~512MB，
   落盘不可接受；而 MP4 输出走**文件**（可 seek），这样才能用 `+faststart`
   （moov 前置，浏览器拿到即可播）。
2. `-v error` + stderr 单独管道：避免 ffmpeg 日志刷满缓冲区导致死锁
   （stdout 已重定向到文件，不会再和 stdin 争管道）。
3. **临时目录可被 `LIP_TMPDIR` 覆盖，且优先用固定子目录**：MP4 产物要落盘，
   所以这个模块对临时目录有真实依赖。三类环境都需要能改它：
   本机受限/沙箱化运行时系统 temp 可能整块不可写；
   云 GPU 的系统盘通常很小而数据盘挂在 `/root/autodl-tmp`；
   受限环境常只放行**固定路径**、拒绝随机后缀目录。
   于是 `LIP_TMPDIR` 存在时优先复用其下的固定子目录，否则退回系统 temp 的
   `mkdtemp`（见 `_tempdir`）。
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from contextlib import AbstractContextManager, nullcontext
from typing import Iterable, Iterator

import numpy as np

DEFAULT_FPS = 25
DEFAULT_CRF = 26
DEFAULT_PRESET = "veryfast"


class EncodeError(RuntimeError):
    """编码失败（ffmpeg 缺失/参数错误/帧数据非法）。"""


def _chain_first(first: np.ndarray, rest: Iterator[np.ndarray]) -> Iterator[np.ndarray]:
    yield first
    yield from rest


def _ffmpeg_bin() -> str:
    return os.environ.get("LIP_FFMPEG", "ffmpeg")


def _tmpdir() -> str | None:
    """临时目录根：`LIP_TMPDIR` 优先，未设/设成空串则回落到系统 temp。

    每次调用都重读环境变量（而不是模块级常量）：测试与被调用方可在运行时改。
    """
    return os.environ.get("LIP_TMPDIR") or None


def _tempdir(prefix: str) -> AbstractContextManager[str]:
    """建临时目录，返回可 `with ... as path` 的上下文。

    两级策略，为的是让**受限环境下也能跑**（本机沙箱化运行时真实遇到过）：
      1. 若 `LIP_TMPDIR` 下已存在 `<prefix>work` 固定目录，直接用——受限环境常按
         路径白名单放行**固定**路径，而随机后缀（mkdtemp）路径会被直接拒绝；
         pytest 侧由 `backend/tests/conftest.py` 预先创建这类固定目录。
      2. 否则退回 `mkdtemp`（正常开发/CI/云端的默认路径），并用
         `ignore_cleanup_errors` 让清理阶段的 chmod/rmtree 失败不影响业务结果
         ——清理是卫生问题，不该把一次成功的编码变成异常。
    """
    root = _tmpdir()
    if root:
        work = os.path.join(root, f"{prefix.rstrip('_')}work")
        if os.path.isdir(work):
            return nullcontext(work)
    return tempfile.TemporaryDirectory(
        prefix=prefix, dir=root, ignore_cleanup_errors=True
    )


def encode_frames_to_mp4(
    frames: Iterable[np.ndarray],
    fps: int = DEFAULT_FPS,
    crf: int = DEFAULT_CRF,
    preset: str = DEFAULT_PRESET,
    faststart: bool = True,
) -> bytes:
    """BGR uint8 帧序列 → MP4(H.264, yuv420p) 字节。

    frames: 可迭代对象，每帧 shape=(H, W, 3) uint8（OpenCV BGR 口径，与 MuseTalk 输出一致）。
            **接受任意可迭代**（含生成器）：704×1216 的 200 帧裸数据约 512MB，
            因此不要先 list() 再传，边取边写管道才能保持常量内存。
    """
    it = iter(frames)
    try:
        first = np.asarray(next(it))
    except StopIteration as exc:
        raise EncodeError("空帧序列，无法编码") from exc

    if first.ndim != 3 or first.shape[2] != 3:
        raise EncodeError(f"帧形状非法（期望 HxWx3 BGR）: {first.shape}")
    h, w = int(first.shape[0]), int(first.shape[1])
    if h % 2 or w % 2:
        # yuv420p 要求偶数边长；MuseTalk 输出 704x1216 天然满足，此处防御非法输入
        raise EncodeError(f"yuv420p 要求偶数宽高，收到 {w}x{h}")

    cmd = [
        _ffmpeg_bin(), "-y", "-v", "error",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", str(fps),
        "-i", "pipe:0",
        "-an",
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-pix_fmt", "yuv420p",
    ]
    if faststart:
        cmd += ["-movflags", "+faststart"]

    with _tempdir("lipenc_") as td:
        out_path = os.path.join(td, "out.mp4")
        cmd += [out_path]
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,  # noqa: S603
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except FileNotFoundError as exc:
            raise EncodeError(f"找不到 ffmpeg（{_ffmpeg_bin()}）：{exc}") from exc

        try:
            assert proc.stdin is not None
            for f in _chain_first(first, it):
                arr = np.asarray(f)
                if arr.shape[:2] != (h, w):
                    raise EncodeError(f"帧尺寸不一致：期望 {w}x{h}，收到 {arr.shape[1]}x{arr.shape[0]}")
                proc.stdin.write(np.ascontiguousarray(arr, dtype=np.uint8).tobytes())
            proc.stdin.close()
        except BrokenPipeError as exc:
            raise EncodeError(f"ffmpeg 提前终止：{_tail(proc)}") from exc

        err = proc.stderr.read() if proc.stderr else b""
        proc.wait()
        if proc.returncode != 0:
            raise EncodeError(f"ffmpeg 退出码 {proc.returncode}: {err.decode('utf-8', 'replace')[:300]}")
        if not os.path.exists(out_path):
            raise EncodeError(f"ffmpeg 未产出文件: {err.decode('utf-8', 'replace')[:200]}")

        with open(out_path, "rb") as fh:
            data = fh.read()
    if not data:
        raise EncodeError("编码结果为空")
    return data


def _tail(proc: subprocess.Popen) -> str:  # type: ignore[type-arg]
    try:
        return (proc.stderr.read() if proc.stderr else b"").decode("utf-8", "replace")[:200]
    except Exception:  # noqa: BLE001
        return "(stderr 不可读)"


def probe_mp4(data: bytes) -> dict:
    """用 ffprobe 读回编码结果的真实参数（验证用：证明产物真是 H.264 且帧数正确）。"""
    with _tempdir("lipprobe_") as td:
        p = os.path.join(td, "p.mp4")
        with open(p, "wb") as fh:
            fh.write(data)
        cmd = [_ffmpeg_bin().replace("ffmpeg", "ffprobe"), "-v", "error",
               "-select_streams", "v:0",
               "-show_entries", "stream=codec_name,width,height,nb_frames,avg_frame_rate,pix_fmt",
               "-of", "json", p]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=60, check=False)  # noqa: S603
        except FileNotFoundError as exc:
            raise EncodeError("找不到 ffprobe") from exc
        if r.returncode != 0:
            raise EncodeError(f"ffprobe 失败: {r.stderr.decode('utf-8', 'replace')[:200]}")
        import json as _json

        streams = _json.loads(r.stdout.decode("utf-8")).get("streams") or [{}]
        s = streams[0]
        return {
            "codec": s.get("codec_name"),
            "width": s.get("width"),
            "height": s.get("height"),
            "nb_frames": int(s["nb_frames"]) if s.get("nb_frames") else None,
            "fps": s.get("avg_frame_rate"),
            "pix_fmt": s.get("pix_fmt"),
        }

