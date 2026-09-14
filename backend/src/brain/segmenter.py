"""对话大脑输出的切句器（流水线重叠的前提）。

为什么需要它：DESIGN §3.1 的核心思想是"各段流水线并行，全链路延迟不是各段串行累加"。
若等 LLM 输出完整回答再交给 TTS，首包延迟 = LLM 全量生成 + TTS 首包（串行）；
按句切分后，首句一到位即可开始合成，首包延迟 ≈ LLM 首句 + TTS 首包。

切分规则：
- 句末标点（。！？；…以及英文 !?; 和换行）立即成句
- 单句过长（>= SOFT_LIMIT）时退而求其次在最近的逗号/顿号处切，避免攒到几十字才出声
- 硬上限 HARD_LIMIT 兜底（无标点的长串也不能无限等）
"""
from __future__ import annotations

SENTENCE_END = "。！？!?；;…\n"
SOFT_LIMIT = 30
HARD_LIMIT = 60
CLAUSE_END = "，,、）)】」"


class SentenceSplitter:
    """增量喂 token，吐出可立即送去合成的句子。"""

    def __init__(self, soft_limit: int = SOFT_LIMIT, hard_limit: int = HARD_LIMIT) -> None:
        self.buf = ""
        self.soft_limit = soft_limit
        self.hard_limit = hard_limit

    def feed(self, token: str) -> list[str]:
        out: list[str] = []
        for ch in token:
            self.buf += ch
            if ch in SENTENCE_END:
                piece = self.buf.strip()
                if piece:
                    out.append(piece)
                self.buf = ""
                continue
            if len(self.buf) >= self.hard_limit:
                out.append(self.buf.strip())
                self.buf = ""
                continue
            if len(self.buf) >= self.soft_limit and ch in CLAUSE_END:
                out.append(self.buf.strip())
                self.buf = ""
        return [p for p in out if p]

    def flush(self) -> str | None:
        """流结束时把剩余文本交出（无标点结尾的句子）。"""
        piece = self.buf.strip()
        self.buf = ""
        return piece or None
