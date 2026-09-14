"""语音合成（DESIGN §3.2 `tts/`）：流式输出 + word 级时间戳。

时间戳是音画同步的前提（SPEC §4.3）：口型帧调度以音频时间戳为时钟基准，
故本模块的产出必须携带 words。
"""
from tts.cosyvoice import CosyVoiceTts, TtsChunk, TtsError

__all__ = ["CosyVoiceTts", "TtsChunk", "TtsError"]
