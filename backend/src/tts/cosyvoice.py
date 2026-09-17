"""流式语音合成（DESIGN §3.2 `tts/`）。

实现方式：DashScope CosyVoice 的 WebSocket 双工协议（不是 SDK 封装），
原因是 word 级时间戳（SPEC §4.3 音画同步的硬前提）只从原始 result-generated
事件的 payload.output.sentence.words 里拿得到。

⚠️ 选型实测（2026-09-13）：`cosyvoice-v1` 即使显式传 word_timestamp_enabled=true，
sentence.words 全为空；`cosyvoice-v2`（voice longxiaochun_v2）返回
{text, begin_index, end_index, begin_time, end_time}。故 config.yaml tts.model = cosyvoice-v2。

两个已实测的性能要点：
1. **连接复用**：WS 握手实测 ~195ms，占总首包时间的 ~40%。故本模块维持一条长连接
   （多任务复用，实测可行），连接失效才重建。
2. **句级调用**：为了和 LLM 流水线重叠（DESIGN §3.1），每句一次任务；
   调用方通过 start_offset_ms 传入该句在整轮音频中的起点，保证时间戳仍是全局单调的。

⚠️ 时间戳两个坑（都已处理，勿回退）：
- words.begin_time 是**句内相对**时间（每句从 ~0 重新开始）→ 必须叠加句起点
- 同一句的 words 是**增量上报**的（sentence-begin 只给前几个词）→ 必须只发新增部分
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

import websockets

from config import get_secret, load_config
from constants import BYTES_PER_MS, SAMPLE_RATE  # 单一定义，勿在本模块重定义（见 constants.py）

WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"


class TtsError(Exception):
    """TTS 段错误（code 对齐 SPEC §6：TTS_TIMEOUT / TTS_ERROR / TTS_NO_TIMESTAMP）"""

    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass
class TtsChunk:
    """一片合成音频 + 该片内已产出的词级时间戳（时间戳为整轮音频的全局时间）"""

    audio: bytes
    offset_ms: int
    words: list[tuple[str, int, int]] = field(default_factory=list)
    is_first: bool = False


@dataclass
class TtsResult:
    first_packet_ms: Optional[int] = None
    total_ms: int = 0
    audio_bytes: int = 0
    words: list[tuple[str, int, int]] = field(default_factory=list)
    reused_connection: bool = False


def _headers() -> dict:
    key = get_secret("DASHSCOPE_API_KEY")
    if not key:
        raise TtsError("TTS_ERROR", "DASHSCOPE_API_KEY 未配置（写入 backend/.env 或设为环境变量）")
    return {"Authorization": f"bearer {key}", "X-DashScope-DataInspection": "enable"}


def _parse_words(sentence: dict, base_ms: int) -> list[tuple[str, int, int]]:
    """把句内相对时间戳换算成整段音频的全局时间戳。

    ⚠️ DashScope 的 words.begin_time 是**句内相对**时间（每句从 ~0 重新开始），
    音画同步（SPEC §4.3）用的是整段音频时钟，故必须叠加该句的全局起点 base_ms。
    """
    out: list[tuple[str, int, int]] = []
    for w in sentence.get("words") or []:
        text = w.get("text")
        begin, end = w.get("begin_time"), w.get("end_time")
        if text is None or begin is None or end is None:
            continue
        out.append((str(text), int(begin) + base_ms, int(end) + base_ms))
    return out


def _is_open(ws: object) -> bool:
    """跨 websockets 版本判断连接是否可用。"""
    state = getattr(ws, "state", None)
    if state is not None:
        return getattr(state, "name", str(state)).upper() == "OPEN"
    return not getattr(ws, "closed", True)


async def _connect() -> object:
    try:
        return await websockets.connect(  # type: ignore[call-arg]
            WS_URL, additional_headers=_headers(), max_size=16 * 1024 * 1024
        )
    except TypeError:  # 旧版 websockets 用 extra_headers
        return await websockets.connect(  # type: ignore[call-arg]
            WS_URL, extra_headers=_headers(), max_size=16 * 1024 * 1024
        )


class _Session:
    """维持一条可复用的 WS 长连接（握手实测 ~195ms，不能每句都付）。"""

    def __init__(self) -> None:
        self._ws: object | None = None
        self._lock = asyncio.Lock()
        self.connect_ms: Optional[int] = None
        # 同一连接**同时只允许一个 tts task**（#10）：DashScope duplex 的事件按 task_id 隔离，
        # 但**音频是裸二进制帧、不带 task_id**，接收侧无法区分属于哪个 task
        # → 并发使用同一连接必然把 A 句的音频当成 B 句的。
        # 这里用锁把"并发"变成"排队"；当前 tts_consumer 本就串行消费，属防御性加锁、不改变现状。
        self._task_lock = asyncio.Lock()

    def use(self) -> asyncio.Lock:
        """独占使用本条连接的锁（见上方说明）。"""
        return self._task_lock

    async def get(self) -> tuple[object, bool]:
        """返回 (连接, 是否复用)。"""
        async with self._lock:
            if self._ws is not None and _is_open(self._ws):
                return self._ws, True
            t0 = time.perf_counter()
            self._ws = await _connect()
            self.connect_ms = int((time.perf_counter() - t0) * 1000)
            return self._ws, False

    def invalidate(self) -> None:
        self._ws = None

    async def close(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
            self._ws = None


class CosyVoiceTts:
    """CosyVoice 流式合成客户端（config.yaml tts 段驱动）。"""

    def __init__(self, model: str | None = None, voice: str | None = None, session: _Session | None = None) -> None:
        cfg = (load_config().get("tts") or {})
        self.model: str = model or cfg.get("model") or "cosyvoice-v2"
        self.voice: str = voice or cfg.get("voice") or "longxiaochun_v2"
        self.result = TtsResult()
        self._session = session or _Session()

    async def stream(
        self,
        text: str,
        start_offset_ms: int = 0,
        timeout_s: float = 20.0,
    ) -> AsyncIterator[TtsChunk]:
        """合成一句文本，产出音频片与全局时间戳的词。

        start_offset_ms：该句在整轮回答音频中的起点（上一句累计时长），
        由调用方维护以保证多句拼起来的音画时间轴连续。

        ⚠️ **同一实例同时只允许一个 task**（`_Session.use`）：DashScope 的音频帧不带
        task_id，无法按 task 区分，并发使用同一连接会串音频。
        """
        async with self._session.use():
            try:
                async for chunk in self._stream_once(text, start_offset_ms, timeout_s):
                    yield chunk
            except TtsError as exc:
                if not exc.retryable:
                    raise
                # 连接失效（Idle 断链等）→ 重建后重试一次
                self._session.invalidate()
                async for chunk in self._stream_once(text, start_offset_ms, timeout_s):
                    yield chunk

    async def _stream_once(
        self, text: str, start_offset_ms: int, timeout_s: float
    ) -> AsyncIterator[TtsChunk]:
        task_id = uuid.uuid4().hex
        run_task = {
            "header": {"action": "run-task", "task_id": task_id, "streaming": "duplex"},
            "payload": {
                "task_group": "audio",
                "task": "tts",
                "function": "SpeechSynthesizer",
                "model": self.model,
                "parameters": {
                    "text_type": "PlainText",
                    "voice": self.voice,
                    "format": "pcm",
                    "sample_rate": SAMPLE_RATE,
                    "volume": 50,
                    "rate": 1,
                    "pitch": 1,
                    "word_timestamp_enabled": True,  # SPEC §4.3 前提
                },
                "input": {},
            },
        }
        cont_task = {
            "header": {"action": "continue-task", "task_id": task_id, "streaming": "duplex"},
            "payload": {"input": {"text": text}},
        }
        fin_task = {
            "header": {"action": "finish-task", "task_id": task_id, "streaming": "duplex"},
            "payload": {"input": {}},
        }

        stats = TtsResult()
        pending_words: list[tuple[str, int, int]] = []
        sent_base: dict[int, int] = {}
        sent_emitted: dict[int, int] = {}
        sent_dur: dict[int, int] = {}
        cursor_ms = start_offset_ms  # 句级全局游标（跨调用延续）
        first = True
        t0 = time.perf_counter()

        try:
            ws, reused = await self._session.get()
            stats.reused_connection = reused

            await ws.send(json.dumps(run_task))  # type: ignore[attr-defined]
            await ws.send(json.dumps(cont_task))  # type: ignore[attr-defined]
            await ws.send(json.dumps(fin_task))  # type: ignore[attr-defined]

            while True:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=timeout_s)  # type: ignore[attr-defined]
                except asyncio.TimeoutError as exc:
                    raise TtsError("TTS_TIMEOUT", f"等待合成结果超过 {timeout_s}s", retryable=True) from exc

                if isinstance(msg, bytes):
                    if stats.first_packet_ms is None:
                        stats.first_packet_ms = int((time.perf_counter() - t0) * 1000)
                    offset = start_offset_ms + int(stats.audio_bytes / BYTES_PER_MS)
                    stats.audio_bytes += len(msg)
                    chunk = TtsChunk(audio=msg, offset_ms=offset, words=pending_words, is_first=first)
                    pending_words = []
                    first = False
                    yield chunk
                    continue

                try:
                    evt = json.loads(msg)
                except json.JSONDecodeError:
                    continue
                header = evt.get("header") or {}
                evt_task = header.get("task_id")
                if evt_task is not None and evt_task != task_id:
                    continue  # 其他 task 的事件（串行使用下不会出现；见 _Session.use）
                name = header.get("event")

                if name == "result-generated":
                    sentence = (evt.get("payload", {}).get("output", {}) or {}).get("sentence") or {}
                    idx = sentence.get("index")
                    raw_words = sentence.get("words") or []
                    if idx is not None and raw_words:
                        if idx not in sent_base:
                            sent_base[idx] = cursor_ms
                            sent_emitted[idx] = 0
                        dur = max(
                            (int(w["end_time"]) for w in raw_words if w.get("end_time") is not None),
                            default=0,
                        )
                        sent_dur[idx] = max(sent_dur.get(idx, 0), dur)
                        cursor_ms = sent_base[idx] + sent_dur[idx]

                        full = _parse_words(sentence, sent_base[idx])
                        new_words = full[sent_emitted[idx]:]
                        sent_emitted[idx] = len(full)
                        if new_words:
                            pending_words.extend(new_words)
                            stats.words.extend(new_words)
                elif name == "task-failed":
                    detail = json.dumps(evt.get("header", {}), ensure_ascii=False)
                    self._session.invalidate()
                    raise TtsError("TTS_ERROR", f"合成任务失败: {detail[:200]}", retryable=True)
                elif name == "task-finished":
                    break
        except TtsError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._session.invalidate()
            raise TtsError("TTS_ERROR", f"WebSocket 异常: {type(exc).__name__}: {exc}", retryable=True) from exc

        if pending_words:
            yield TtsChunk(audio=b"", offset_ms=start_offset_ms + int(stats.audio_bytes / BYTES_PER_MS), words=pending_words)

        stats.total_ms = int((time.perf_counter() - t0) * 1000)
        self.result = stats
        if stats.audio_bytes == 0:
            raise TtsError("TTS_ERROR", "未收到任何音频数据")
        if not stats.words:
            # SPEC §6: TTS_NO_TIMESTAMP 触发 Plan B（FunASR 字级时间戳对齐）
            raise TtsError("TTS_NO_TIMESTAMP", f"{self.model} 未返回 word 级时间戳")
