"""切句器单测：流水线重叠的正确性前提（切错会让 TTS 读出半个词 / 久不出声）。

语义约定：
- 句末标点 → 立即成句
- 缓冲 >= soft_limit 后遇到子句标点（，、等）→ 成句（避免攒太长才出声）
- 缓冲达到 hard_limit → 无条件成句（无标点的长串兜底）
"""
from brain.segmenter import SentenceSplitter


def test_splits_on_sentence_end():
    s = SentenceSplitter()
    assert s.feed("你好") == []
    assert s.feed("。世界") == ["你好。"]
    assert s.flush() == "世界"


def test_splits_multiple_in_one_token():
    s = SentenceSplitter()
    out = s.feed("第一句。第二句！第三句？")
    assert out == ["第一句。", "第二句！", "第三句？"]
    assert s.flush() is None


def test_soft_limit_breaks_at_clause():
    """软上限：越过 soft_limit 后的第一个子句标点即切（不等到句末）。"""
    s = SentenceSplitter(soft_limit=10, hard_limit=60)
    # 第 11 个字符就是逗号，且缓冲已 >= 10 → 只吐到逗号，其后仍留在缓冲
    assert s.feed("这是一个比较长的句子，还没结束") == ["这是一个比较长的句子，"]
    assert s.buf == "还没结束"
    # 剩余部分未达 soft_limit，继续缓冲
    assert s.feed("，还是没说完") == []
    assert s.flush() == "还没结束，还是没说完"


def test_soft_limit_not_reached_stays_buffered():
    s = SentenceSplitter(soft_limit=30, hard_limit=60)
    assert s.feed("短句，带逗号") == []
    assert s.flush() == "短句，带逗号"


def test_hard_limit_without_punctuation():
    """硬上限：无标点时到 hard_limit 立即成句，绝不无限等。"""
    s = SentenceSplitter(soft_limit=10, hard_limit=12)
    assert s.feed("没有任何标点的一长串文字") == ["没有任何标点的一长串文字"]
    assert s.flush() is None


def test_hard_limit_then_remainder():
    s = SentenceSplitter(soft_limit=10, hard_limit=12)
    out = s.feed("没有任何标点的一长串文字还有剩余")
    assert out == ["没有任何标点的一长串文字"]
    assert s.flush() == "还有剩余"


def test_newline_terminates():
    s = SentenceSplitter()
    assert s.feed("行一\n行二") == ["行一"]
    assert s.flush() == "行二"


def test_flush_empty_returns_none():
    s = SentenceSplitter()
    s.feed("。")
    assert s.flush() is None


def test_reassembled_text_is_lossless():
    """切句不能丢字或加字——拼回来必须等于原文。"""
    src = "亲，退货到账一般1到7个工作日，具体看退款方式哦～您方便的话我帮您转人工核实一下这笔订单。"
    s = SentenceSplitter()
    pieces = []
    for ch in src:
        pieces.extend(s.feed(ch))
    tail = s.flush()
    if tail:
        pieces.append(tail)
    assert "".join(pieces) == src
