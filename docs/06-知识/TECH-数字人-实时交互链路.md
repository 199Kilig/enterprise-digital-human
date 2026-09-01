---
title: TECH：数字人 — 实时交互链路技术栈
type: tech
status: 学习中
date: 2026-09-01
updated: 2026-09-01
tech: 数字人 / 实时交互
links: [../01-需求/PRD-实时交互数字人助手.md, ../02-方案/DESIGN-实时交互链路架构.md, ../03-决策/ADR-003-口型模型选型.md]
---
> 文件名：`TECH-数字人-实时交互链路.md`
>
> 类型：tech
> 状态：学习中（链路概念已懂，模型实操待 V-01~V-05）
> 读者：未来的我（复盘 / 面试前回忆）
> 结论：我用 ASR→LLM→TTS→口型→渲染的全链路自研编排（FastAPI 状态机），亮点是延迟预算分解 + 流式并行 + 唯一时钟源音画对齐 + VAD 打断，对应量化指标（端到端 < 2s / TTFT < 1s / 音画时差 < 50ms / 打断 < 200ms）。

## 1. 触发场景（HUMAN）

**当时的需求**：把"企业级数字人"写到简历里要有真东西讲。市面上的"数字人"大多是离线内容生成（HeyGen 录视频），实时交互这块工程门槛高、岗位稀缺，正好差异化。

**为什么需要这套技术**：实时交互数字人 = 串行链路（ASR→LLM→TTS→口型→渲染），每段都是独立技术领域，**延迟是核心矛盾**——用户说完话到数字人开口，业界开源方案首包普遍 ~3s（"像电话延迟"），本项目目标 < 2s。

**面试讲法**：背景——"开源级联方案延迟秒级、交互体验差"；切入——"我从 0 到 1 自研编排（不引开源整包，ADR-002），量化指标驱动优化"。

## 2. 技术要点（MUST）

### 2.1 链路五段（对齐本项目选型：模型全开源，编排自研）

| 段 | 作用 | 本项目选型 | 依据 |
|---|---|---|---|
| ASR 语音识别 | 声音→文字（流式） | FunASR `paraformer-zh-streaming` + fsmn-vad（开源，ModelScope 下载） | DESIGN §3.2 / EVAL-P1 V-03 |
| LLM 对话大脑 | 文字→回答文字（流式） | DeepSeek（云端优先，config.yaml 可切） | DESIGN §3.2 / EVAL-P1 V-04 |
| TTS 语音合成 | 文字→声音（流式） | **双路线**：云端 DashScope `cosyvoice-v1`（支持 word 时间戳）优先；开源 CosyVoice2（24kHz、双向流式但**无时间戳**）兜底 → 触发 Plan B | EVAL-P1 V-02 |
| 口型驱动 Lip Sync | 音频→嘴部视频帧 | **MuseTalk**（音频驱动，whisper mel 16k；face 区域 256²，输出=输入形象分辨率，25fps） | **ADR-003** |
| 渲染传输 | 视频帧推浏览器 | P1 Gradio 本地验证；P2 WebRTC（VP8/H.264 25fps，音频 Opus 16k） | DESIGN / SPEC §4 |

### 2.2 三个核心矛盾（差异化卖点）

- **延迟**：链路串行，每段都是瓶颈；解法=延迟预算分解（每段打点）+ **流水线并行**（TTS 首包即播，不等完整音频）——端到端不是各段串行累加
- **音画同步**：音频流和口型帧的时钟对齐；**唯一时钟源原则**：全链路以 TTS 时间戳为基准，lip/render 不独立维护时间轴（SPEC 核心原则）
- **高并发/成本**：口型依赖 GPU 推理，单路吃显存；云 GPU 按小时（ADR-001），推理服务化 + 显存/成本台账

### 2.3 关键概念（面试要能讲）

- **TTFT**（Time To First Token/首包）：从用户说完到第一个音/字出来；验收口径 = **音频首包**（端到端 <2s），口型首帧单独记录（音频驱动的口型必然晚于音频）
- **流式 vs 整段**：TTS 边合成边播 = 首包几百 ms；等整段合成完 = 1-2s，**流式是必须的**
- **VAD**（Voice Activity Detection）：检测用户插话 → 触发打断；本项目 P1 前端 AudioWorklet 实现 + HTTP 打断信号（ADR-004），P2 评估迁后端
- **word-level 时间戳**：音画同步的前提；开源 TTS 常不给 → **Plan B：FunASR SeACo-Paraformer 字级时间戳对齐**（EVAL-P1 V-02）
- **音画时差**：音频播放时刻 vs 对应口型帧渲染时刻的偏差；测量=音频能量包络 vs 口型运动强度做互相关（V-05，含工具自校准）
- **WebRTC vs HLS**：WebRTC 端到端 <500ms，HLS 直播延迟 3-10s，**实时交互只能用 WebRTC**（P2）

### 2.4 口型驱动方案速记（对齐 ADR-003 决策）

| 方案 | 特点 | 结论 |
|---|---|---|
| Wav2Lip | 老牌，效果一般（嘴型对但脸僵） | 兜底（显存 2-4G，V-01 失败预案） |
| SadTalker | 经典，效果+性能平衡 | ❌ 被否：生成实时性弱，难满足实时交互（ADR-003） |
| **MuseTalk** | **本项目主选**：实时性好（官方 30fps+ @V100）、社区活跃、流式时序有案例 | ✅ ADR-003 |
| EMO / Hallo | 阿里系，效果惊艳但模型重 | 研究前沿，工程岗不碰（PRD 非目标） |

## 3. 操作步骤（MUST）

```bash
# === 已验证（2026-09-01，真实输出）===

# 环境：uv venv + 依赖（uv 建的 venv 无 pip；装含 uvicorn 的包用 -e . 读 pyproject 依赖）
cd backend
uv venv .venv --python 3.11
uv pip install -e . && uv pip install pytest httpx
.venv/Scripts/python.exe -m pytest tests/ -v        # → 15 passed（状态机转移表全路径）

# V-05 音画测量自校准（PASSED，max_error 0ms）
cd eval && ../.venv/Scripts/python.exe verify_sync_measure.py --report-dir reports

# API 骨架联调（5 端点 6 点验证通过）
.venv/Scripts/python.exe -m uvicorn src.api.routes:app --port 8010

# === 待跑（依赖未装/资源未配，跑过回填真实输出，见 EVAL-P1）===

# V-03 FunASR 流式（本机，装 torch CPU + funasr）
uv pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
uv pip install funasr modelscope edge-tts
cd eval && python gen_test_audio.py                  # edge-tts 合成测试集 → 16k wav
python verify_funasr_streaming.py --wav ../../data/testset/audio/t1-01.wav

# V-01 MuseTalk 云 GPU（AutoDL 租用后） / V-02 TTS 时间戳（DashScope key）
# V-04 DeepSeek 流式 SSE（key 复用 Hermes 的）
```

## 4. 难点与解决（HUMAN）

**难点 1：流式 TTS 时间戳不可用 → 音画同步无锚点**
- 排查：开源 CosyVoice 无 word 时间戳（EVAL-P1 V-02 双路线验证结论）
- 解决：云端 DashScope cosyvoice-v1（带时间戳）优先；不可用则 Plan B = FunASR SeACo 字级对齐（零新增技术栈）；再不济降级"音频对齐"方案

**难点 2：口型"像但假"**
- 表现：嘴型对得上，但脸不动/眼神僵/像 PPT
- 解决：**不吹效果，差异化在延迟/同步/并发**——简历明说"行业通病，量化指标是工程能力而非效果"

**难点 3：云 GPU 成本失控**
- 表现：口型推理单路吃显存，云 GPU 24h 不关一个月烧几千
- 解决：按小时租用（ADR-001）、实验完即释放、**成本台账**（token + GPU 时长）；P3 用批处理/量化拉满单卡路数

**难点 4：用户插话打断**
- 表现：数字人还在说，用户插话，数字人不理
- 解决：VAD 检测（P1 前端 AudioWorklet）+ HTTP 打断信号 → 状态机 `SPEAKING→INTERRUPTED`，沿流水线反向传播 stop（TTS←Brain←ASR）+ 三段式缓冲清理；目标打断响应 <200ms（打断时序设计 v1.0）

**难点 5：测量工具自身会错（本项目真实踩坑）**
- 表现：V-05 自校准首跑 FAILED——负偏移测出 4460ms 偏差
- 排查：scipy `correlate(a,v)` 峰值 k = **v 领先 a** 的帧数（符号与原实现相反）；`mode='same'+n//2` 存在 ±1 帧换算歧义
- 解决：改用 `mode='full'` + 显式 lag 轴 + 取负；**先校准工具再归因系统**（DESIGN §5.4 坑 5）——自校准机制本身就是用来抓这种 bug 的

## 5. 亮点与代码锚点（MUST）

| 亮点 | 代码位置 |
|---|---|
| 状态机转移表实现（五态十事件，纯逻辑返回动作） | `backend/src/api/state_machine.py: SessionStateMachine.handle` |
| 模块数据契约（含 TTS sample_rate 字段 v1.1） | `backend/src/schemas.py: TtsSegment` |
| API 骨架（会话/打断/interrupt_done/SSE） | `backend/src/api/routes.py` |
| 音画时差测量 + 工具自校准（互相关，含 bug 修复） | `backend/eval/verify_sync_measure.py: measure_av_sync` |
| 固定测试集 27 条六类 + 标注口径 | `docs/eval/EVAL-测试集定义.md` |

## 6. 关联文档与后续

- 关联：[PRD](../01-需求/PRD-实时交互数字人助手.md) / [DESIGN](../02-方案/DESIGN-实时交互链路架构.md) / [SPEC](../02-方案/SPEC-接口与协议规范.md) / [ADR-003](../03-决策/ADR-003-口型模型选型.md) / [EVAL-P1](../eval/EVAL-P1-验证脚本清单.md)
- 后续：
  - **V-01~V-05 实操**：跑完回填 §3 真实输出，登记 `docs/eval-history.md` 台账
  - **待补笔记**：ASR / TTS / 口型 / WebRTC 各自单独 TECH 笔记（实操踩坑后写，避免泛滥）
  - **简历联动**：技术栈栏写"ASR / LLM / TTS / Lip Sync / WebRTC"；项目经验栏套"全链路自研编排 + 量化指标（延迟/音画/并发）"

---

*纪律：本文档是知识沉淀（个人资产），只记提炼后的成果，不记过程快照（快照进 `docs/_inbox/`）。写完在 `docs/06-知识/README.md` tech-index 台账登记一行。*
