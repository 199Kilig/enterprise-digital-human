"""会话状态机：实现状态转移表（DESIGN-打断时序与状态机转移表 §4.1）

五态：LISTENING / THINKING / SPEAKING / INTERRUPTED / ERROR
事件驱动，纯逻辑（不执行模型调用）。`handle()` 返回 (新状态, 动作列表)，
由编排层（api 层）执行动作。非法转移抛 StateMachineError；INTERRUPTED
期间的新打断信号按转移表 ❌ 规则忽略（返回原状态 + 空动作）。

对齐: docs/02-方案/DESIGN-打断时序与状态机转移表.md §4.1
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Tuple

from schemas import SessionState


class StateEvent(Enum):
    """状态机输入事件（对应转移表"触发事件"列）"""

    ASR_ENDPOINT = "asr_endpoint"  # ASR 端点（is_final=True）
    SILENCE_TIMEOUT = "silence_timeout"  # 用户静音超时（>2s 兜底）
    LLM_FIRST_TOKEN = "llm_first_token"  # LLM 首 token 返回
    LLM_TIMEOUT = "llm_timeout"  # LLM 超时（>1.5s）
    LLM_ERROR = "llm_error"  # LLM 错误
    VAD_INTERRUPT = "vad_interrupt"  # 打断信号（前端 VAD / HTTP interrupt）
    TTS_LIP_DONE = "tts_lip_done"  # TTS 流结束 + Lip 流结束
    INTERRUPT_DONE = "interrupt_done"  # 前端播放清理完成确认
    USER_SPEECH = "user_speech"  # SPEAKING 中收到用户语音分片（VAD 未触发）
    FATAL_ERROR = "fatal_error"  # 任何状态发生不可恢复错误


# ---- 动作（编排层执行，状态机只下发指令）----
ACT_SEND_BRAIN = "send_brain"  # 发送用户完整文本到 Brain
ACT_TRIGGER_TTS = "trigger_tts"  # 触发 TTS 合成（LLM 首 token 后）
ACT_FALLBACK_TTS = "fallback_tts"  # 降级话术 TTS（LLM 超时/错误）
ACT_EMIT_INTERRUPTED = "emit_interrupted"  # SSE interrupted 事件
ACT_CLEANUP_PIPELINE = "cleanup_pipeline"  # 沿流水线反向 stop（TTS←Brain←ASR）+ 清缓冲
ACT_EMIT_DONE = "emit_done"  # SSE done 事件
ACT_BUFFER_SPEECH = "buffer_interrupt_audio"  # 语音分片缓存进 pre_interrupt_buffer
ACT_OPEN_NEW_STREAM = "open_new_stream"  # 关闭旧 SSE 流，打开新流


@dataclass(frozen=True)
class Transition:
    """一条转移：目标状态 + 动作 + 是否允许"""

    to: SessionState
    actions: Tuple[str, ...] = ()
    allow: bool = True  # False = 忽略（保持原状态，不抛错）


class StateMachineError(Exception):
    """非法转移（转移表未定义的 from+event 组合）"""


class SessionStateMachine:
    """单会话状态机。转移表见 DESIGN §4.1，勿改语义，变更走 ADR。"""

    # {from_state: {event: Transition}}
    TRANSITIONS: Dict[SessionState, Dict[StateEvent, Transition]] = {
        SessionState.LISTENING: {
            StateEvent.ASR_ENDPOINT: Transition(SessionState.THINKING, (ACT_SEND_BRAIN,)),
            StateEvent.SILENCE_TIMEOUT: Transition(SessionState.THINKING, (ACT_SEND_BRAIN,)),
        },
        SessionState.THINKING: {
            StateEvent.LLM_FIRST_TOKEN: Transition(SessionState.SPEAKING, (ACT_TRIGGER_TTS,)),
            StateEvent.LLM_TIMEOUT: Transition(SessionState.SPEAKING, (ACT_FALLBACK_TTS,)),
            StateEvent.LLM_ERROR: Transition(SessionState.SPEAKING, (ACT_FALLBACK_TTS,)),
        },
        SessionState.SPEAKING: {
            StateEvent.VAD_INTERRUPT: Transition(
                SessionState.INTERRUPTED, (ACT_EMIT_INTERRUPTED, ACT_CLEANUP_PIPELINE)
            ),
            StateEvent.TTS_LIP_DONE: Transition(SessionState.LISTENING, (ACT_EMIT_DONE,)),
            StateEvent.USER_SPEECH: Transition(
                SessionState.SPEAKING, (ACT_BUFFER_SPEECH,)
            ),  # ⚠️ 缓存 pre_interrupt_buffer，打断触发后再处理
        },
        SessionState.INTERRUPTED: {
            StateEvent.INTERRUPT_DONE: Transition(
                SessionState.LISTENING, (ACT_OPEN_NEW_STREAM,)
            ),
            StateEvent.VAD_INTERRUPT: Transition(
                SessionState.INTERRUPTED, (), allow=False
            ),  # ❌ 忽略，保持清理中
        },
    }

    def __init__(self, session_id: str = "unknown") -> None:
        self.session_id = session_id
        self._state = SessionState.LISTENING

    @property
    def state(self) -> SessionState:
        return self._state

    def reset(self) -> None:
        """会话关闭/重开时复位"""
        self._state = SessionState.LISTENING

    def handle(self, event: StateEvent) -> Tuple[SessionState, List[str]]:
        """处理一个事件。

        返回 (新状态, 动作列表)。
        - FATAL_ERROR：任何状态 → ERROR
        - 转移表已定义且 allow=True：执行转移
        - 转移表已定义且 allow=False：忽略，返回原状态 + 空动作
        - 转移表未定义：抛 StateMachineError
        """
        if event is StateEvent.FATAL_ERROR:
            self._state = SessionState.ERROR
            return self._state, []

        table = self.TRANSITIONS.get(self._state)
        trans = table.get(event) if table else None
        if trans is None:
            raise StateMachineError(
                f"[{self.session_id}] 非法转移: {self._state.value} + {event.value}"
            )
        if not trans.allow:
            return self._state, []  # 忽略
        self._state = trans.to
        return self._state, list(trans.actions)
