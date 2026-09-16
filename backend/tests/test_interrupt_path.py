"""打断路径后端侧测量与状态转移验证（PRD FR-06 / DESIGN-打断 §4.1）。

为什么单独一个文件：`docs/eval-history.md` 里「打断响应」一行长期是"待测"，
而打断是 PRD §3 的验收项之一，也是项目唯一"两个模块协作"的时序（前端 VAD → 后端状态机 → 清理）。
本文件把**后端侧那一半**变成可复现的数字与断言。

⚠️ 覆盖边界（别把它当端到端验收）：
    完整打断响应 = 前端 VAD 确认 + HTTP 往返 + 后端状态转移。
    本文件只测第三段（ASGI 内存直连，无网络、无麦克风）。
    前两段（真实麦克风插话、回声自打断）必须人工实测，见 `backend/eval/reports/asr_mic_check.md` §5。
"""
from __future__ import annotations

import importlib
import time

import pytest
from fastapi.testclient import TestClient

mod = importlib.import_module("api.routes")
StateEvent = importlib.import_module("api.state_machine").StateEvent
SessionState = importlib.import_module("schemas").SessionState

# PRD §3：打断响应 < 200ms（此处为后端侧分量的上限；ASGI 内存调用实测远低于此）
BUDGET_MS = 200


@pytest.fixture()
def client() -> TestClient:
    return TestClient(mod.app)


def _new_session(client: TestClient) -> str:
    r = client.post("/api/v1/session")
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def _drive_to_speaking(session_id: str) -> None:
    """白盒把会话推到 SPEAKING（模拟"数字人正在播报"）。

    走的是状态机自己的合法路径（LISTENING→THINKING→SPEAKING），不是直接改 `_state`，
    这样一旦转移表被改动，本文件也会跟着失败——不能靠绕过状态机来制造前置条件。
    真实情形下这两步由 ASR 端点与 LLM 首 token 触发（见 /chat/stream 编排）。
    """
    machine = mod._machines[session_id]
    machine.handle(StateEvent.ASR_ENDPOINT)      # LISTENING → THINKING
    machine.handle(StateEvent.LLM_FIRST_TOKEN)   # THINKING  → SPEAKING
    assert machine.state is SessionState.SPEAKING


def _state(session_id: str) -> SessionState:
    return mod._machines[session_id].state


def test_interrupt_from_speaking_transitions_within_budget(client: TestClient) -> None:
    """SPEAKING + 打断 → INTERRUPTED，且后端处理耗时在 PRD 预算内。"""
    sid = _new_session(client)
    _drive_to_speaking(sid)

    t0 = time.perf_counter()
    r = client.post(f"/api/v1/session/{sid}/interrupt")
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "interrupted"
    assert _state(sid) is SessionState.INTERRUPTED
    assert elapsed_ms < BUDGET_MS, f"后端打断处理 {elapsed_ms:.1f}ms 超出 {BUDGET_MS}ms 预算"


def test_interrupt_while_listening_is_ignored_idempotent(client: TestClient) -> None:
    """非 SPEAKING 态收到打断：转移表未定义 → 幂等忽略，不抛错、不改状态。"""
    sid = _new_session(client)  # 停在 LISTENING
    r = client.post(f"/api/v1/session/{sid}/interrupt")
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "ignored", "state": "listening"}
    assert _state(sid) is SessionState.LISTENING


def test_repeated_interrupt_during_cleanup_is_ignored(client: TestClient) -> None:
    """清理期（INTERRUPTED）内的新打断必须被忽略（DESIGN-打断 §4.1 ❌ 规则）。"""
    sid = _new_session(client)
    _drive_to_speaking(sid)
    client.post(f"/api/v1/session/{sid}/interrupt")

    r = client.post(f"/api/v1/session/{sid}/interrupt")
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "ignored", "state": "interrupted"}
    assert _state(sid) is SessionState.INTERRUPTED


def test_interrupt_done_returns_to_listening(client: TestClient) -> None:
    """前端清理完成确认 → INTERRUPTED → LISTENING（可开始新一轮）。"""
    sid = _new_session(client)
    _drive_to_speaking(sid)
    client.post(f"/api/v1/session/{sid}/interrupt")

    r = client.post(f"/api/v1/session/{sid}/interrupt_done")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cleaned"
    assert _state(sid) is SessionState.LISTENING

    # 回到 LISTENING 后应能立刻接受新一轮端点（打断的体验目标：不额外延迟）
    new_state, actions = mod._machines[sid].handle(StateEvent.ASR_ENDPOINT)
    assert new_state is SessionState.THINKING


def test_interrupt_unknown_session_returns_404(client: TestClient) -> None:
    r = client.post("/api/v1/session/deadbeef/interrupt")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "SESSION_NOT_FOUND"


def test_interrupt_sets_stop_requested_flag(client: TestClient) -> None:
    """打断必须**真的置位 `stop_requested`** —— 这才是"让数字人闭嘴"的开关。

    修复前：`/interrupt` 只改状态机状态，状态机返回的动作被丢弃，进行中的 SSE 生成器
    没有任何停止依据 → 整轮 token/TTS 照旧发完，前端播放队列照旧播完
    → 用户感受"数字人说话时打不断"。
    """
    sid = _new_session(client)
    _drive_to_speaking(sid)
    assert mod._sessions[sid].stop_requested is False

    client.post(f"/api/v1/session/{sid}/interrupt")
    assert mod._sessions[sid].stop_requested is True, "打断未置位：生成器不会停"

    client.post(f"/api/v1/session/{sid}/interrupt_done")
    assert mod._sessions[sid].stop_requested is False, "清理完成应复位，否则下一轮一开口就被打断"


def test_placeholder_stream_marks_done_placeholder(client: TestClient) -> None:
    """无 `text` 的空跑流必须标 `placeholder`。

    为什么关键：前端 SSE 断线时浏览器会**自动重连**，而重连请求不带 text
    → 后端返回这条占位流（thinking + done，无 answer）→ 前端若按普通一轮处理，
    answer 为空就什么都不追加 → 用户看到"这一轮回复凭空消失"。
    """
    sid = _new_session(client)
    body = client.get("/api/v1/chat/stream", params={"session_id": sid}).text
    assert "placeholder" in body, f"占位流未标记 placeholder，响应体：{body[:200]}"
    assert '"placeholder": true' in body.replace("\n", " ") or '"placeholder":true' in body


def test_speaking_receives_user_speech_without_interrupt(client: TestClient) -> None:
    """SPEAKING 中收到语音分片但 VAD 未触发打断：状态不变，动作为缓存（DESIGN §4.1 ⚠️ 行）。

    对应 `pre_interrupt_buffer` 语义——当前 ASR 模块尚未实现该缓冲（见 PROGRESS-2026-09-15），
    本测试锁住的是**状态机侧契约**，避免后续补实现时改坏转移表。
    """
    sid = _new_session(client)
    _drive_to_speaking(sid)
    new_state, actions = mod._machines[sid].handle(StateEvent.USER_SPEECH)
    assert new_state is SessionState.SPEAKING
    assert actions == ["buffer_interrupt_audio"]
