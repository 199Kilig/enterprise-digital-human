"""API 路由骨架（P1）：会话管理 + 打断 + SSE 流（对齐 SPEC §5/§3）

P1 定位：链路骨架，不接真实模型。SSE /chat/stream 为占位流（thinking → done），
asr/brain/tts/lip 模块待 V-01~V-05 验证后按 SPEC §2.1 接入。
"""
from __future__ import annotations

import asyncio
import base64
import json
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
from lip import LipServiceError, build_lip_engine
from schemas import SessionContext, SessionState
from tts.cosyvoice import CosyVoiceTts, TtsError

router = APIRouter(prefix="/api/v1")

app = FastAPI(title="企业级数字人 backend（P1 骨架）")
app.include_router(router)

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
    ctx.state = new_state
    ctx.last_active = _now()
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
    return {"status": "cleaned", "session_id": session_id}


# ---- 语音识别上行（SPEC §3.1 规定音频走独立端点；端点判定见 ADR-004 由前端做）----
class AsrChunkRequest(BaseModel):
    session_id: str
    audio_pcm_16k_b64: str = ""
    seq: int = 0
    end: bool = False  # 前端判定语句结束（静音/松手）时置 True


# 每会话一个流式识别器（P1 单机内存态；多实例需外部存储，非本项目范围）
_asr_sessions: Dict[str, StreamingAsr] = {}


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

        if not text:
            yield _sse("thinking", {"state": "thinking", "timestamp_ms": 0})
            yield _sse("done", {"total_seq_audio": 0, "total_seq_lip": 0, "duration_ms": 0})
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
        lip_in_q: asyncio.Queue = asyncio.Queue()
        lip_out_q: asyncio.Queue = asyncio.Queue()

        llm_info: Dict[str, object] = {"parts": [], "ttft_ms": None, "done_ms": None, "error": None}
        tts_info: Dict[str, object] = {"first_ms": None, "total_ms": 0, "words": 0, "error": None, "start_ms": None}
        lip_info: Dict[str, object] = {
            "frames": 0, "sentences": 0, "first_ms": None, "total_ms": 0,
            "gpu_mb": 0, "infer_fps": 0.0, "error": None, "transport": None, "bytes": 0,
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
                    sent_offset = offset_ms  # 本句在整段回答中的起点 = 口型帧的时钟基准
                    pcm_buf = bytearray()
                    async for chunk in _tts.stream(sentence, start_offset_ms=offset_ms):
                        if chunk.audio:
                            if tts_info["first_ms"] is None:
                                tts_info["first_ms"] = int((time.perf_counter() - t0) * 1000)
                            offset_ms = chunk.offset_ms + int(len(chunk.audio) / 32)  # 32 B/ms
                            pcm_buf.extend(chunk.audio)
                        tts_info["words"] = int(tts_info["words"]) + len(chunk.words)
                        await audio_q.put(chunk)
                    # 整句 PCM 收齐才交口型：口型推理以句为单位（不能按 TTS 分片喂）
                    if _LIP_ON and pcm_buf:
                        await lip_in_q.put((bytes(pcm_buf), sent_offset))
            except TtsError as exc:
                tts_info["error"] = exc
            finally:
                tts_info["total_ms"] = int((time.perf_counter() - t_start) * 1000)
                await audio_q.put(None)
                await lip_in_q.put(None)

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
                    try:
                        res = await asyncio.to_thread(_lip.infer, pcm, [], _LIP_FPS, session_id)
                    except LipServiceError as exc:
                        lip_info["error"] = str(exc)
                        continue
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
                                "end_ms": chunk.offset_ms + int(len(chunk.audio) / 32),
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
            for task in (llm_task, tts_task, lip_task):
                if task is not None and not task.done():
                    task.cancel()

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

    lip_url = _LIP_CFG.get("service_url", "http://localhost:8002")
    reachable, mode, detail = False, ("mock（占位帧，未接云 GPU）" if not _LIP_ON else "musetalk（云 GPU）"), None
    try:
        # 用 /health 而不是 /docs：/docs 只是文档页，不代表模型已加载
        with urllib.request.urlopen(f"{lip_url}/health", timeout=1.5) as resp:  # noqa: S310
            reachable = resp.status == 200
            if reachable and _LIP_ON:
                info = json.loads(resp.read().decode("utf-8"))
                detail = {
                    "device": info.get("device"),
                    "avatar": info.get("avatar"),
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
