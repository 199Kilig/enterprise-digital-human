"""API 路由骨架（P1）：会话管理 + 打断 + SSE 流（对齐 SPEC §5/§3）

P1 定位：链路骨架，不接真实模型。SSE /chat/stream 为占位流（thinking → done），
asr/brain/tts/lip 模块待 V-01~V-05 验证后按 SPEC §2.1 接入。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Dict, Optional

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from api.state_machine import SessionStateMachine, StateEvent, StateMachineError
from schemas import SessionContext, SessionState

router = APIRouter(prefix="/api/v1")

app = FastAPI(title="企业级数字人 backend（P1 骨架）")
app.include_router(router)

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


# ---- SSE 事件流（SPEC §3，P1 占位）----
@router.get("/chat/stream")
def chat_stream(session_id: str) -> StreamingResponse:
    """P1 占位：按状态机发 thinking → done，验证前端 SSE 消费链路。

    P2 起在此接入 ASR/Brain/TTS/Lip 流水线，按 SPEC §3.3 事件定义推送。
    """
    _get_session(session_id)  # 404 兜底

    async def gen():
        yield "event: thinking\ndata: {\"state\":\"thinking\",\"timestamp_ms\":0}\n\n"
        yield "event: done\ndata: {\"total_seq_audio\":0,\"total_seq_lip\":0,\"duration_ms\":0}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
