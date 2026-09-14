"""ASR 流式模块单测：不加载真实模型（用假模型验证攒块/增量/收尾逻辑）。"""
from __future__ import annotations

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
