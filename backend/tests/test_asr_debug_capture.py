"""ASR_DEBUG 采集缓冲的生命周期测试（防内存泄漏回归）。

背景：`ASR_DEBUG=1` 时后端把每次语音会话的完整 PCM 累积在 `_asr_capture[session_id]`，
识别收尾才写盘并 pop。**任何提前退出路径都必须释放它**，否则累积的 PCM 永久驻留内存
（真人麦克风一段话就是几百 KB，长时间跑会持续增长）。

外部代码审查只发现了 `AsrError` 一处；复核同类路径时发现 `close_session` 同样遗漏
（关掉会话后缓冲仍留在内存里）。本文件把三条路径都锁住：正常收尾 / 识别异常 / 关会话。

⚠️ 用 fake 替身替换 `StreamingAsr`，避免测试真去加载 ASR 模型（慢且依赖权重）。
"""
from __future__ import annotations

import base64
import importlib
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from asr.streaming import AsrError


class _FakeAsr:
    """最小 StreamingAsr 替身：不加载模型，只回固定文本。"""

    def __init__(self, *_, **__) -> None:
        self.full_text = "你好"

    def push(self, pcm16: bytes, is_last: bool = False):
        return SimpleNamespace(text="你好", is_final=bool(is_last), start_ms=0, end_ms=100)


@pytest.fixture
def mod():
    return importlib.import_module("api.routes")


@pytest.fixture
def client(mod, monkeypatch):
    monkeypatch.setattr(mod, "StreamingAsr", _FakeAsr)
    return TestClient(mod.app)


@pytest.fixture
def capture(mod, monkeypatch) -> dict:
    """打开 ASR_DEBUG 并隔离缓冲字典，避免污染其它测试。返回该字典本身。"""
    monkeypatch.setattr(mod, "_ASR_DEBUG", True)
    monkeypatch.setattr(mod, "_asr_capture", {})
    return mod._asr_capture


def _pcm_b64(n_bytes: int = 640) -> str:
    return base64.b64encode(b"\x11" * n_bytes).decode()


def _sid(client: TestClient) -> str:
    return client.post("/api/v1/session").json()["session_id"]


def _chunk(client: TestClient, sid: str, seq: int, *, end: bool, n_bytes: int = 640):
    return client.post("/api/v1/asr/chunk", json={
        "session_id": sid, "seq": seq, "audio_pcm_16k_b64": _pcm_b64(n_bytes), "end": end,
    })


def test_capture_accumulates_while_listening(client, capture) -> None:
    """反向断言：识别进行中缓冲确实在累积。

    没有这条，上面几个"assert sid not in capture"即使缓冲功能整体失效也会通过（假绿）。
    """
    sid = _sid(client)
    for seq in (0, 1, 2):
        r = _chunk(client, sid, seq, end=False, n_bytes=320)
        assert r.status_code == 200, r.text
    assert len(capture.get(sid, b"")) == 3 * 320, "三片 320 字节累计应为 960 字节"


def test_capture_released_on_normal_end(client, capture) -> None:
    """正常收尾（end=True 写盘）后缓冲必须清空。"""
    sid = _sid(client)
    r = _chunk(client, sid, 0, end=True)
    assert r.status_code == 200, r.text
    assert sid not in capture, "正常收尾后采集缓冲应已释放"


def test_capture_released_on_asr_error(client, capture, monkeypatch) -> None:
    """识别异常（AsrError → 503）时缓冲也必须释放 —— 外部审查发现的泄漏点。"""
    sid = _sid(client)
    assert _chunk(client, sid, 0, end=False).status_code == 200  # 缓冲区里先有东西

    def boom(self, pcm16, is_last=False):
        raise AsrError("ASR_ERROR", "模拟识别失败")

    monkeypatch.setattr(_FakeAsr, "push", boom)
    r = _chunk(client, sid, 1, end=False)
    assert r.status_code == 503, r.text
    assert sid not in capture, "识别异常后采集缓冲必须释放（否则泄漏）"


def test_capture_released_on_session_close(client, capture) -> None:
    """关会话时缓冲也要释放 —— 同类路径，审查报告未覆盖。"""
    sid = _sid(client)
    assert _chunk(client, sid, 0, end=False).status_code == 200
    assert capture.get(sid), "前置条件：缓冲里应有累积的 PCM"

    r = client.delete(f"/api/v1/session/{sid}")
    assert r.status_code == 200, r.text
    assert sid not in capture, "关会话后采集缓冲必须释放"


def test_capture_untouched_when_debug_off(client, mod, monkeypatch) -> None:
    """ASR_DEBUG 关闭时不应产生任何采集记录（默认不落盘、不驻留）。"""
    monkeypatch.setattr(mod, "_ASR_DEBUG", False)
    monkeypatch.setattr(mod, "_asr_capture", {})
    sid = _sid(client)
    assert _chunk(client, sid, 0, end=False).status_code == 200
    assert mod._asr_capture == {}, "未开 ASR_DEBUG 时不应累积任何 PCM"
