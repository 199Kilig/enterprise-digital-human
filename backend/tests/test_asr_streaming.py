"""ASR 流式模块单测：不加载真实模型（用假模型验证攒块/增量/收尾逻辑）。"""
from __future__ import annotations

import time

import numpy as np
import pytest

import asr.streaming as streaming
from asr.streaming import CHUNK_STRIDE, StreamingAsr, pcm16_to_float32


class FakeModel:
    """记录每次送进来的样本数，返回可预测文本。"""

    def __init__(self) -> None:
        self.calls: list[tuple[int, bool]] = []

    def generate(self, input, cache, is_final, **kwargs):  # noqa: A002
        self.calls.append((int(len(input)), is_final))
        return [{"text": f"t{len(self.calls)}"}]


@pytest.fixture()
def fake(monkeypatch):
    model = FakeModel()
    monkeypatch.setattr(streaming, "get_model", lambda: model)
    return model


def _pcm(n_samples: int, value: int = 1000) -> bytes:
    return np.full(n_samples, value, dtype="<i2").tobytes()


def test_pcm16_to_float32_scale_and_empty():
    assert pcm16_to_float32(b"").size == 0
    arr = pcm16_to_float32(np.array([0, 16384, -16384], dtype="<i2").tobytes())
    assert arr.dtype == np.float32
    assert arr[0] == 0.0
    assert abs(arr[1] - 0.5) < 1e-4


def test_pcm16_odd_length_drops_half_sample():
    """分片边界可能切断样本，不能崩。"""
    assert pcm16_to_float32(b"\x01\x02\x03").size == 1


def test_buffers_until_full_chunk(fake):
    asr = StreamingAsr()
    # 不足 600ms → 不送模型
    chunk = asr.push(_pcm(CHUNK_STRIDE - 100))
    assert fake.calls == []
    assert chunk.text == ""
    # 补齐到一块 → 送一次模型（is_final=False）
    chunk = asr.push(_pcm(100))
    assert fake.calls == [(CHUNK_STRIDE, False)]
    assert chunk.text == "t1"
    assert chunk.is_final is False


def test_multiple_chunks_in_one_push(fake):
    asr = StreamingAsr()
    asr.push(_pcm(CHUNK_STRIDE * 2 + 50))
    assert [c[1] for c in fake.calls] == [False, False]
    assert asr.state.pending.size == 50  # 残余留待下次


def test_last_flushes_remainder_with_is_final(fake):
    asr = StreamingAsr()
    asr.push(_pcm(100), is_last=True)
    assert fake.calls == [(100, True)]  # 残余样本也送，且标 final
    assert asr.state.pending.size == 0


def test_full_text_accumulates(fake):
    asr = StreamingAsr()
    asr.push(_pcm(CHUNK_STRIDE))
    asr.push(_pcm(CHUNK_STRIDE))
    assert asr.full_text == "t1t2"


def test_reset_clears_state(fake):
    asr = StreamingAsr()
    asr.push(_pcm(CHUNK_STRIDE))
    asr.state.reset()
    assert asr.full_text == ""
    assert asr.state.pending.size == 0
    assert asr.state.cache == {}


def test_asr_error_wrapped(fake, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("模型炸了")

    monkeypatch.setattr(fake, "generate", boom)
    asr = StreamingAsr()
    with pytest.raises(streaming.AsrError) as ei:
        asr.push(_pcm(CHUNK_STRIDE))
    assert ei.value.code == "ASR_ERROR"


# ---- 时间戳语义（SPEC §2 start_ms/end_ms = 本次增量在语音流中的时间窗）----

def test_timestamps_cover_all_chunks_in_one_push(fake):
    """一次 push 送多块时，窗口必须覆盖**全部**块。

    修正前只标「最后一块」的窗口（(chunks-1)*600 → chunks*600），
    前端一次补发 2 块（如 1.2s 分片）时会标成 600~1200ms，与增量文本起点不符。
    """
    asr = StreamingAsr()
    chunk = asr.push(_pcm(CHUNK_STRIDE * 2))
    assert (chunk.start_ms, chunk.end_ms) == (0, 1200)


def test_timestamps_continue_across_pushes(fake):
    asr = StreamingAsr()
    asr.push(_pcm(CHUNK_STRIDE))
    chunk = asr.push(_pcm(CHUNK_STRIDE))
    assert (chunk.start_ms, chunk.end_ms) == (600, 1200)


# ---- ASR_TIMEOUT（SPEC §6：单块识别 >3s）----

def test_slow_chunk_raises_asr_timeout(monkeypatch, fake):
    """超时须抛 ASR_TIMEOUT 而非 ASR_ERROR —— 两者的前端处理不同（提示重试 vs 兜底）。"""
    monkeypatch.setattr(streaming, "ASR_TIMEOUT_S", 0.05)

    def slow(*a, **k):
        time.sleep(0.15)
        return [{"text": "慢"}]

    monkeypatch.setattr(fake, "generate", slow)
    asr = StreamingAsr()
    with pytest.raises(streaming.AsrError) as ei:
        asr.push(_pcm(CHUNK_STRIDE))
    assert ei.value.code == "ASR_TIMEOUT"


def test_normal_chunk_does_not_trigger_timeout(fake):
    """实测单块 166ms vs 阈值 3000ms，不能误报。"""
    asr = StreamingAsr()
    chunk = asr.push(_pcm(CHUNK_STRIDE))
    assert chunk.text == "t1"
    assert streaming.CHUNK_MS == 600
