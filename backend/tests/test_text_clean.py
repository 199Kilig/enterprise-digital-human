"""TTS 文本清理单测：确保喂给 TTS 的是"能念出来的干净文本"。

为什么这些断言值得写：TTS 收到 Markdown/emoji 不会报错，只会**念错**
（星号、表情符号名称、把"（停顿）"当正文），线上表现是"数字人嘴里说着星号"——
属于不报错但明显错误的缺陷，必须靠断言锁住。
"""
from brain.segmenter import SentenceSplitter
from brain.text_clean import clean_for_tts


def test_strips_inline_emphasis():
    assert clean_for_tts("**分数**加减法") == "分数加减法"
    assert clean_for_tts("*重点*是通分") == "重点是通分"
    # 删除线只剥标记、保留内容：清理层不判断语义（要"删内容"就属改写，不属清理）
    assert clean_for_tts("~~错的~~对的") == "错的对的"
    assert clean_for_tts("__强调__") == "强调"


def test_strips_structural_markdown():
    assert clean_for_tts("# 复习计划") == "复习计划"
    assert clean_for_tts("- 第一步\n- 第二步") == "第一步 第二步"
    assert clean_for_tts("1. 先通分\n2. 再相加") == "先通分 再相加"
    assert clean_for_tts("① 通分") == "通分"
    assert clean_for_tts("> 引用的话") == "引用的话"
    assert clean_for_tts("---") == ""
    assert clean_for_tts("| 项目 | 值 |\n|---|---|") == ""


def test_strips_code_and_links():
    assert clean_for_tts("看 `1/2` 这个分数") == "看 1/2 这个分数"
    assert clean_for_tts("```\n分母相同\n```") == "分母相同"
    assert clean_for_tts("参考[课本](https://x.com/a)第 3 页") == "参考课本第 3 页"
    assert clean_for_tts("打开 https://example.com/a?b=1 看看") == "打开 看看"
    assert clean_for_tts("<https://example.com>") == ""


def test_strips_emoji():
    assert clean_for_tts("真棒！🎉👏") == "真棒！"
    assert clean_for_tts("✅ 完成") == "完成"
    assert clean_for_tts("分数 → 小数") == "分数 小数"


def test_removes_stage_directions_but_keeps_math_parentheses():
    """舞台说明删掉；数学括号是题目内容，绝不能删（念错题比念出"停顿"更严重）。"""
    assert clean_for_tts("（停顿）我们看下一题") == "我们看下一题"
    # 中文句中删掉舞台说明不补空格（中文本来不需要词间空格）
    assert clean_for_tts("我觉得（微笑）你可以先通分") == "我觉得你可以先通分"
    # 括号形式保持原文（只删舞台说明，不做全角/半角归一化）
    assert clean_for_tts("先算（3/4）加（1/4）") == "先算（3/4）加（1/4）"
    assert clean_for_tts("（3+5）×2") == "（3+5）乘2"


def test_symbols_become_spoken_forms():
    assert clean_for_tts("3×4") == "3乘4"
    assert clean_for_tts("8÷2") == "8除以2"
    assert clean_for_tts("1/2 = 0.5") == "1/2 等于 0.5"


def test_collapses_whitespace_and_stray_spaces():
    assert clean_for_tts("你好   \n\n  同学") == "你好 同学"
    assert clean_for_tts("先通分 ，再相加 。") == "先通分，再相加。"


def test_empty_and_pure_noise():
    assert clean_for_tts("") == ""
    assert clean_for_tts("   ") == ""
    assert clean_for_tts("🎉🎉🎉") == ""
    assert clean_for_tts("---\n---") == ""


def test_plain_text_is_untouched():
    src = "我们先把分母变成一样的，再算分子。"
    assert clean_for_tts(src) == src


def test_idempotent():
    """再清一次不应继续变化：清理必须在真实链路的重复调用下稳定。"""
    noisy = "**重点**：3×4 🎯（停顿）先算 `3/4` 哦！"
    once = clean_for_tts(noisy)
    assert clean_for_tts(once) == once


def test_real_failure_sample_through_splitter():
    """真实形态：LLM 输出带格式噪音的长句 → 切句 → 逐句清理后每句都可直接念。"""
    src = "**分数加法**：先通分哦～🎯\n- 分母相同才能加\n（停顿）我们来试一题：3/4 + 1/4 = 1"
    splitter = SentenceSplitter()
    sentences = splitter.feed(src)
    tail = splitter.flush()
    if tail:
        sentences.append(tail)
    spoken = [clean_for_tts(s) for s in sentences]
    spoken = [s for s in spoken if s]  # 调用方（routes.tts_consumer）同样跳过空句
    assert spoken, "清理后不应为空——否则整轮没有语音产出"
    joined = " ".join(spoken)
    for noise in ("**", "🎯", "（停顿）", "- ", "#"):
        assert noise not in joined
    assert "等于" in joined  # "=" 已口语化
