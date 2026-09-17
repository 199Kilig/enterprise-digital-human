"""链路级公共常量（单一定义，全链路引用）。

为什么要单独一个模块：`32 字节/毫秒`这个推导值曾散落三处——
`api/routes.py`（`* 32`）、`lip/engine.py`（`/ 32000.0`）、`tts/cosyvoice.py`（`BYTES_PER_MS`）——
数值一致但各写一遍，含义只靠注释维系。一旦链路支持非 16kHz 采样
（CosyVoice2 原生 24kHz，`config.yaml` 已留伏笔），这三处会**同时错且不易发现**。
"""
from __future__ import annotations

SAMPLE_RATE = 16000  # 链路统一基准采样率（SPEC §2 TtsSegment.sample_rate）
BYTES_PER_SAMPLE = 2  # PCM 16bit 单声道
BYTES_PER_MS = SAMPLE_RATE * BYTES_PER_SAMPLE // 1000  # 32 B/ms


def ms_to_bytes(ms: float) -> int:
    """毫秒 → PCM 字节数（16k/16bit 单声道）。"""
    return int(ms * BYTES_PER_MS)


def bytes_to_ms(n_bytes: float) -> int:
    """PCM 字节数 → 毫秒（时间戳换算用；取整误差 <1ms，句级游标由服务端时间戳校正）。"""
    return int(n_bytes / BYTES_PER_MS)
