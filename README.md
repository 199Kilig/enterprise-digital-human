# 企业级数字人

> **一句话定位**：浏览器里说一句话，数字人开口应答 —— 端到端 **1.18s**（目标 < 2s），口型片段产出 **RTF 0.71**（快于音频播放，队列不积压）。
> 全链路**自研编排**（FastAPI + 显式会话状态机），口型推理跑在云端 GPU，本机只做编排与播放。

---

## 核心指标

> **唯一入口是评估台账** `docs/eval-history.md`（口径 / 环境 / 脚本三要素齐全）。
> 下表只是摘要，每个数字都标了实测日期，以台账为准。

| 维度 | 指标 | 实测 | 目标 | 状态 |
|---|---|---|---|---|
| 延迟 | **端到端**（说完 → 首包开口） | **1183ms**（6 轮均值，min 977 / max 1407，2026-09-14） | < 2s | ✅ |
| 延迟 | LLM 首 token | ~740ms（503~1108ms 波动，2026-09-02） | < 800ms | ✅ |
| 延迟 | ASR 单块处理 | 166ms（CPU RTF 0.27，2026-09-02） | ≤ 300ms | ✅ |
| 延迟 | ASR 首字端到端 | ≈ 766ms（600ms 分片粒度下，2026-09-02） | — | ⚠️ 见下 |
| 延迟 | TTS 首包 | **618ms**（2026-09-14） | < 300ms | ❌ **未达标** |
| 延迟 | 口型单片（云 GPU） | **711~737ms / RTF 0.72~0.74**（2026-09-23） | RTF < 1 | ✅ |
| 延迟 | 口型首帧 | 待测 | < 400ms | ⏳ |
| 吞吐 | ASR 流式字准率（干净音频） | 100%（7/7，CER 0.000，2026-09-17） | — | ✅ |
| 资源 | 云 GPU 显存 | 7064 MiB（模型 + 536 帧 avatar 缓存，2026-09-14） | 24G 卡 | ✅ |
| 资源 | 云↔本机隧道带宽 | **0.96 MB/s 下行 / 0.46 MB/s 上行**（2026-09-14） | — | ⚠️ 链路层硬限 |
| 音画同步 | 口型片段到达偏差（1s 分片后） | 首片 **+205ms**，13/13 片**提前到达**（2026-09-14） | — | ✅ |
| 稳定性 / 并发 | 30 分钟不掉帧 / 单卡路数 | 待测 | — | ⏳ |

**两处如实说明**：① **TTS 首包 618ms 未达 300ms 目标**（CosyVoice v2 WebSocket 首片实测），是当前端到端里唯一未达标的分段；② **ASR「首字端到端 ≤300ms」原目标在 600ms 分片粒度下不成立**，仅"单块处理"成立（EVAL-P1 v1.2 已修正口径）。

---

## 架构

```mermaid
flowchart TB
    subgraph FE["React 前端（浏览器）"]
        MIC["语音采集<br/>AudioWorklet + VAD"]
        PLAY["音频播放 + 视频播放<br/>音频时钟为准"]
    end

    subgraph BE["FastAPI 后端（本机）"]
        SM["会话状态机<br/>LISTENING → THINKING → SPEAKING → INTERRUPTED"]
        ASRM["asr/ 流式识别"]
        BRAIN["brain/ 对话大脑<br/>+ 文本清理"]
        TTSM["tts/ 流式合成<br/>word-level 时间戳"]
        LIPM["lip/ 口型调度<br/>1s 分片 (ADR-006)"]
        SSE["SSE 事件流<br/>thinking / brain_token<br/>tts_audio / lip_video / done"]
    end

    subgraph CLOUD["云端 GPU（AutoDL 4090）"]
        LIPSVC["MuseTalk 口型服务<br/>gen(GPU) + blend(numpy) + h264"]
    end

    EXT1["FunASR<br/>本地流式"]
    EXT2["DeepSeek<br/>对话模型"]
    EXT3["CosyVoice v2<br/>阿里云百炼"]

    MIC -->|"16k PCM 分片"| ASRM
    ASRM --> EXT1
    ASRM --> BRAIN
    BRAIN --> EXT2
    BRAIN --> TTSM
    TTSM --> EXT3
    TTSM -->|"音频分片"| SSE
    TTSM -->|"1s 音频分片"| LIPM
    LIPM -->|"HTTP POST /lip/infer"| LIPSVC
    LIPSVC -->|"H.264 整句片段<br/>73~75 KB/片"| LIPM
    LIPM -->|"lip_video"| SSE
    SM -.->|"状态与打断"| SSE
    SSE -->|"EventSource"| PLAY
    MIC -.->|"VAD 打断"| SM
```

**数据流一句话**：语音 → 流式 ASR → LLM 流式出字 → TTS 流式出音频（**首包即播**）→ 同一路音频按 1s 分片送云端口型 → H.264 片段经 SSE 回前端 → 按音频时钟调度播放。

### 延迟分解（实测，非目标值）

```mermaid
gantt
    title 一次真实提问的耗时构成（实测口径，2026-09-14 / 09-23）
    dateFormat X
    axisFormat %s
    section 首包路径
    ASR 识别（分片）        :a1, 0, 166
    LLM 首 token            :a2, 166, 740
    TTS 首包                :a3, 906, 618
    section 口型（并行，不阻塞开口）
    云端口型单片 711ms      :b1, 1524, 711
    传输隧道 0.96MB/s       :b2, after b1, 300
```

| 分段 | 实测 | 说明 |
|---|---|---|
| ASR 单块 → 首字端到端 | 166ms / ≈766ms | 600ms 分片粒度 |
| LLM 首 token | ~740ms | 流式 |
| TTS 首包 | 618ms | **即开口时刻**，与口型并行 |
| **→ 端到端（首包开口）** | **1183ms** | 达标（< 2s） |
| 口型单片（并行支路） | 711ms / RTF 0.71 | 快于音频播放，不积压 |
| 云↔本机隧道 | 0.96 MB/s | 链路层硬限（非协议层，见 ADR-005） |

> **关键是并行而非串行累加**：TTS 首包一出就开始播放，口型走独立支路追赶音频时钟。

---

## 工程要点（可讲的部分）

| 要点 | 做法 | 依据 |
|---|---|---|
| **口型链路提速** | 把官方 PIL 融合换纯 numpy 等价实现（逐像素 maxdiff=0），云侧片耗时 1065 → 711ms、RTF 1.065 → 0.71 | ADR-010、RUNBOOK §3.20 |
| **为什么不上 GPU 做融合** | 实测 1.0×：每帧要过 PCIe 5.7MB，真实计算仅 362×362×3 —— 瓶颈是搬运不是算力 | 同上（E3/E4 对照） |
| **为什么不做帧级/WebRTC 重写** | 瓶颈在实现不在架构；帧级会把 SSE 事件数 11 → 300；WebRTC 属 P2（链路层才是硬限） | ADR-010 |
| **口型传输选 H.264 整句片段** | 逐帧 JPEG 15.52MB/8s → H.264 387KB，**41×** | ADR-005 |
| **按 1s 音频分片送检** | 首片偏差 5274ms → **205ms**；服务 URL 禁用 `localhost`（本机解析 2s/次） | ADR-006、RUNBOOK 坑 20 |
| **打断归属前端** | VAD/端点检测在浏览器 AudioWorklet，后端只"收到音频就识别" | ADR-004 |
| **不引开源整包** | 编排/状态机/TTS 清洗全部自研，依赖只在模型层 | ADR-002 |
| **文档与代码同寿命** | PRD / DESIGN / ADR / 进度 / 台账分离；**量化指标只进台账**，其余文档只引用 | `docs/docs-guide.md` |

---

## 快速开始

```bash
# 1) 后端（本机）—— 需先在 backend/.env 配好 DASHSCOPE_API_KEY 等
cd backend
.venv/Scripts/python.exe -m uvicorn api.routes:app --host 127.0.0.1 --port 8010

# 2) 前端（本机）
cd frontend && npm install && npm run dev        # http://127.0.0.1:5173
```

**需要口型画面时**（云 GPU）：

```bash
# AutoDL 控制台开机（选"有卡模式"）后，一键恢复服务 + 隧道
bash backend/deploy/restore-cloud-lip.sh <SSH端口>
```

**验证服务状态**：`curl http://127.0.0.1:8010/api/v1/health` → `lip_service.reachable` 应为 `true`。

> 环境搭建的完整细节与全部已知坑见 `docs/RUNBOOK-环境记录.md`（含"关机不丢东西/换镜像会丢"等）。

## 30 分钟演示脚本

> 路由以 `frontend/src/App.tsx` 为准：**4 个产品页**（产品视角，主导航）+ **3 个工程视图**（验收证据链，收在侧栏二级分组，默认不外露）。
> `2026-09-23` 那次重构已下线 `/tools/:key` 的 4 个占位页（错题集合 / 视频讲解 / 知识库 / 阶段目标）——它们没有后端数据源，能力由 `/insights`（真数据）承接。

| 分钟 | 内容 | 看点 |
|---|---|---|
| 0–3 | 启动后端 + 前端 + `restore-cloud-lip.sh` | `health` 里 `lip_service.reachable: true`、云端 `frames_cached: 536` |
| 3–8 | `/console` 文字提问走通全链路 | 气泡逐段出字；偶发提示只有两种：**「麦克风不可用」**（打字不受影响）或真正的「链路出了点问题」 |
| 8–15 | `/console` 语音提问 + 打断 | 说话即打断（ADR-004 前端 VAD）；打断后 SSE 立即停止产出 |
| 15–20 | `/` 学习目标 → `/insights` 学习洞察 | 统计**真算**：往 localStorage 塞已知记录，KPI 与话题聚类随之变化（`scripts/verify_frontend_pages.py` 就断言这点） |
| 20–24 | `/library` 数字人资产 | 形象预览绑定真实产物（`/media/*`）；后端不可达时显示**降级态**，不假装在线 |
| 24–28 | 工程视图 `/studio` `/metrics` `/ledger` | 实时延迟分解、口型片段、队列水位、`lip_dropped`；每行指标的「口径 + 环境 + 脚本」三要素 |
| 28–30 | `docs/03-决策/` | 每个关键选择的被否方案与实测依据（ADR-005/006/010） |

## 验证与自检

四个入口，都是「真跑」而不是「能编译就行」：

```bash
# 1) 后端单测（76 项，含口型编码器真实内容回归）
backend/.venv/Scripts/python.exe -m pytest backend/tests -q

# 2) 文档体系校验（文档地图登记、命名、类型闭集；不通过 = FAIL）
python scripts/validate_docs.py

# 3) 前端页面验证（无头 Chrome 真开页面断言：统计真算 / 主题 token 生效 /
#    降级态 / 旧占位路由已下线 / 对话页真实回答 —— 需后端在 8010 上跑着）
#    先起前端（另开一个终端）：cd frontend && npm run dev   # http://127.0.0.1:5173
VERIFY_BASE_URL=http://127.0.0.1:5173 backend/.venv/Scripts/python.exe scripts/verify_frontend_pages.py
#    不设 VERIFY_BASE_URL 则默认打 4173（npm run preview）

# 4) 前端样式一致性（CSS 里有没有已经没人用的孤儿类 / 幽灵选择器）
#    只需前端 dev server，不需要后端与任何凭据
backend/.venv/Scripts/python.exe scripts/verify_frontend_styles.py
#    另可加 --no-browser 只做静态扫描（不起浏览器，秒级出结果）
```

> 上面第 1、2、4 条已在 `2026-09-28` 实测通过（`76 passed` / `PASS: 全部文档合规` /
> `PASS：无孤儿类，活跃样式生效`）；第 3 条需要真实 LLM + TTS 凭据与前端 dev server，
> 本机环境不具备时未跑——脚本自身的前置检查会先报「5173 未就绪」而不是给出误导性结论。

> **测试为什么把临时目录放在仓库内**：口型编码器要把 MP4 落盘，因此依赖临时目录；
> 而受限/沙箱化环境常拒绝随机后缀目录、云 GPU 的系统盘又很小。
> `backend/tests/conftest.py` 把 `LIP_TMPDIR` 指到 `backend/.tmp-lip/`（已 gitignore、跑完自清），
> 编码器本身也支持用 `LIP_TMPDIR` 覆盖、并优先复用其下固定工作目录——
> 于是「测试能不能跑」不再取决于宿主机的 temp 权限。

## 目录结构

```
backend/    FastAPI 后端（api / asr / brain / tts / lip / eval）+ deploy（云侧口型服务与运维脚本）
frontend/   React 前端（产品视角 4 页：学习目标 / 对话辅导 / 学习洞察 / 数字人资产；
                       工程视图 3 页：链路工作台 / 指标看板 / 评估台账）
scripts/    文档校验（validate_docs.py）· 前端页面验证 · mermaid 图表校验
docs/       全部文档（见下方文档地图）；文档规范见 docs/docs-guide.md
```

## 文档地图

| 文档 | 类型 | 状态 | 链接 |
|---|---|---|---|
| 需求与验收标准（MUST） | 需求 | 已定稿 | `docs/01-需求/PRD-实时交互数字人助手.md` |
| 架构方案 | 方案 | 已定稿 | `docs/02-方案/DESIGN-实时交互链路架构.md` |
| 打断时序与状态机 | 方案 | 已定稿 | `docs/02-方案/DESIGN-打断时序与状态机转移表.md` |
| 接口与协议规范 | 方案 | v1.10 | `docs/02-方案/SPEC-接口与协议规范.md` |
| 决策记录（只增不改） | 决策 | 10 篇 | `docs/03-决策/ADR-*.md` |
| 进度 | 进度 | 每阶段一份 | `docs/04-进度/PROGRESS-*.md`（最新：`PROGRESS-2026-09-28-测试临时目录治理与演示口径对齐.md`） |
| **评估台账（指标唯一入口）** | 台账 | 演进 | `docs/eval-history.md` |
| 环境记录与踩坑 | 运维 | 演进 | `docs/RUNBOOK-环境记录.md` |
| 文档总索引 | — | — | `docs/README.md` |
