"""流式语音识别（DESIGN §3.2 `asr/`）：FunASR paraformer-zh-streaming（CPU，本机可跑）。

口径来自 V-03 已验证的官方流式用法（勿改，改则需重跑 V-03）：
  - chunk_size=[0,10,5] → 600ms/块（第二个数×60ms 为粒度，第三个数为 lookahead）
  - chunk_stride = chunk_size[1]*960 = 9600 样本（600ms @16kHz）
  - cache={} 必须跨块持久化（流式状态），is_final=True 仅最后一块
  - 输入 numpy float32，16kHz 单声道
实测（V-03）：单块处理 166ms（RTF 0.27）；600ms 粒度下首字墙钟 ≈766ms。

VAD/端点归属见 ADR-004：P1 由前端 AudioWorklet 做（含打断），后端只负责"收到音频就识别"。
本模块不做端点检测——端点由调用方（前端静音检测 / 未来的 fsmn-vad）决定何时置 end=True。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from schemas import AsrChunk

SAMPLE_RATE = 16000
CHUNK_SIZE = [0, 10, 5]
CHUNK_STRIDE = CHUNK_SIZE[1] * 960  # 9600 样本 = 600ms
ENC_LOOK_BACK = 4
DEC_LOOK_BACK = 1
MODEL_NAME = "paraformer-zh-streaming"


class AsrError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code  # 对齐 SPEC §6：ASR_ERROR / ASR_TIMEOUT
        self.message = message


_model = None
_model_lock = threading.Lock()


def get_model():
    """懒加载 FunASR 模型（首次约需下载/加载数秒；进程内单例）。"""
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                try:
                    from funasr import AutoModel  # 延迟导入：funasr 启动慢，别拖累 API 进程启动
                except ImportError as exc:  # pragma: no cover
                    raise AsrError(
                        "ASR_ERROR",
                        f"缺少 funasr 依赖：{exc}。安装：uv pip install funasr modelscope",
                    ) from exc
                _model = AutoModel(model=MODEL_NAME)
    return _model


@dataclass
class AsrState:
    """一次语句（utterance）的流式状态。"""

    cache: dict = field(default_factory=dict)
    pending: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    text: str = ""  # 累计识别文本
    started: bool = False
    chunks: int = 0

    def reset(self) -> None:
        self.cache = {}
        self.pending = np.zeros(0, dtype=np.float32)
        self.text = ""
        self.started = False
        self.chunks = 0


def pcm16_to_float32(pcm: bytes) -> np.ndarray:
    """16bit 小端 PCM（前端上行格式）→ float32 [-1,1]（FunASR 输入格式）。"""
    if len(pcm) % 2:
        pcm = pcm[:-1]  # 丢弃半个样本（分片边界可能切断）
    arr = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
    return arr / 32768.0


class StreamingAsr:
    """单路流式识别器：喂任意长度的 PCM，内部按 600ms 攒块送模型。"""

    def __init__(self, state: Optional[AsrState] = None) -> None:
        self.state = state or AsrState()

    def push(self, pcm16: bytes, is_last: bool = False) -> AsrChunk:
        """喂一段 16k/16bit 单声道 PCM。

        满 600ms 才送模型（避免碎片化推理）；is_last=True 时把残余样本一并送出并置 is_final。
        """
        samples = pcm16_to_float32(pcm16)
        if samples.size:
            self.state.pending = np.concatenate([self.state.pending, samples])

        produced = ""
        while self.state.pending.size >= CHUNK_STRIDE:
            piece = self.state.pending[:CHUNK_STRIDE]
            self.state.pending = self.state.pending[CHUNK_STRIDE:]
            produced += self._generate(piece, is_final=False)

        if is_last:
            tail = self.state.pending
            self.state.pending = np.zeros(0, dtype=np.float32)
            produced += self._generate(tail, is_final=True)

        if produced:
            self.state.text += produced
        return AsrChunk(
            text=produced,
            is_final=is_last,
            start_ms=(self.state.chunks - 1) * 600 if self.state.chunks else 0,
            end_ms=self.state.chunks * 600,
            confidence=0.0,
        )

    def _generate(self, samples: np.ndarray, is_final: bool) -> str:
        model = get_model()
        try:
            res = model.generate(
                input=samples,
                cache=self.state.cache,
                is_final=is_final,
                chunk_size=CHUNK_SIZE,
                encoder_chunk_look_back=ENC_LOOK_BACK,
                decoder_chunk_look_back=DEC_LOOK_BACK,
            )
        except Exception as exc:  # noqa: BLE001
            raise AsrError("ASR_ERROR", f"识别失败: {type(exc).__name__}: {exc}") from exc
        self.state.chunks += 1
        self.state.started = True
        if not res:
            return ""
        return (res[0].get("text") or "").strip()

    @property
    def full_text(self) -> str:
        return self.state.text
