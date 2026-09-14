"""Lip 推理引擎抽象（SPEC §2.1）

- MockLipEngine：无 GPU 时生成占位帧，供本机链路联调
- MuseTalkLipEngine：真实 MuseTalk（AutoDL 实例部署），V-01 验证后按官方 API 实现
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from io import BytesIO
from typing import List, Optional, Tuple

from PIL import Image

from schemas import LipFrame


class LipEngine(ABC):
    """口型推理引擎接口：输入 16k PCM 音频 + 形象帧 → 帧序列"""

    @abstractmethod
    def infer(
        self,
        audio_pcm_16k: bytes,
        avatar_frames_b64: List[str],
        fps: int,
        session_id: Optional[str] = None,
    ) -> dict:
        """返回 {"frames": List[LipFrame], "gpu_memory_mb": int, "infer_fps": float}"""
        raise NotImplementedError


def _placeholder_jpeg(size: Tuple[int, int] = (64, 64)) -> bytes:
    """生成灰色占位 JPEG 帧（mock 用）"""
    img = Image.new("RGB", size, (120, 120, 120))
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=60)
    return buf.getvalue()


class MockLipEngine(LipEngine):
    """mock：按音频时长生成占位帧序列（16k 单声道 → duration = n_bytes/32000 s）"""

    def infer(
        self,
        audio_pcm_16k: bytes,
        avatar_frames_b64: List[str],
        fps: int,
        session_id: Optional[str] = None,
    ) -> dict:
        duration_s = len(audio_pcm_16k) / 32000.0  # 16kHz * 2B/sample
        n_frames = max(1, int(duration_s * fps))
        frame = _placeholder_jpeg()
        t0 = time.time()
        frames = [
            LipFrame(frame=frame, pts_ms=int(i * 1000 / fps), frame_idx=i)
            for i in range(n_frames)
        ]
        elapsed = time.time() - t0
        return {
            "frames": frames,
            "gpu_memory_mb": 0,
            "infer_fps": n_frames / elapsed if elapsed > 0 else fps,
        }


class MuseTalkLipEngine(LipEngine):
    """真实 MuseTalk 引擎（部署在 AutoDL 实例，有卡模式）。

    V-01（MuseTalk GPU 验证）通过后，按官方 API 封装：
      1. 加载 unet/vae/pe/whisper（权重见 MuseTalk/models/）
      2. 形象帧 → landmark/bbox → crop(256²) → vae latents（preparation）
      3. 音频 → whisper mel → 按 fps 切 whisper_chunks
      4. 逐批：pe() → unet() → vae.decode_latents() → pad 回原图
    接口契约见 SPEC §2.1；实现文件：lip/musetalk_engine.py（实例部署时落地）
    """

    def infer(
        self,
        audio_pcm_16k: bytes,
        avatar_frames_b64: List[str],
        fps: int,
        session_id: Optional[str] = None,
    ) -> dict:
        raise NotImplementedError("MuseTalk 引擎待 V-01 验证后实现（见 lip/musetalk_engine.py）")
