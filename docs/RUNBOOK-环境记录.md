# 环境记录（runbook）

> **定位**：本机/云 GPU 双环境的事实基线（ADR-001 要求的环境记录文档）。
> 内容只记事实（版本/端口/命令），不重复 DESIGN 方案；新增环境事实在此追加。
> 更新频率：环境变更时更新（装新依赖/换云镜像/换端口）。

## 1. 本机开发环境（Windows 11）

| 项 | 值 |
|---|---|
| OS | Windows 11（git-bash / MSYS 终端） |
| Python | 3.11.15（系统 `python`）；`python3` 命令缺失 |
| 包管理 | uv（venv 无 pip，装包用 `uv pip install --python .venv/Scripts/python.exe ...`） |
| backend venv | `backend/.venv`（uv 创建，Python 3.11） |

### backend 依赖版本（2026-09-01 快照）

| 包 | 版本 | 用途 |
|---|---|---|
| fastapi | 0.141.1 | API 网关 |
| uvicorn | 0.52.4 | ASGI 服务器 |
| pydantic | 2.13.5 | 请求校验 |
| starlette | 1.6.0 | FastAPI 底层 |
| numpy | 2.4.6 | eval 信号处理 |
| scipy | 1.17.1 | V-05 互相关测量 |
| pytest | 9.1.1 | 单元测试 |
| httpx | 0.28.1 | API 联调/测试 |

> 重装命令：`cd backend && uv pip install -e . && uv pip install pytest httpx`

### 常用命令

```bash
# 起服务（联调用端口 8010）
cd backend && .venv/Scripts/python.exe -m uvicorn src.api.routes:app --port 8010

# 跑单测
cd backend && .venv/Scripts/python.exe -m pytest tests/ -v

# 跑 V-05 音画测量自校准
cd backend/eval && ../.venv/Scripts/python.exe verify_sync_measure.py --report-dir reports
```

## 2. 端口约定

| 端口 | 用途 | 说明 |
|---|---|---|
| 8010 | backend API（P1 联调） | 8000/8001 易被本机其他项目占用，新服务避开 |
| 8002 | lip 推理服务（预留，SPEC §2.1） | 云 GPU 侧，见 config.yaml `lip.service_url` |

> 记忆坑：本机 3306/8000/5173 常被占；中文路径下 uvicorn `--reload` 不可靠（改码后手动重启）。

## 3. 云 GPU 环境（占位，未租）

| 项 | 值/要求 |
|---|---|
| 平台 | AutoDL（按小时，ADR-001） |
| 状态 | ⚠️ 未租用（阻塞 V-01/V-02） |
| 镜像要求 | Python 3.10 + CUDA 11.7+（MuseTalk 官方要求）；本机是 3.11，**版本不一致是已知坑**（DESIGN §5.4 坑 1） |
| 租用后记录 | GPU 型号 / CUDA 版本 / 镜像 ID / 依赖版本 → 追加到本表并登记 eval 台账"资源"行 |

## 4. 模型与下载源

- 模型权重国内优先 **ModelScope**（DESIGN §5.4 坑 2）：FunASR（paraformer-zh-streaming + fsmn-vad）、CosyVoice2、MuseTalk 权重
- 口型推理实验数据（FPS/显存）必须**实验即时落档**到 `backend/eval/reports/`，否则云实例释放后丢失（ADR-001 后果）

## 5. 变更记录

| 日期 | 变更 |
|---|---|
| 2026-09-01 | 建档：本机基线 + backend venv + 端口约定 + 云 GPU 占位 |
