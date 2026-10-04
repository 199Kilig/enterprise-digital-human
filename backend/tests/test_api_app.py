"""API 应用装配冒烟测试。

为什么需要：`backend/tests/` 里的其它测试只覆盖纯逻辑模块（ASR/切句/状态机/编码器），
**没有一条 import 过 api.routes**。结果是 routes.py 里一个 `NameError: name 'os' is not defined`
（用了 os.environ 却没 import）跑完整套单测全绿，直到真正启动 uvicorn 才炸。
本文件补上这道门：把"能不能装配起来"变成一条测试。
"""
from __future__ import annotations

import importlib


def test_api_app_imports_and_exposes_routes() -> None:
    """api.routes 能被导入，FastAPI app 装配完成，关键路由可解析。

    ⚠️ 断言必须走 `app.openapi()["paths"]` 而不是 `app.routes`：
    本版本（FastAPI 0.141.1）的 include_router 是**惰性**的，`app.routes` 里只有一个
    `_IncludedRouter` 占位对象，拿不到任何 APIRoute（拿 app.routes 断言会误判为"路由全丢"——
    这个坑我踩过，写在这里省下一次）。
    """
    mod = importlib.import_module("api.routes")
    assert mod.app is not None, "FastAPI app 未装配"

    paths = set(mod.app.openapi().get("paths", {}))
    for expected in ("/api/v1/session", "/api/v1/chat/stream", "/api/v1/health"):
        assert expected in paths, f"缺少路由 {expected}（现有：{sorted(paths)}）"


def test_lip_pipeline_config_resolved() -> None:
    """口型流水线关键配置已解析（ADR-005/006）——这些值错了症状很隐蔽（偏差/积压）。"""
    mod = importlib.import_module("api.routes")
    assert isinstance(mod._LIP_ON, bool)
    assert mod._LIP_FPS > 0
    assert mod._LIP_CHUNK_MS > 0, "分片粒度必须为正"
    assert mod._LIP_CHUNK_BYTES == mod._LIP_CHUNK_MS * 32, "16k PCM16 = 32 字节/ms"
    assert 0 <= mod._LIP_MIN_TAIL_MS < mod._LIP_CHUNK_MS, "尾片阈值应小于分片粒度"


def test_session_probe_endpoint() -> None:
    """会话存活探测端点（前端刷新后恢复会话用，v1.8 新增）。

    为什么需要测试：前端恢复会话全靠它区分"后端还活着"与"后端重启过"——
    前者复用同一 session_id（多轮上下文连续），后者新建会话但保留本地历史。
    它一旦坏掉，症状是"刷新后历史看得到、但数字人完全不记得之前聊过"。
    """
    from fastapi.testclient import TestClient

    mod = importlib.import_module("api.routes")
    client = TestClient(mod.app)
    sid = client.post("/api/v1/session").json()["session_id"]

    r = client.get(f"/api/v1/session/{sid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["session_id"] == sid
    assert body["state"] == "listening"
    assert body["history_len"] == 0
    assert body["created_at"]

    assert client.get("/api/v1/session/deadbeef").status_code == 404


def test_lip_service_url_must_not_use_localhost() -> None:
    """服务 URL 禁用 localhost：本机 localhost 解析 ~2s/次（RUNBOOK 坑 20，实测）。"""
    mod = importlib.import_module("api.routes")
    engine = mod._lip
    url = getattr(engine, "service_url", None)
    if url is None:
        return  # mock 引擎无 service_url
    assert "localhost" not in url, (
        f"service_url 含 localhost（{url}）：本机解析耗时约 2 秒/次，"
        "会直接叠加到音画偏差上，请改用 127.0.0.1"
    )


def test_readonly_data_endpoints_resolve_real_paths() -> None:
    """只读数据端点（指标看板 / 评估台账）真的能从仓库里读到数据。

    为什么需要：`/api/v1/metrics` 与 `/api/v1/eval/ledger` 是前端 `/metrics`、
    `/ledger` 两页的唯一数据源，而它们的目录由**仓库根路径**推导而来（2026-10-04 起
    统一走 config.REPO_ROOT，此前 routes.py 自己另算了一遍 `parents[3]`，同一事实两份定义）。
    路径推错一层不会抛异常，只会表现成"看板全空 / 台账 0 行"，与"本来就没数据"肉眼无法区分——
    所以这里直接拿仓库里真实存在的报告与台账来断言，而不是只断言状态码 200。
    """
    from fastapi.testclient import TestClient

    mod = importlib.import_module("api.routes")
    # 先钉住根：层数推错的典型症状是指到仓库外
    assert mod.REPO_ROOT == mod._REPORTS_DIR.parents[2], mod._REPORTS_DIR
    assert mod._REPORTS_DIR.is_dir(), f"报告目录不存在：{mod._REPORTS_DIR}"
    assert mod._LEDGER_FILE.is_file(), f"台账文件不存在：{mod._LEDGER_FILE}"

    client = TestClient(mod.app)
    r = client.get("/api/v1/metrics")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reports_found"], "一份报告都没找到——REPORTS_DIR 可能推错了"
    assert body["rows"], "指标行为空——REPORTS_DIR 可能推错了"

    r2 = client.get("/api/v1/eval/ledger")
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert body2["rows"], "台账 0 行——LEDGER_FILE 可能推错了"
