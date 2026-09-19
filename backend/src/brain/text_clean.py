"""TTS 前的文本清理：把 LLM 输出洗成"能念出来的干净文本"。

职责边界（重要，勿越界）：
- 只清洗**送给 TTS 的那一份**文本；SSE 的 `brain_token` 与 `done.answer` 仍是 LLM 原文
  （前端要按 Markdown 渲染气泡，不能把清洗后的文本回写给人看）。
- 只做**格式类**噪音清理（Markdown / emoji / 编号 / 链接 / 舞台说明），
  不做语义改写（不改写句子、不摘要、不做完整数值口语化）。

为什么要有这一层：prompt（`brain/persona.py`）已经要求模型别输出 Markdown 与 emoji，
但模型并不总是听话；TTS 收到 `**重点**` 会念出"星号星号重点星号星号"，
收到 emoji 可能直接卡住或念出怪异音。约束 + 清理双保险，清理层是兜底。

已实测的两种失败形态（本地断言见 tests/test_text_clean.py）：
- `**分数**加减法` → 不清理时 TTS 读出星号
- `（停顿）我们看下一题` → 不清理时把"停顿"当正文念出来
"""
from __future__ import annotations

import re

# ---- 结构性 Markdown ----
FENCE = re.compile(r"```[a-zA-Z0-9_+.-]*\s*(.*?)```", re.S)  # 代码块：留内容去围栏
INLINE_CODE = re.compile(r"`([^`]*)`")
HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]*", re.M)
QUOTE = re.compile(r"^[ \t]{0,3}>[ \t]?", re.M)
HR = re.compile(r"^[ \t]{0,3}(?:-{3,}|\*{3,}|_{3,})[ \t]*$", re.M)  # 分隔线整行
TABLE_ROW = re.compile(r"^[ \t]{0,3}\|.*\|[ \t]*$", re.M)  # 表格行：念出来只有竖线噪音
# ⚠️ 必须要求列表符后跟空白或行尾：(?=[ \t]|$)
#    否则 "**加粗**" 的行首 "*" 会被当列表符吃掉，剩下 "分数*加减法" 这种残骸喂给 TTS
LIST_MARK = re.compile(r"^[ \t]*(?:[-*+•·]|\d{1,2}[.、)）]|[①-⑳])(?=[ \t]|$)[ \t]*", re.M)

# ---- 行内强调 / 链接 ----
# 拆成四条而不是一条 (\*{1,3}|_{1,3})…：一条通吃时  会在 "*" 与 "**" 之间回溯，
# 把 "**分数**加减法" 匹成 "*分数*" + 残余 "*"（实测），必须分别锚定。
BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", re.S)
ITALIC = re.compile(r"(?<!\*)\*(?=\S)(.+?)(?<=\S)\*(?!\*)", re.S)
UNDER_BOLD = re.compile(r"__(?=\S)(.+?)(?<=\S)__", re.S)
UNDER_ITALIC = re.compile(r"(?<![0-9A-Za-z_])_(?=\S)(.+?)(?<=\S)_(?![0-9A-Za-z_])", re.S)
STRIKE = re.compile(r"~~(.+?)~~", re.S)
LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")  # [文字](url) → 文字
AUTOLINK = re.compile(r"<(https?://[^>\s]+)>")
BARE_URL = re.compile(r"https?://[^\s，。！？、）)]+")

# LaTeX 定界符：模型偶尔吐 $x^2$ / \( \) —— 去掉定界符，内容按普通文本念
MATH_DELIM = re.compile(r"\$([^$]{1,60})\$|\\\((.{1,60}?)\\\)")

# ---- emoji / 装饰符号 ----
EMOJI = re.compile(
    "["
    "\U0001F000-\U0001FAFF"  # 表情、物品、补充符号（😀📚🎯…）
    "\U00002600-\U000027BF"  # 杂项符号与 dingbats（✓✗★♥…）
    "\U00002190-\U000021FF"  # 箭头（→ 念出来只是噪音）
    "\U00002B00-\U00002BFF"  # 杂项符号与箭头（⭐…）
    "\U0001F1E6-\U0001F1FF"  # 区域指示符（国旗）
    "\uFE0F\u200D\u20E3"     # 变体选择符 / ZWJ / 键帽
    "]+"
)

# ---- 舞台说明（（停顿）（笑）…）----
# 只删"整段都落在词表里"的括号内容：数学里括号是有意义的（如（3/4）），
# 一概删括号会把题目念错，所以用**显式词表**而非"删所有括号"。
STAGE_RE = re.compile(r"[（(]\s*([^（()）]{1,12}?)\s*[）)]")
STAGE_WORDS = {
    "停顿", "稍等", "笑", "微笑", "大笑", "轻笑", "叹气", "轻声", "小声", "温柔地", "认真地",
    "严肃", "无奈", "惊讶", "开心", "难过", "兴奋", "沉默", "点头", "摇头", "鼓掌", "拍手",
    "思考", "想了想", "强调", "调皮", "眨眼", "咳嗽", "清嗓子", "语气上扬", "语速放慢", "示范",
}

# ---- 数学符号 → 口语（教育场景；只做高置信、不涉及数值改写的替换）----
# 不做完整数值口语化（12 → 十二、82% → 百分之八十二）：
# 那需要数字→中文转换与量词判断，属于 TTS 前端文本规范化（TN）范畴，留给后续。
SYMBOL_SPEECH = {
    "×": "乘",
    "✕": "乘",
    "÷": "除以",
    "=": "等于",
    "≈": "约等于",
    "√": "根号",
}

_WHITESPACE = re.compile(r"\s+")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([，。！？、；：）】」])")


def _replace_stage(match: re.Match[str]) -> str:
    inner = match.group(1).strip()
    return "" if inner in STAGE_WORDS else match.group(0)


def clean_for_tts(text: str) -> str:
    """返回可直接喂给 TTS 的干净文本；若清理后为空则返回空串（调用方应跳过该句）。"""
    if not text:
        return ""
    s = text
    s = FENCE.sub(lambda m: m.group(1).strip(), s)
    s = TABLE_ROW.sub("", s)
    s = HR.sub("", s)
    s = QUOTE.sub("", s)
    s = HEADING.sub("", s)
    s = LIST_MARK.sub("", s)
    s = INLINE_CODE.sub(lambda m: m.group(1), s)
    s = STRIKE.sub(lambda m: m.group(1), s)
    s = BOLD.sub(lambda m: m.group(1), s)
    s = ITALIC.sub(lambda m: m.group(1), s)
    s = UNDER_BOLD.sub(lambda m: m.group(1), s)
    s = UNDER_ITALIC.sub(lambda m: m.group(1), s)
    s = LINK.sub(lambda m: m.group(1), s)
    s = AUTOLINK.sub("", s)
    s = BARE_URL.sub("", s)
    s = MATH_DELIM.sub(lambda m: (m.group(1) or m.group(2) or ""), s)
    s = EMOJI.sub("", s)
    s = STAGE_RE.sub(_replace_stage, s)
    for sym, spoken in SYMBOL_SPEECH.items():
        s = s.replace(sym, spoken)
    s = _WHITESPACE.sub(" ", s)
    s = _SPACE_BEFORE_PUNCT.sub(r"\1", s)
    return s.strip()
