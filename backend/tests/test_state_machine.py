"""状态机单元测试：覆盖 DESIGN-打断时序与状态机转移表 §4.1 全部路径（含边界）"""
from __future__ import annotations

import pytest

from api.state_machine import (
    ACT_BUFFER_SPEECH,
    ACT_CLEANUP_PIPELINE,
    ACT_EMIT_DONE,
    ACT_EMIT_INTERRUPTED,
    ACT_FALLBACK_TTS,
    ACT_OPEN_NEW_STREAM,
    ACT_SEND_BRAIN,
    ACT_TRIGGER_TTS,
    SessionStateMachine,
    StateEvent,
    StateMachineError,
)
from schemas import SessionState


def make_machine() -> SessionStateMachine:
    return SessionStateMachine(session_id="test-session")


# ---- 初始状态 ----
def test_initial_state_is_listening():
    m = make_machine()
    assert m.state is SessionState.LISTENING


def test_reset_returns_to_listening():
    m = make_machine()
    m.handle(StateEvent.ASR_ENDPOINT)
    m.handle(StateEvent.LLM_FIRST_TOKEN)
    m.handle(StateEvent.VAD_INTERRUPT)
    assert m.state is SessionState.INTERRUPTED
    m.reset()
    assert m.state is SessionState.LISTENING


# ---- 正常流转 ----
def test_normal_flow_full_cycle():
    """LISTENING→THINKING→SPEAKING→LISTENING（无打断全流程）"""
    m = make_machine()
    assert m.handle(StateEvent.ASR_ENDPOINT) == (
        SessionState.THINKING,
        [ACT_SEND_BRAIN],
    )
    assert m.handle(StateEvent.LLM_FIRST_TOKEN) == (
        SessionState.SPEAKING,
        [ACT_TRIGGER_TTS],
    )
    assert m.handle(StateEvent.TTS_LIP_DONE) == (
        SessionState.LISTENING,
        [ACT_EMIT_DONE],
    )


def test_silence_timeout_fallback():
    """静音超时（>2s）兜底 → THINKING"""
    m = make_machine()
    assert m.handle(StateEvent.SILENCE_TIMEOUT) == (
        SessionState.THINKING,
        [ACT_SEND_BRAIN],
    )


@pytest.mark.parametrize("evt", [StateEvent.LLM_TIMEOUT, StateEvent.LLM_ERROR])
def test_llm_timeout_and_error_use_fallback_tts(evt):
    """LLM 超时/错误 → SPEAKING + 降级话术"""
    m = make_machine()
    m.handle(StateEvent.ASR_ENDPOINT)  # → THINKING
    new_state, actions = m.handle(evt)
    assert new_state is SessionState.SPEAKING
    assert ACT_FALLBACK_TTS in actions


# ---- 打断（SPEAKING → INTERRUPTED → LISTENING）----
def test_interrupt_full_path():
    m = make_machine()
    m.handle(StateEvent.ASR_ENDPOINT)
    m.handle(StateEvent.LLM_FIRST_TOKEN)  # → SPEAKING
    new_state, actions = m.handle(StateEvent.VAD_INTERRUPT)
    assert new_state is SessionState.INTERRUPTED
    assert actions == [ACT_EMIT_INTERRUPTED, ACT_CLEANUP_PIPELINE]
    new_state, actions = m.handle(StateEvent.INTERRUPT_DONE)
    assert new_state is SessionState.LISTENING
    assert actions == [ACT_OPEN_NEW_STREAM]


def test_interrupt_ignored_while_interrupted():
    """INTERRUPTED 期间新打断信号被忽略（转移表 ❌ 行）"""
    m = make_machine()
    m.handle(StateEvent.ASR_ENDPOINT)
    m.handle(StateEvent.LLM_FIRST_TOKEN)
    m.handle(StateEvent.VAD_INTERRUPT)  # → INTERRUPTED
    state, actions = m.handle(StateEvent.VAD_INTERRUPT)
    assert state is SessionState.INTERRUPTED
    assert actions == []


def test_user_speech_buffered_while_speaking():
    """SPEAKING 中 VAD 未触发的语音分片 → 缓存 pre_interrupt_buffer（⚠️ 行）"""
    m = make_machine()
    m.handle(StateEvent.ASR_ENDPOINT)
    m.handle(StateEvent.LLM_FIRST_TOKEN)  # → SPEAKING
    state, actions = m.handle(StateEvent.USER_SPEECH)
    assert state is SessionState.SPEAKING
    assert actions == [ACT_BUFFER_SPEECH]


# ---- 非法转移 / 不可恢复错误 ----
def test_illegal_transition_raises():
    m = make_machine()  # LISTENING 下 LLM 事件非法
    with pytest.raises(StateMachineError):
        m.handle(StateEvent.LLM_FIRST_TOKEN)


@pytest.mark.parametrize(
    "setup",
    [
        "initial",
        "thinking",
        "speaking",
        "interrupted",
    ],
)
def test_fatal_error_from_any_state(setup):
    """任何状态 + FATAL_ERROR → ERROR"""
    m = make_machine()
    if setup == "thinking":
        m.handle(StateEvent.ASR_ENDPOINT)
    elif setup == "speaking":
        m.handle(StateEvent.ASR_ENDPOINT)
        m.handle(StateEvent.LLM_FIRST_TOKEN)
    elif setup == "interrupted":
        m.handle(StateEvent.ASR_ENDPOINT)
        m.handle(StateEvent.LLM_FIRST_TOKEN)
        m.handle(StateEvent.VAD_INTERRUPT)
    state, actions = m.handle(StateEvent.FATAL_ERROR)
    assert state is SessionState.ERROR
    assert actions == []


# ---- 转移表自洽性（元测试）----
def test_transition_table_internal_consistency():
    """转移表结构自洽：allow=True 的转移目标合法且带动作；允许自转移需有副作用"""
    for from_state, events in SessionStateMachine.TRANSITIONS.items():
        assert from_state in SessionState
        for event, trans in events.items():
            assert isinstance(event, StateEvent)
            assert trans.to in SessionState
            if trans.allow:
                # 自转移（from == to）必须携带副作用动作，否则等于无意义循环
                assert trans.to is not from_state or trans.actions
            else:
                # 不允许的转移：目标必须等于源状态（忽略语义）
                assert trans.to is from_state
