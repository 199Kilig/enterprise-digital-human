"""API 路由骨架（P1）：会话管理 + 打断 + SSE 流（对齐 SPEC §5/§3）

P1 定位：链路骨架，不接真实模型。SSE /chat/stream 为占位流（thinking → done），
asr/brain/tts/lip 模块待 V-01~V-05 验证后按 SPEC §2.1 接入。
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, cast

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.state_machine import SessionStateMachine, StateEvent, StateMachineError
from asr.streaming import AsrError, StreamingAsr
from brain.llm import BrainError, DeepSeekBrain
from brain.segmenter import SentenceSplitter
from config import load_config
from constants import BYTES_PER_MS, bytes_to_ms
from lip import LipServiceError, build_lip_engine
from schemas import SessionContext, SessionState
from tts.cosyvoice import CosyVoiceTts, TtsError

router = APIRouter(prefix="/api/v1")

app = FastAPI(title="企业级数字人 backend（P1 骨架）")
# include_router 统一放在文件末尾（所有 @router.xxx 之后）。
# 本版本（FastAPI 0.141.1）的 include_router 是**惰性**的（app.routes 里是一个 `_IncludedRouter`，
# 请求时才解析），所以放前面也能用；但老版本是快照式（注册那刻 router 里有什么就是什么），
# 放末尾对两种语义都成立，且 app.routes 不直接可见路由（见 tests/test_api_app.py 用 openapi 断言）。

# 对话大脑 + 语音合成单例（config.yaml 驱动；密钥经 src/config.py 从 env/.env 注入）
_brain = DeepSeekBrain()
_tts = CosyVoiceTts()

# 口型引擎：ADR-001 双环境分工 —— 真实推理在云 GPU（HTTP），本机只编排。
# lip.mock=true 时退化为占位帧（无卡/无隧道联调用）。
_CFG = load_config()
_lip = build_lip_engine(_CFG)
_LIP_CFG = _CFG.get("lip") or {}
_LIP_ON = not bool(_LIP_CFG.get("mock", True))
_LIP_FPS = int(_LIP_CFG.get("fps", 25))
# ADR-006：口型按音频**分片**送推理（不是整句一次），以缩小音画偏差。
# 16kHz 单声道 PCM16 = 32 字节/毫秒
_LIP_CHUNK_MS = int(_LIP_CFG.get("chunk_ms", 1000))
_LIP_CHUNK_BYTES = max(1, _LIP_CHUNK_MS) * BYTES_PER_MS  # 16k PCM16 = 32 B/ms（constants）
_LIP_MIN_TAIL_MS = int(_LIP_CFG.get("min_tail_ms", 200))
_LIP_MIN_TAIL_BYTES = max(1, _LIP_MIN_TAIL_MS) * BYTES_PER_MS
# 口型输入队列限长（config.yaml lip.queue_max）。满则丢最旧，见 gen() 里的 lip_put
_LIP_QUEUE_MAX = int(_LIP_CFG.get("queue_max", 8))
# 逐片诊断日志（默认关）：排查音画偏差时设 LIP_DEBUG=1，会打印每片的派发/返回时刻
_LIP_DEBUG = os.environ.get("LIP_DEBUG", "") == "1"

# 流水线重叠开关（config.yaml pipeline.overlap）：false 时退化为串行（整段回答后合成），
# 保留该开关是为了 A/B 对照——"优化了多少"必须能测，不能靠感觉（见 eval/verify_e2e_latency.py）
_OVERLAP = bool((load_config().get("pipeline") or {}).get("overlap", True))
_SERIAL_LIMIT = 10**9  # 串行模式下让切句器永不切分 = 等完整回答

# P1：内存会话存储（单机演示；多实例需外部存储，非本项目范围）
_sessions: Dict[str, SessionContext] = {}
_machines: Dict[str, SessionStateMachine] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_session(session_id: str) -> SessionContext:
    ctx = _sessions.get(session_id)
    if ctx is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "SESSION_NOT_FOUND", "message": f"session {session_id} 不存在", "session_id": session_id},
        )
    return ctx


def _get_machine(session_id: str) -> SessionStateMachine:
    m = _machines.get(session_id)
    if m is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "SESSION_NOT_FOUND", "message": f"session {session_id} 不存在", "session_id": session_id},
        )
    return m


# ---- 会话生命周期（SPEC §5.1 / §5.3）----
@router.post("/session")
def create_session(user_id: Optional[str] = None) -> dict:
    session_id = uuid.uuid4().hex
    now = _now()
    _sessions[session_id] = SessionContext(
        session_id=session_id,
        state=SessionState.LISTENING,
        created_at=now,
        last_active=now,
    )
    _machines[session_id] = SessionStateMachine(session_id=session_id)
    return {"session_id": session_id, "created_at": now.isoformat()}


@router.get("/session/{session_id}")
def get_session(session_id: str) -> dict:
    """只读探测会话是否存活（前端刷新后恢复会话用）。

    为什么需要：会话是**进程内内存态**（P1），后端一重启旧 session_id 全部失效。
    前端从 localStorage 恢复前先探一次：
      - 命中 → 直接复用该 session → **后端 ctx.history 还在，多轮上下文不断**
      - 404 → 前端新建会话，但保留本地历史（仅可查看，LLM 上下文从零开始）
    """
    ctx = _get_session(session_id)
    machine = _get_machine(session_id)
    return {
        "session_id": session_id,
        "state": machine.state.value,
        "created_at": ctx.created_at.isoformat(),
        "history_len": len(ctx.history),
    }


@router.delete("/session/{session_id}")
def close_session(session_id: str) -> dict:
    _get_session(session_id)  # 404 兜底
    _sessions.pop(session_id, None)
    _machines.pop(session_id, None)
    _asr_sessions.pop(session_id, None)
    return {"status": "closed"}


# ---- 打断（SPEC §5.2 / §5.2.1）----
@router.post("/session/{session_id}/interrupt")
def interrupt(session_id: str) -> dict:
    """VAD 打断信号（等价于自动打断）。仅在 SPEAKING 生效；其他状态幂等忽略。"""
    machine = _get_machine(session_id)
    ctx = _get_session(session_id)
    try:
        new_state, actions = machine.handle(StateEvent.VAD_INTERRUPT)
    except StateMachineError:
        # 转移表未定义该转移（如 LISTENING/THINKING 阶段收到打断）：对外幂等忽略
        return {"status": "ignored", "state": machine.state.value}
    if not actions:
        # 转移表定义了该转移但 allow=False（INTERRUPTED 期间重复收到打断）：
        # 状态不转移、无动作下发。**必须与"打断成功"区分开**，否则前端无法判断
        # 本轮打断是否真的生效（DESIGN-打断 §4.1 ❌ 行；由 tests/test_interrupt_path.py 锁住）
        return {"status": "ignored", "state": machine.state.value}
    ctx.state = new_state
    ctx.last_active = _now()
    # 真正让数字人闭嘴：置位后 chat_stream 的生成器会停止产出 token / TTS / 口型。
    # 之前这里只改状态、把状态机返回的动作（ACT_EMIT_INTERRUPTED / ACT_CLEANUP_PIPELINE）丢掉，
    # 结果"打断"只有状态变了、音频照样放完 → 用户感受是"打不断"。
    ctx.stop_requested = True
    return {"status": "interrupted" if new_state is SessionState.INTERRUPTED else new_state.value}


@router.post("/session/{session_id}/interrupt_done")
def interrupt_done(session_id: str) -> dict:
    """前端播放清理完成确认 → INTERRUPTED → LISTENING"""
    machine = _get_machine(session_id)
    ctx = _get_session(session_id)
    try:
        new_state, actions = machine.handle(StateEvent.INTERRUPT_DONE)
    except StateMachineError:
        return {"status": "ignored", "state": machine.state.value}
    ctx.state = new_state
    ctx.last_active = _now()
    ctx.stop_requested = False  # 清理完成，为新轮次复位
    return {"status": "cleaned", "session_id": session_id}


# ---- 语音识别上行（SPEC §3.1 规定音频走独立端点；端点判定见 ADR-004 由前端做）----
class AsrChunkRequest(BaseModel):
    session_id: str
    audio_pcm_16k_b64: str = ""
    seq: int = 0
    end: bool = False  # 前端判定语句结束（静音/松手）时置 True


# 每会话一个流式识别器（P1 单机内存态；多实例需外部存储，非本项目范围）
_asr_sessions: Dict[str, StreamingAsr] = {}

# ASR 真人采集（`ASR_DEBUG=1` 启动时开）：把每次语音会话的**完整音频 + 识别文本**落盘，
# 供 `eval/verify_asr_mic_accuracy.py` 做真人麦克风准确率复盘。
# 为什么需要：固定测试集音频是 TTS 合成的（干净），识别全对；真实麦克风有噪声/口音/距离，
# "感受不好"必须用真实样本才能定位是模型精度还是参数问题。默认关闭，不落盘。
_ASR_DEBUG = os.environ.get("ASR_DEBUG", "") == "1"
_ASR_CAPTURE_DIR = Path(__file__).resolve().parents[2] / "eval" / "reports" / "asr_capture"
_asr_capture: Dict[str, bytearray] = {}


def _dump_asr_capture(session_id: str, pcm: bytes, text: str) -> None:
    """写 16k 单声道 wav + 同名 json。文件名前缀是时间戳，用于与念读顺序对齐。"""
    import wave as _wave

    try:
        _ASR_CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
        base = _ASR_CAPTURE_DIR / f"{time.strftime('%H%M%S')}_{session_id[:8]}"
        with _wave.open(str(base.with_suffix(".wav")), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(pcm)
        base.with_suffix(".json").write_text(
            json.dumps({"session_id": session_id, "text": text, "bytes": len(pcm),
                        "duration_s": round(len(pcm) / (BYTES_PER_MS * 1000), 2)},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001
        pass  # 采集失败绝不影响识别主流程


@router.post("/asr/chunk")
def asr_chunk(req: AsrChunkRequest) -> dict:
    """喂一段 16k/16bit 单声道 PCM（base64），返回增量与累计识别文本。

    端点检测不在后端（ADR-004）：前端静音检测决定何时 end=True，后端只做识别。
    """
    _get_session(req.session_id)

    asr = _asr_sessions.get(req.session_id)
    if asr is None or req.seq == 0:
        asr = StreamingAsr()  # seq=0 视为新语句起点
        _asr_sessions[req.session_id] = asr

    try:
        audio = base64.b64decode(req.audio_pcm_16k_b64) if req.audio_pcm_16k_b64 else b""
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=400, detail={"code": "ASR_ERROR", "message": f"audio_pcm_16k_b64 解码失败: {exc}"}
        ) from exc

    if _ASR_DEBUG and audio:
        _asr_capture.setdefault(req.session_id, bytearray()).extend(audio)

    try:
        chunk = asr.push(audio, is_last=req.end)
    except AsrError as exc:
        _asr_sessions.pop(req.session_id, None)
        raise HTTPException(status_code=503, detail={"code": exc.code, "message": exc.message}) from exc

    payload = {
        "seq": req.seq,
        "text": chunk.text,  # 本片增量（SPEC §2 AsrChunk.text 语义）
        "full_text": asr.full_text,  # 累计文本（前端实时显示用）
        "is_final": chunk.is_final,
        "start_ms": chunk.start_ms,
        "end_ms": chunk.end_ms,
    }
    if req.end:
        if _ASR_DEBUG:
            _dump_asr_capture(req.session_id, bytes(_asr_capture.pop(req.session_id, b"")), asr.full_text)
        _asr_sessions.pop(req.session_id, None)  # 语句结束即释放流式状态
    return payload


# ---- SSE 事件流（SPEC §3）----
# P1 扩展：text 查询参数为文本输入通道（SPEC §3.1 原设计音频走 WebRTC DataChannel；
# 语音上行未接入前，用 text 驱动同一事件流。属新增可选参数，不破坏既有契约）。
_BRAIN_SYSTEM_PROMPT = (
    "你是电商零售企业的实时客服数字人助手，名字叫小顾。"
    "用简体中文口语化回答，一次回复控制在两句话以内——你的回答会被实时合成语音并由数字人说出，"
    "过长会明显增加用户等待。"
    "涉及运费、退货、发货时效、商品参数等售前售后问题：不确定的政策绝不编造，"
    "明确说明需要人工客服进一步核实。"
)



def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.get("/chat/stream")
async def chat_stream(session_id: str, text: Optional[str] = None) -> StreamingResponse:
    """SSE 事件流。

    - 不带 text：P1 占位流（thinking → done），用于链路自检
    - 带 text：真实链路 —— 状态机转 THINKING → DeepSeek 流式产 token
      → **按句切分即刻送 TTS**（DESIGN §3.1 流水线并行，不等 LLM 说完）→ done

    流水线结构：LLM 生产者 → 句子队列 → TTS 消费者 → 音频队列 → SSE 输出。
    本生成器只做队列搬运，避免 TTS 合成阻塞文字事件下发（"边收边显示"要跟手）。
    """
    ctx = _get_session(session_id)
    machine = _get_machine(session_id)

    async def gen():
        t0 = time.perf_counter()
        ctx.stop_requested = False  # 新一轮：复位打断标志（上一轮已由 /interrupt_done 收尾）

        if not text:
            yield _sse("thinking", {"state": "thinking", "timestamp_ms": 0})
            # placeholder=true：这是"没带 text 的空跑流"（如前端 SSE 自动重连），
            # 不是一轮真实回答。前端据此忽略，避免把空 done 当成一轮结果处理。
            yield _sse("done", {"total_seq_audio": 0, "total_seq_lip": 0, "duration_ms": 0, "placeholder": True})
            return

        # ---- 1) 用户语音端点（文本等价）→ THINKING，走真实转移表 ----
        # 无 TTS 时上一轮会停在 THINKING（转移表没有 THINKING→LISTENING），新一轮开始前复位
        if machine.state is not SessionState.LISTENING:
            machine.reset()
        try:
            new_state, _actions = machine.handle(StateEvent.ASR_ENDPOINT)
        except StateMachineError:
            machine.reset()
            new_state, _actions = machine.handle(StateEvent.ASR_ENDPOINT)
        ctx.state = new_state
        ctx.last_active = _now()
        ctx.history.append({"role": "user", "content": text})
        yield _sse("thinking", {"state": "thinking", "timestamp_ms": int((time.perf_counter() - t0) * 1000)})

        # ---- 2) 流水线：LLM 生产者 → 句子队列 → TTS 消费者 → 音频队列 ----
        # DESIGN §3.1：各段流水线并行，全链路延迟不是各段串行累加。
        # 首句一到位即开始合成，因此"首次出声"可以早于 LLM 输出完整回答。
        messages = [{"role": "system", "content": _BRAIN_SYSTEM_PROMPT}] + ctx.history[-12:]

        token_q: asyncio.Queue = asyncio.Queue()
        sentence_q: asyncio.Queue = asyncio.Queue()
        audio_q: asyncio.Queue = asyncio.Queue()
        # 口型：整句 PCM → 云 GPU 推理 → 帧（ADR-001；SPEC §3.2 lip_frame 事件）
        # ⚠️ 限长 + 满则丢最旧（#6）：TTS 合成快于实时，而口型是单 GPU 串行推理，
        # 无界队列会让片段越积越多、严重滞后于音频时钟 → 音画同步直接崩
        # （RUNBOOK §3.14 的待验项：单片生成 >1000ms 就会出现积压）。
        # 丢旧留新：迟到的片段按音频时钟已播不出去（SPEC §3.5）——**丢帧好过整体延迟**。
        lip_in_q: asyncio.Queue = asyncio.Queue(maxsize=_LIP_QUEUE_MAX)

        async def lip_put(item: object) -> None:
            while lip_in_q.full():
                try:
                    dropped = lip_in_q.get_nowait()
                except asyncio.QueueEmpty:
                    break
                lip_info["dropped"] = int(lip_info.get("dropped", 0)) + 1
                if _LIP_DEBUG:
                    print(f"[lip] queue full -> drop {dropped[1] if dropped else None}ms", flush=True)
            await lip_in_q.put(item)
        lip_out_q: asyncio.Queue = asyncio.Queue()

        llm_info: Dict[str, object] = {"parts": [], "ttft_ms": None, "done_ms": None, "error": None}
        tts_info: Dict[str, object] = {"first_ms": None, "total_ms": 0, "words": 0, "error": None, "start_ms": None}
        lip_info: Dict[str, object] = {
            "frames": 0, "sentences": 0, "first_ms": None, "total_ms": 0,
            "gpu_mb": 0, "infer_fps": 0.0, "error": None, "transport": None, "bytes": 0,
            "dispatched": 0, "chunk_ms": _LIP_CHUNK_MS,
        }

        async def llm_producer() -> None:
            # overlap=false 时退化为串行：切句器不切分 → 只有整段回答才会送合成
            splitter = (
                SentenceSplitter()
                if _OVERLAP
                else SentenceSplitter(soft_limit=_SERIAL_LIMIT, hard_limit=_SERIAL_LIMIT)
            )
            try:
                async for chunk in _brain.stream_chat(messages):
                    if chunk.ttft_ms is not None:
                        llm_info["ttft_ms"] = chunk.ttft_ms
                        # 转移表：THINKING + LLM_FIRST_TOKEN → SPEAKING（动作=触发 TTS）
                        try:
                            ns, _ = machine.handle(StateEvent.LLM_FIRST_TOKEN)
                            ctx.state = ns
                        except StateMachineError:
                            pass
                    cast(list, llm_info["parts"]).append(chunk.token)
                    await token_q.put(chunk)
                    for sentence in splitter.feed(chunk.token):
                        await sentence_q.put(sentence)
                tail = splitter.flush()
                if tail:
                    await sentence_q.put(tail)
            except BrainError as exc:
                llm_info["error"] = exc
            finally:
                llm_info["done_ms"] = int((time.perf_counter() - t0) * 1000)
                await token_q.put(None)
                await sentence_q.put(None)

        async def tts_consumer() -> None:
            offset_ms = 0
            t_start = time.perf_counter()
            try:
                while True:
                    sentence = await sentence_q.get()
                    if sentence is None:
                        break
                    if tts_info["start_ms"] is None:
                        # 流水线重叠的真凭据：首句交给 TTS 的时刻（此刻 LLM 可能还在生成）
                        tts_info["start_ms"] = int((time.perf_counter() - t0) * 1000)
                    buf_start_ms = offset_ms  # 待发分片在音频时钟上的起点（口型片段排期用）
                    pcm_buf = bytearray()
                    async for chunk in _tts.stream(sentence, start_offset_ms=offset_ms):
                        if chunk.audio:
                            if tts_info["first_ms"] is None:
                                tts_info["first_ms"] = int((time.perf_counter() - t0) * 1000)
                            offset_ms = chunk.offset_ms + bytes_to_ms(len(chunk.audio))
                            pcm_buf.extend(chunk.audio)
                            # ADR-006：音频一到就按 ~chunk_ms 切片送口型，**不等整句合成完**。
                            # 依据：TTS 合成快于实时（实测 5.27s 出 7.28s 音频），分片送检才能让
                            # 口型片段在该播之前就绪；整句送检时首片段迟到 5.3s（见 ADR-006 实测表）。
                            while _LIP_ON and len(pcm_buf) >= _LIP_CHUNK_BYTES:
                                await lip_put((bytes(pcm_buf[:_LIP_CHUNK_BYTES]), buf_start_ms))
                                del pcm_buf[:_LIP_CHUNK_BYTES]
                                # 诊断日志（LIP_DEBUG=1 时开）：派发时刻 vs 音频时钟
                                if _LIP_DEBUG:
                                    print(
                                        f"[lip] dispatch start={buf_start_ms}ms "
                                        f"at_ms={int((time.perf_counter() - t0) * 1000)} audio_pos={offset_ms}ms",
                                        flush=True,
                                    )
                                buf_start_ms += _LIP_CHUNK_MS
                                lip_info["dispatched"] = int(lip_info["dispatched"]) + 1
                        tts_info["words"] = int(tts_info["words"]) + len(chunk.words)
                        await audio_q.put(chunk)
                    # 句尾残片：太短就不单独跑一次推理（不值得为几十毫秒占一次 GPU）
                    if _LIP_ON and len(pcm_buf) >= _LIP_MIN_TAIL_BYTES:
                        await lip_put((bytes(pcm_buf), buf_start_ms))
                        lip_info["dispatched"] = int(lip_info["dispatched"]) + 1
            except TtsError as exc:
                tts_info["error"] = exc
            finally:
                tts_info["total_ms"] = int((time.perf_counter() - t_start) * 1000)
                await audio_q.put(None)
                await lip_put(None)  # 哨兵也走同一 helper：满则先丢最旧腾位置

        async def lip_worker() -> None:
            """整句 PCM → 云 GPU 口型服务 → 帧序列（ADR-001：推理在云，编排在本机）。

            串行执行：服务端是单 GPU 单实例，并发调用只会互相拖慢（实测见 RUNBOOK 坑 15）。
            阻塞式 HTTP 调用放 to_thread，避免卡住事件循环。
            """
            t_start = time.perf_counter()
            try:
                while True:
                    item = await lip_in_q.get()
                    if item is None:
                        break
                    pcm, sent_offset = item
                    t_recv = time.perf_counter()
                    try:
                        res = await asyncio.to_thread(_lip.infer, pcm, [], _LIP_FPS, session_id)
                    except LipServiceError as exc:
                        lip_info["error"] = str(exc)
                        continue
                    if _LIP_DEBUG:
                        print(
                            f"[lip] done start={sent_offset}ms recv_ms={int((t_recv - t0) * 1000)} "
                            f"call_ms={res['wall_ms']} emit_ms={int((time.perf_counter() - t0) * 1000)} "
                            f"qsize={lip_in_q.qsize()}",
                            flush=True,
                        )
                    lip_info["sentences"] = int(lip_info["sentences"]) + 1
                    lip_info["frames"] = int(lip_info["frames"]) + int(res.get("n_frames", 0))
                    lip_info["gpu_mb"] = res["gpu_memory_mb"]
                    lip_info["infer_fps"] = res["infer_fps"]
                    lip_info["transport"] = res.get("transport", "frames")
                    lip_info["bytes"] = int(lip_info["bytes"]) + (
                        len(res["video"]) if res.get("transport") == "h264" else
                        sum(len(f.frame) for f in res.get("frames", []))
                    )
                    if lip_info["first_ms"] is None:
                        lip_info["first_ms"] = int((time.perf_counter() - t0) * 1000)
                    await lip_out_q.put((sent_offset, res))
            finally:
                lip_info["total_ms"] = int((time.perf_counter() - t_start) * 1000)
                await lip_out_q.put(None)

        llm_task = asyncio.create_task(llm_producer())
        tts_task = asyncio.create_task(tts_consumer())
        lip_task = asyncio.create_task(lip_worker()) if _LIP_ON else None

        seq = 0
        audio_seq = 0
        lip_seq = 0
        token_done = False
        audio_done = False
        lip_done = not _LIP_ON
        try:
            while not (token_done and audio_done and lip_done):
                # 打断检查：/interrupt 置位后立即停止产出（不再收 token / 合成 TTS / 送口型）。
                # 放在循环最前，是为了让打断的最坏延迟 = 一次循环周期（5ms），而不是等本轮跑完。
                if ctx.stop_requested:
                    break
                # 文字优先下发：保证"边收边显示"跟手，不被合成阻塞
                while not token_done:
                    try:
                        item = token_q.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if item is None:
                        token_done = True
                        break
                    yield _sse(
                        "brain_token",
                        {
                            "seq": seq,
                            "token": item.token,
                            "ttft_ms": item.ttft_ms if seq == 0 else None,
                            "at_ms": int((time.perf_counter() - t0) * 1000),
                        },
                    )
                    seq += 1

                while not audio_done:
                    try:
                        chunk = audio_q.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if chunk is None:
                        audio_done = True
                        break
                    if chunk.audio:
                        yield _sse(
                            "tts_audio",
                            {
                                "seq": audio_seq,
                                "audio_b64": base64.b64encode(chunk.audio).decode("ascii"),
                                "start_ms": chunk.offset_ms,
                                "end_ms": chunk.offset_ms + bytes_to_ms(len(chunk.audio)),
                                "words": [[w[0], w[1], w[2]] for w in chunk.words],
                                "sample_rate": 16000,
                            },
                        )
                        audio_seq += 1
                    elif chunk.words:
                        yield _sse(
                            "tts_audio",
                            {
                                "seq": audio_seq,
                                "audio_b64": "",
                                "start_ms": chunk.offset_ms,
                                "end_ms": chunk.offset_ms,
                                "words": [[w[0], w[1], w[2]] for w in chunk.words],
                                "sample_rate": 16000,
                            },
                        )
                        audio_seq += 1

                while not lip_done:
                    try:
                        lip_item = lip_out_q.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if lip_item is None:
                        lip_done = True
                        break
                    sent_offset, res = lip_item
                    if res.get("transport") == "h264":
                        # ADR-005 默认：整句 H.264 片段，前端按 start_ms 排队播放
                        yield _sse(
                            "lip_video",
                            {
                                "seq": lip_seq,
                                "video_b64": base64.b64encode(res["video"]).decode("ascii"),
                                "start_ms": sent_offset,
                                "duration_ms": res.get("duration_ms", 0),
                                "n_frames": res.get("n_frames", 0),
                                "fps": res.get("fps", _LIP_FPS),
                                "sentence_offset_ms": sent_offset,
                                "at_ms": int((time.perf_counter() - t0) * 1000),
                            },
                        )
                        lip_seq += 1
                        continue
                    for f in res.get("frames", []):
                        yield _sse(
                            "lip_frame",
                            {
                                "seq": lip_seq,
                                "frame_b64": base64.b64encode(f.frame).decode("ascii"),
                                "pts_ms": sent_offset + f.pts_ms,
                                "frame_idx": f.frame_idx,
                                "sentence_offset_ms": sent_offset,
                                "fps": _LIP_FPS,
                                "at_ms": int((time.perf_counter() - t0) * 1000),
                            },
                        )
                        lip_seq += 1

                if token_done and audio_done and lip_done:
                    break
                # 让出事件循环给生产者任务（两个队列都空时的唯一等待点）
                await asyncio.sleep(0.005)
        finally:
            pending = [t for t in (llm_task, tts_task, lip_task) if t is not None and not t.done()]
            for t in pending:
                t.cancel()
            if pending:
                # 只 cancel 不 await → CancelledError 无人接管（"Task was destroyed but it is
                # pending" 警告），且子任务 finally 里的队列收尾可能被跳过。
                try:
                    await asyncio.gather(*pending, return_exceptions=True)
                except Exception:  # noqa: BLE001
                    pass

        if ctx.stop_requested:
            # 被打断：**不发 done**。状态机此刻是 INTERRUPTED，若继续往下走
            # TTS_LIP_DONE 转移会抛错并被 reset 成 LISTENING，反而破坏打断流程
            # （INTERRUPTED → LISTENING 只能由 /interrupt_done 驱动）。
            return

        # ---- 3) 错误与收尾 ----
        llm_err = llm_info["error"]
        tts_err = tts_info["error"]
        if llm_err is not None or tts_err is not None:
            machine.handle(StateEvent.FATAL_ERROR)
            ctx.state = machine.state
            err = llm_err if llm_err is not None else tts_err
            yield _sse("error", {"code": getattr(err, "code", "ERROR"), "message": getattr(err, "message", str(err))})
            return

        parts = cast(list, llm_info["parts"])
        answer = "".join(parts)
        if answer:
            ctx.history.append({"role": "assistant", "content": answer})
        ttft_ms = llm_info["ttft_ms"]
        llm_done_ms = llm_info["done_ms"]
        tts_first_ms = tts_info["first_ms"]
        tts_total_ms = tts_info["total_ms"]
        words_total = tts_info["words"]

        # ---- 4) 结束：TTS_LIP_DONE → LISTENING（走转移表，非直接赋值）----
        try:
            new_state, _ = machine.handle(StateEvent.TTS_LIP_DONE)
            ctx.state = new_state
        except StateMachineError:
            machine.reset()
            ctx.state = machine.state

        yield _sse(
            "done",
            {
                "total_seq_audio": audio_seq,
                "total_seq_lip": lip_seq,
                "duration_ms": int((time.perf_counter() - t0) * 1000),
                "lip_frames": lip_info["frames"],
                "lip_sentences": lip_info["sentences"],
                "lip_first_ms": lip_info["first_ms"],
                "lip_total_ms": lip_info["total_ms"],
                "lip_infer_fps": lip_info["infer_fps"],
                "lip_gpu_memory_mb": lip_info["gpu_mb"],
                "lip_transport": lip_info["transport"],
                "lip_bytes": lip_info["bytes"],
                "lip_chunks": lip_info["dispatched"],
                "lip_dropped": lip_info.get("dropped", 0),  # 队列背压丢掉的片段数（>0 说明口型跟不上）
                "lip_chunk_ms": lip_info["chunk_ms"],
                "lip_error": lip_info["error"],
                "ttft_ms": ttft_ms,
                "tokens": len(parts),
                "tts_first_packet_ms": tts_first_ms,
                "tts_total_ms": tts_total_ms,
                "tts_words": words_total,
                # 流水线重叠证据（DESIGN §3.1）：首句送合成的时刻是否早于 LLM 输出完成
                "llm_done_ms": llm_done_ms,
                "tts_start_ms": tts_info["start_ms"],
                "first_audio_ms": tts_first_ms,
                "overlapped": tts_info["start_ms"] is not None
                and llm_done_ms is not None
                and tts_info["start_ms"] < llm_done_ms,
                "state_after": machine.state.value,
                "answer": answer,
            },
        )

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---- 只读数据端点：前端看板/台账的真数据源 ----
# 设计原则：报告里没有的字段一律返回 null（status=pending），不编造数字。
_REPO_ROOT = Path(__file__).resolve().parents[3]
_REPORTS_DIR = _REPO_ROOT / "backend" / "eval" / "reports"
_LEDGER_FILE = _REPO_ROOT / "docs" / "eval-history.md"


def _load_report(name: str) -> Optional[dict]:
    path = _REPORTS_DIR / name
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _dig(data: Optional[dict], dotted: str):
    cur = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


# (维度, 指标名, 报告文件, 取值路径, 目标, 比较方向, 场景/口径, 环境)
# 目标值来自 DESIGN §5.1 / PRD §3（引用不复制数值口径，此处仅为前端着色所需）
_METRIC_DEFS = [
    ("延迟", "ASR 单块处理", "funasr_streaming.json", "first_word_wall_ms", 300, "lte", "600ms/块流式识别，CPU", "本机"),
    ("延迟", "LLM 首 token（单次）", "llm_streaming.json", "ttft_ms", 800, "lte", "报告记单次 503ms；台账记 3 次均值 ~740ms（503~1108 波动）；报告自身对旧目标 500ms 判 FAILED", "本机"),
    ("延迟", "TTS 首包", "cosyvoice_timestamp.json", "first_packet_ms", 300, "lte", "首包即播（V-02）", "云 GPU/本机"),
    ("延迟", "口型首帧", "musetalk_validation.json", "realtime_run.metrics.first_frame_ms", 400, "lte", "MuseTalk 首帧（待插桩）", "云 GPU"),
    ("延迟", "端到端（首包）", "e2e_mock.json", "e2e_ms", 2000, "lt", "用户说完→首包（V-06）", "云 GPU"),
    ("音画同步", "时差测量误差", "sync_measure.json", "max_error_ms", 50, "lte", "自校准：互相关测量误差（非最终时差）", "本机"),
    ("音画同步", "打断响应", "interrupt_measure.json", "interrupt_ms", 200, "lte", "插话→数字人停止（V-06）", "云 GPU"),
    ("质量", "口型人工评分", "quality_score.json", "score", 4, "gte", "1-5 分，固定抽查 5 条", "人工"),
    ("稳定性", "连续对话时长", "stability.json", "duration_min", 30, "gte", "30 分钟不掉帧（P4）", "云 GPU"),
    ("并发", "单卡路数", "concurrency.json", "lanes", None, None, "云 GPU 并发压测（P3）", "云 GPU"),
    ("资源", "峰值显存", "musetalk_validation.json", "metrics.peak_vram_mib", None, None, "MuseTalk v1.0 离线推理", "云 GPU 4090"),
    ("资源", "实时吞吐", "musetalk_validation.json", "realtime_run.metrics.steady_throughput_fps", 25, "gte", "realtime 模式稳态（25fps 为目标）", "云 GPU 4090"),
]

_UNITS = {
    "ASR 单块处理": "ms",
    "LLM 首 token（单次）": "ms",
    "TTS 首包": "ms",
    "口型首帧": "ms",
    "端到端（首包）": "ms",
    "时差测量误差": "ms",
    "打断响应": "ms",
    "口型人工评分": "分",
    "连续对话时长": "min",
    "单卡路数": "路",
    "峰值显存": "MiB",
    "实时吞吐": "fps",
}


def _status_of(value, target, direction) -> tuple[str, str | None]:
    if value is None:
        return "pending", None
    if target is None or direction is None:
        return "ok", None
    ok = value <= target if direction == "lte" else (value < target if direction == "lt" else value >= target)
    if ok:
        return "ok", f"{'≤' if direction in ('lte', 'lt') else '≥'}{target}"
    return ("warn" if direction == "gte" else "fail"), f"{'≤' if direction in ('lte', 'lt') else '≥'}{target}"


@router.get("/health")
def health() -> dict:
    """链路健康：前端顶栏与工作台用（只读，不做写操作）。"""
    import urllib.error
    import urllib.request

    lip_url = _LIP_CFG.get("service_url", "http://127.0.0.1:8002")
    reachable, mode, detail = False, ("mock（占位帧，未接云 GPU）" if not _LIP_ON else "musetalk（云 GPU）"), None
    try:
        # 用 /health 而不是 /docs：/docs 只是文档页，不代表模型已加载
        with urllib.request.urlopen(f"{lip_url}/health", timeout=1.5) as resp:  # noqa: S310
            reachable = resp.status == 200
            if reachable and _LIP_ON:
                info = json.loads(resp.read().decode("utf-8"))
                # 诚实标注：连的是真云 GPU 还是 fake 回放（eval/fake_lip_server.py）
                # —— 否则界面会把"回放固定素材"显示成"云 GPU 实时推理"
                if str(info.get("version", "")).lower() in ("fake", "mock") or info.get("device") == "cpu":
                    mode = "FAKE 回放（协议联调，非真实推理）"
                detail = {
                    "device": info.get("device"),
                    "avatar": info.get("avatar"),
                    "service_version": info.get("version"),
                    "transport_default": info.get("transport_default"),
                    "frames_cached": info.get("frames_cached"),
                    "gpu_free_mb": info.get("gpu_free_mb"),
                    "gpu_total_mb": info.get("gpu_total_mb"),
                }
    except (urllib.error.URLError, OSError, ValueError):
        reachable = False

    return {
        "api": "up",
        "lip_service": {"url": lip_url, "reachable": reachable, "mode": mode, "detail": detail},
        "reports_present": len(list(_REPORTS_DIR.glob("*.json"))) if _REPORTS_DIR.exists() else 0,
        "sessions_active": len(_sessions),
    }


@router.get("/metrics")
def metrics() -> dict:
    """五维指标（PRD §3）+ 延迟预算（DESIGN §5.1），数据源 = eval/reports/*.json。"""
    rows = []
    for dim, name, fname, path, target, direction, scope, env in _METRIC_DEFS:
        report = _load_report(fname)
        raw = _dig(report, path)
        value = raw if isinstance(raw, (int, float)) else None
        status, target_str = _status_of(value, target, direction)
        unit = _UNITS.get(name, "")
        rows.append(
            {
                "dimension": dim,
                "metric": name,
                "value": f"{value:g} {unit}".strip() if value is not None else None,
                "value_raw": value,
                "target": target_str,
                "status": status,
                "scenario": scope,
                "env": env,
                "source": f"backend/eval/reports/{fname}" if report else None,
                "measuredAt": (report or {}).get("date"),
            }
        )

    # 延迟瀑布图专用（前端按此渲染 span；未测段保持 null）
    musetalk = _load_report("musetalk_validation.json") or {}
    budget = [
        {
            "key": "asr",
            "label": "ASR 首字",
            "measured_ms": _dig(_load_report("funasr_streaming.json"), "first_word_wall_ms"),
            "target_ms": 300,
            "note": "V-03 实测：600ms/块粒度下首字 166ms；端到端首字≈766ms",
        },
        {
            "key": "llm",
            "label": "LLM 首 token",
            "measured_ms": _dig(_load_report("llm_streaming.json"), "ttft_ms"),
            "target_ms": 800,
            "note": "V-04 实测：503~1108ms 波动，均值 ~740ms",
        },
        {
            "key": "tts",
            "label": "TTS 首包",
            "measured_ms": _dig(_load_report("cosyvoice_timestamp.json"), "first_packet_ms"),
            "target_ms": 300,
            "note": "V-02 未执行（DashScope key 未配置）",
        },
        {
            "key": "lip",
            "label": "口型首帧",
            "measured_ms": _dig(musetalk, "realtime_run.metrics.first_frame_ms"),
            "target_ms": 400,
            "note": "V-01 realtime 已测吞吐（19.07fps/52.4ms 每帧），首帧需插桩",
        },
        {
            "key": "e2e",
            "label": "端到端（首包）",
            "measured_ms": _dig(_load_report("e2e_mock.json"), "e2e_ms"),
            "target_ms": 2000,
            "note": "V-06 未执行（全链路联调后测）",
        },
    ]

    return {
        "generated_at": _now().isoformat(),
        "reports_dir": str(_REPORTS_DIR),
        "reports_found": sorted(p.name for p in _REPORTS_DIR.glob("*.json")) if _REPORTS_DIR.exists() else [],
        "rows": rows,
        "latency_budget": budget,
        "runtime": {
            "gpu": musetalk.get("env", {}).get("gpu"),
            "image": musetalk.get("env", {}).get("image"),
            "output_resolution": (musetalk.get("tasks") or [{}])[0].get("resolution"),
            "output_fps": (musetalk.get("tasks") or [{}])[0].get("fps"),
            "peak_vram_mib": _dig(musetalk, "metrics.peak_vram_mib"),
            "realtime_fps": _dig(musetalk, "realtime_run.metrics.steady_throughput_fps"),
            "realtime_rtf": _dig(musetalk, "realtime_run.metrics.rtf"),
        },
    }


@router.get("/eval/ledger")
def eval_ledger() -> dict:
    """解析 docs/eval-history.md 的指标表（PRD FR-08 同口径回归台账）。"""
    if not _LEDGER_FILE.exists():
        raise HTTPException(status_code=404, detail={"code": "LEDGER_NOT_FOUND", "message": "eval-history.md 不存在"})

    rows: list[dict] = []
    for line in _LEDGER_FILE.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 6 or cells[0] in ("日期", "维度") or set(cells[0]) <= {"-", " "}:
            continue
        if cells[1] in ("—", "-", ""):  # 台账开档等占位行
            continue
        # 表1：日期|指标|数值|口径/场景|环境|来源   表2：维度|指标|数值|口径|环境|来源
        rows.append(
            {
                "date": cells[0],
                "metric": cells[1],
                "value": cells[2],
                "scope": cells[3],
                "env": cells[4],
                "source": cells[5],
            }
        )

    return {
        "generated_at": _now().isoformat(),
        "source_file": "docs/eval-history.md",
        "rows": rows,
    }


# 统一在文件末尾注册（对 include_router 的惰性/快照两种语义都成立）
app.include_router(router)
