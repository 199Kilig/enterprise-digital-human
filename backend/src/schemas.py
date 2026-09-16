"""模块间数据契约（对齐 SPEC-接口与协议规范 §2 v1.1）

链接: docs/02-方案/SPEC-接口与协议规范.md
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional, Tuple


# ============ ASR → Brain ============
@dataclass
class AsrChunk:
    """语音识别增量输出"""

    text: str  # 增量识别文本
    is_final: bool = False  # 该分片是否为句尾（端点检测）
    start_ms: Optional[int] = None  # 本分片在用户语音流中的起始时间
    end_ms: Optional[int] = None  # 本分片在用户语音流中的结束时间
    confidence: float = 0.0  # 置信度（0-1），用于调试


# ============ Brain → TTS ============
@dataclass
class BrainOutput:
    """对话大脑流式输出"""

    token: str  # 增量文本 token（流式）
    is_final: bool = False  # 是否为完整回答的最后一个 token


# ============ TTS → Lip / 前端 ============
@dataclass
class TtsSegment:
    """语音合成分片输出"""

    audio: bytes  # PCM 单声道 音频数据（采样率见 sample_rate）
    start_ms: int  # 本分片在整句音频中的起始时间（毫秒）
    end_ms: int  # 本分片在整句音频中的结束时间（毫秒）
    words: List[Tuple[str, int, int]] = field(default_factory=list)
    # 音画同步核心字段: [(词, 起始ms, 结束ms)]，例: [("你好", 0, 300), ("世界", 300, 600)]
    sample_rate: int = 16000  # v1.1 新增。链路统一基准 16kHz；TTS 输出非 16kHz（如 CosyVoice2 24kHz）时由 tts 模块重采样后再出 TtsSegment

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


# ============ Lip → Render ============
@dataclass
class LipFrame:
    """口型驱动视频帧输出"""

    frame: bytes  # JPEG 编码（P1）/ H.264 编码（P2 后）
    pts_ms: int  # 展示时间戳（毫秒），基于 TTS 音频时钟
    frame_idx: int  # 帧序号，用于前端渲染调度


# ============ 会话状态 ============
class SessionState(Enum):
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"
    ERROR = "error"


@dataclass
class SessionContext:
    """会话上下文（内存中维护）"""

    session_id: str
    state: SessionState
    created_at: datetime
    last_active: datetime
    history: List[dict] = field(default_factory=list)  # 对话历史 [{role, content}]
    buffer_audio: List[TtsSegment] = field(default_factory=list)  # 待播放 TTS 队列
    buffer_lip: List[LipFrame] = field(default_factory=list)  # 待渲染口型帧队列
    stop_requested: bool = False
    # v1.6 新增：打断标志。`POST /session/{id}/interrupt` 置位 → 进行中的 `chat_stream`
    # SSE 生成器轮询到即停止产出（不再发 token / 合成 TTS / 送口型），置位后不发 done。
    # 状态机由 /interrupt 与 /interrupt_done 驱动，本标志只负责"让数字人闭嘴"。


# ============ 错误响应 ============
@dataclass
class ApiError:
    """统一错误响应格式（错误码见 SPEC §6）"""

    code: str
    message: str
    session_id: Optional[str] = None
    retry_after: Optional[int] = None  # 建议重试等待秒数
