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
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from config import load_config
from schemas import AsrChunk

# 配置中心（DESIGN §4.4）唯一入口。此前本模块把 model / chunk_size 硬编码，导致
# config.yaml 的 asr 段与实现脱节（config 写 chunk_size=[5,10,5]，代码实际用 [0,10,5]）
# → v1.9 收口：这两项改由 config 提供；另外 4 个从未被读取的字段已从 config 删除。
_ASR_CFG = load_config().get("asr") or {}

SAMPLE_RATE = 16000
MODEL_NAME = str(_ASR_CFG.get("model", "paraformer-zh-streaming"))
CHUNK_SIZE = list(_ASR_CFG.get("chunk_size") or [0, 10, 5])
CHUNK_STRIDE = CHUNK_SIZE[1] * SAMPLE_RATE * 60 // 1000  # 9600 样本 = 600ms
CHUNK_MS = CHUNK_STRIDE * 1000 // SAMPLE_RATE            # 600ms：时间戳换算基准（SPEC §2 start_ms/end_ms）
ENC_LOOK_BACK = 4
DEC_LOOK_BACK = 1
# SPEC §6：ASR_TIMEOUT = 单块识别 >3s。实测单块 166ms（CPU RTF 0.27），余量充足；
# 冷启动后首个请求可能触发（RUNBOOK 坑 8：演示前先预热一次 /api/v1/asr/chunk）。
ASR_TIMEOUT_S = 3.0


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

        first_idx = self.state.chunks  # 本次增量处理的第一个分块序号（时间戳基准）
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
        # 时间戳语义（SPEC §2）：本次**增量文本**在用户语音流中的时间窗。
        # 一次 push 可能送出多块（大分片到达时），窗口必须覆盖本次处理的全部分块——
        # 修正前只标「最后一块」的窗口，多块时与增量起点不符。
        return AsrChunk(
            text=produced,
            is_final=is_last,
            start_ms=first_idx * CHUNK_MS,
            end_ms=self.state.chunks * CHUNK_MS,
            confidence=0.0,
        )

    def _generate(self, samples: np.ndarray, is_final: bool) -> str:
        # 注意：get_model() 的懒加载（首次 ~22s）不计入超时——那是加载成本，不是识别成本
        model = get_model()
        t0 = time.perf_counter()
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
        elapsed = time.perf_counter() - t0
        self.state.chunks += 1
        if elapsed > ASR_TIMEOUT_S:
            # 事后判定而非中断：FunASR 推理是同步阻塞调用，强杀线程会破坏模型内部状态
            # （与 DESIGN-打断 §3.3「GPU 在途推理不取消」同理）。此时 cache 已被本次调用改写，
            # 调用方（routes）收到 ASR_TIMEOUT 会丢弃该会话的流式状态。
            raise AsrError(
                "ASR_TIMEOUT",
                f"单块识别耗时 {elapsed * 1000:.0f}ms 超过 {ASR_TIMEOUT_S * 1000:.0f}ms"
                f"（冷启动或 CPU 抢占？建议先预热一次 /api/v1/asr/chunk）",
            )
        self.state.started = True
        if not res:
            return ""
        return (res[0].get("text") or "").strip()

    @property
    def full_text(self) -> str:
        return self.state.text
