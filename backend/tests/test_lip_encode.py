"""口型传输编码器单测（ADR-005）。

为什么这些测试值得存在：H.264 编码是**云侧**新增的路径，而云 GPU 会关机。
把编码器做成不依赖 torch/cv2 的独立模块后，本机就能验证它的正确性——
避免"代码写完但只能等开机才发现参数写错"。

真实内容回归：用云端真实产出（backend/eval/reports/media/lip_demo_8s_crf26.mp4，
704×1216/200 帧，MuseTalk 真实口型输出）解帧后重新编码，验证：
  - 编出来的确实是 H.264、尺寸/帧数正确
  - 体积与云端 ffmpeg 直出的结果同量级（同内容同 CRF，不应差出数量级）
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import numpy as np
import pytest

from lip_encode import EncodeError, encode_frames_to_mp4, probe_mp4

REAL_MP4 = Path(__file__).resolve().parent.parent / "eval" / "reports" / "media" / "lip_demo_8s_crf26.mp4"


def _synthetic_frames(n: int, w: int = 704, h: int = 1216) -> list[np.ndarray]:
    """合成帧：静态背景 + 移动的"嘴"矩形（模拟真实内容的可压缩性）。"""
    frames = []
    for i in range(n):
        f = np.full((h, w, 3), 90, dtype=np.uint8)
        f[200:260, :] = 140  # 头部横带，增加一点结构
        y = 600 + (i * 7) % 60
        f[y : y + 30, 300:400] = 30  # 移动的暗块 ≈ 嘴
        frames.append(f)
    return frames


def frames_from_video(path: Path, limit: int | None = None) -> list[np.ndarray]:
    """用 ffmpeg 把视频解成 BGR 帧（与 LipEngine 输出同口径）。"""
    probe = probe_mp4(path.read_bytes())
    w, h = int(probe["width"]), int(probe["height"])
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    proc = subprocess.run(cmd, capture_output=True, check=True)  # noqa: S603
    raw = proc.stdout
    frame_bytes = w * h * 3
    n = len(raw) // frame_bytes
    if limit is not None:
        n = min(n, limit)
    return [np.frombuffer(raw[i * frame_bytes : (i + 1) * frame_bytes], dtype=np.uint8)
            .reshape(h, w, 3).copy() for i in range(n)]


def test_encode_synthetic_frames_structure() -> None:
    """合成帧 → 合法 H.264，尺寸/帧率/帧数都对。"""
    data = encode_frames_to_mp4(_synthetic_frames(50), fps=25, crf=26)
    assert data[:4] == b"\x00\x00\x00\x18" or b"ftyp" in data[:16], "不是合法 MP4 容器"
    info = probe_mp4(data)
    assert info["codec"] == "h264", f"编码器不是 H.264: {info}"
    assert (info["width"], info["height"]) == (704, 1216)
    assert info["nb_frames"] == 50
    assert info["fps"] == "25/1", f"帧率应为 25/1，实际 {info['fps']}"
    assert info["pix_fmt"] == "yuv420p", "浏览器兼容性要求 yuv420p"


def test_moov_is_at_front_for_progressive_playback() -> None:
    """faststart 生效：moov 必须在 mdat 之前（浏览器拿到即可播）。"""
    data = encode_frames_to_mp4(_synthetic_frames(10), fps=25)
    assert data.index(b"moov") < data.index(b"mdat"), "moov 未前置，faststart 未生效"


def test_accepts_generator_without_materializing() -> None:
    """编码器必须能吃生成器（200 帧裸数据 ~512MB，不能先 list 再编码）。"""
    seen = {"n": 0}

    def gen():
        for f in _synthetic_frames(30):
            seen["n"] += 1
            yield f

    data = encode_frames_to_mp4(gen(), fps=25)
    assert seen["n"] == 30
    assert probe_mp4(data)["nb_frames"] == 30


def test_rejects_bad_input() -> None:
    with pytest.raises(EncodeError):
        encode_frames_to_mp4(iter([]))
    with pytest.raises(EncodeError):
        encode_frames_to_mp4(iter([np.zeros((100, 101, 3), dtype=np.uint8)]))  # 奇数宽
    with pytest.raises(EncodeError):
        encode_frames_to_mp4(iter([np.zeros((10, 10), dtype=np.uint8)]))  # 非 HxWx3
    # 帧尺寸不一致
    with pytest.raises(EncodeError):
        encode_frames_to_mp4(iter([np.zeros((16, 16, 3), dtype=np.uint8),
                                   np.zeros((16, 18, 3), dtype=np.uint8)]))


@pytest.mark.skipif(not REAL_MP4.exists(), reason="云端真实产物不在（需先跑 V-01/口型服务）")
@pytest.mark.skipif(os.environ.get("SKIP_SLOW_ENCODE") == "1", reason="显式跳过慢测试")
def test_real_content_encode_parity() -> None:
    """真实内容回归：同一批真实帧重编码，体积与云端直出同量级、帧数一致。"""
    ref = REAL_MP4.read_bytes()
    frames = frames_from_video(REAL_MP4)
    assert len(frames) == 200, f"参考素材应有 200 帧，实际 {len(frames)}"

    mine = encode_frames_to_mp4(frames, fps=25, crf=26)
    info = probe_mp4(mine)
    assert info["codec"] == "h264"
    assert info["nb_frames"] == 200

    ratio = len(mine) / len(ref)
    print(f"\n参考(云端直出)={len(ref) / 1024:.0f}KB  本机重编码={len(mine) / 1024:.0f}KB  比值={ratio:.3f}")
    # 同内容同 CRF：体积应在同一量级（ffmpeg 版本差异 8.1 vs 4.2 允许 ±60%）
    assert 0.4 < ratio < 2.5, f"体积相差过大，编码参数可能有误: {ratio:.3f}"

    # ADR-005 的核心结论：H.264 相对逐帧 JPEG 的体积优势（实测 JPEG 15.52MB）
    assert len(mine) < 2 * 1024 * 1024, f"H.264 产物异常大: {len(mine) / 1024 / 1024:.2f}MB"
