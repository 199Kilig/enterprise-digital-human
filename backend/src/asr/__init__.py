"""流式语音识别（DESIGN §3.2 `asr/`）。

VAD/端点归属见 ADR-004：P1 由前端 AudioWorklet 负责端点与打断，后端只做识别。
"""
from asr.streaming import AsrError, AsrState, StreamingAsr, get_model, pcm16_to_float32

__all__ = ["AsrError", "AsrState", "StreamingAsr", "get_model", "pcm16_to_float32"]
