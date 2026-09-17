# 评估台账（eval-history）

> **量化指标唯一入口**（docs-guide §4）：所有实测指标只登记在此，其他文档引用时写"见评估台账对应行"。
> 只记**实测值**，不复制计划/目标值（计划值在 PRD/DESIGN，引用不复制）。
> 每行必须可溯源：口径（怎么测的）+ 环境（什么机器）+ 来源（哪个脚本/报告）。

| 日期 | 指标 | 数值 | 口径/场景 | 环境 | 来源 |
|---|---|---|---|---|---|
| 2026-09-01 | — | — | 台账开档：五维指标行待 V-01~V-05 回填 | — | EVAL-P1-验证脚本清单 |

## 五维指标登记表（待 V-01~V-05 回填，见 EVAL-P1-验证脚本清单.md）

| 维度 | 指标 | 数值 | 口径/场景 | 环境 | 来源 |
|---|---|---|---|---|---|
| 延迟 | ASR 单块处理 | **166ms**（RTF 0.27） | 600ms/块流式，CPU；首字端到端≈766ms（V-03，2026-09-02） | 本机 | eval/reports/funasr_streaming.json |
| 延迟 | ASR 上行往返 | **~250ms/片**（含 HTTP；模型加载首片 22s 除外） | 600ms PCM 分片经 `POST /api/v1/asr/chunk`，真实测试集 7 条全对（2026-09-14） | 本机 CPU | eval/reports/asr_endpoint_check.md |
| 准确率 | ASR 流式模型字准率（干净音频） | **100%**（7/7 全对，CER 0.000） | 固定测试集 `data/testset/audio` 7 条，按 600ms 分片喂流式模型（2026-09-17） | 本机 CPU | eval/reports/asr_model_compare.json |
| 准确率 | 流式 vs 离线模型（two-pass 判据） | 精度**打平**（均 100%）；耗时 **流式 1249ms / 离线 557ms** 每条 | 同一 7 条各跑两遍（均排除首次模型加载）。**结论：干净音频上 two-pass 无精度收益**（+0pt / 代价 +557ms）；是否在真实麦克风场景有收益待真人样本判定（2026-09-17） | 本机 CPU | eval/reports/asr_model_compare.json |
| 延迟 | LLM 首 token | **~740ms**（503~1108ms 波动） | deepseek-chat 流式首 token，3 次实测（V-04，2026-09-02） | 本机 | eval/reports/llm_streaming.json |
| 延迟 | TTS 首包 | **618ms**（探针实测；目标 300ms → 未达标） | CosyVoice v2 WebSocket，16k PCM 首片到达（V-02 探针，2026-09-14） | 本机 | eval/reports/e2e_latency.json |
| 延迟 | 口型首帧 | 待测 | MuseTalk 首帧（V-01）。realtime 吞吐已测（19.07fps/52.4ms 每帧，见资源行），但首帧延迟本脚本无独立时间戳，待插桩测量 | 云 GPU | eval/reports/musetalk_validation.json |
| 延迟 | 端到端 | **1183ms（mean，6 轮；min 977 / max 1407）** | 文本输入→首个 tts_audio（不含 ASR/口型）；PRD 目标 <2s（首包口径）→ 达标（V-06，2026-09-14） | 本机 | eval/reports/e2e_latency.json |
| 延迟 | 端到端（流水线 A/B 归因） | **流水线净收益 45.8ms**（重叠窗口 A 45.8ms vs B -0.3ms） | 同批问题 6 轮 A/B：overlap=true vs false；端到端表面差 158ms 中 143ms 是 LLM 侧波动，**不可归因于优化**（2026-09-14） | 本机 | eval/reports/e2e_latency_pipelined.json / _serial.json |
| 音画同步 | 时差 | 待测 | 音频能量 vs 口型运动互相关（V-05） | 本机 | eval/reports/sync_measure.json |
| 音画同步 | 口型片段到达偏差（整句送检，**改造前**） | 首片 **+5274ms**；均值 +999ms，最大 +17171ms（3 句回答） | `late_ms = 片段到达时的音频时钟 − start_ms`；正=晚到。**两个真因：`localhost` 解析 2s + 等整句合成完**（2026-09-14） | 本机（假口型服务） | eval/verify_lip_sync.py |
| 音画同步 | 口型片段到达偏差（按 1s 分片送检，**改造后**） | 首片 **+205ms**；后续 13/13 片**全部提前到达**（−442 ~ −7498ms） | 同一问题同口径；负值=提前到达，前端按 `start_ms` 排队即同步（ADR-006，2026-09-14） | 本机（假口型服务） | eval/verify_lip_sync.py |
| 延迟 | 本机 `localhost` 名字解析开销 | **~2030 ms/次**（`127.0.0.1` 对照 3~6ms） | 四组对照：requests/urllib × localhost/127.0.0.1，各 3 次（2026-09-14） | 本机 | eval/probe_call_path.py |
| 音画同步 | 打断响应 | 待测 | 插话→数字人停止（V-06 + T4 条目） | 云 GPU | eval/reports/interrupt_measure.json |
| 质量 | 口型人工评分 | 待测 | 1-5 分，固定抽查 5 条（见 EVAL-测试集定义 §4.1） | 人工 | eval/reports/quality_score.json |
| 稳定性 | 连续对话时长 | 待测 | 30 分钟不掉帧（P4 压测） | 云 GPU | eval/reports/stability.json |
| 稳定性 | 掉帧率/失败率 | 待测 | 压测脚本 | 云 GPU | eval/reports/stability.json |
| 并发 | 单卡路数 | 待测 | 并发脚本（P3） | 云 GPU | eval/reports/concurrency.json |
| 资源 | GPU 显存 / FPS | **显存 4828 MiB（4.7G）；UNet 6.06 it/s（批 25）** | MuseTalk v1.0 离线推理，704×1216@25fps，1500 帧全长跑通 exit=0（V-01 normal 模式，2026-09-02） | 云 GPU（AutoDL 4090 24G） | eval/reports/musetalk_validation.json |
| 资源 | GPU 显存 / FPS（realtime 模式） | **稳态 19.07 fps（52.4 ms/帧），RTF 0.763** | MuseTalk v1.0 realtime，1500 帧全长，含逐帧 PNG 落盘；25fps 预算 40ms/帧，缺口 31%（V-01 realtime，2026-09-13） | 云 GPU（AutoDL 4090 24G） | eval/reports/musetalk_validation.json |
| 资源 | 口型 GPU 生成吞吐（服务化） | **92 fps**（8s/200 帧） | MuseTalk 服务化，去掉逐帧落盘后的纯生成侧（pe→unet→vae.decode）；**证明 V-01 的 19.07fps 是 I/O 瓶颈非算力**（2026-09-14） | 云 GPU（AutoDL 4090 24G） | eval/reports/lip_service_check.md |
| 资源 | 口型 CPU 融合吞吐 | **29.6~31 fps** | resize + get_image_blending + JPEG 编码，704×1216（2026-09-14） | 云 GPU（AutoDL 4090 24G） | eval/reports/lip_service_check.md |
| 资源 | 口型服务显存占用 | **7064 MiB** | 模型 + 536 帧 avatar 缓存常驻（torch.cuda.max_memory_allocated，2026-09-14） | 云 GPU（AutoDL 4090 24G） | eval/reports/lip_service_check.md |
| 资源 | 口型传输载荷（H.264 整句片段） | **387 KB / 8s 音频**（同帧逐帧 JPEG 15.52 MB → **41×**） | CRF26 + veryfast，704×1216@25fps；同批帧 ffmpeg 直出实测。**服务内编码路径待云 GPU 复测**（ADR-005 落地日实例已关机） | 云 GPU（AutoDL 4090 24G） | eval/reports/lip_service_check.md |
| 资源 | 云↔本机通道带宽 | **0.96 MB/s 下行 / 0.46 MB/s 上行** | 20 MB 文件实测 SSH 隧道；对比实例原生公网 6.6~18 MB/s（2026-09-14） | 本机↔云 | eval/reports/lip_service_check.md |
| 延迟 | 口型端到端（服务进程内） | **9175 ms / RTF 1.115**（8s 音频 200 帧） | 生成+融合串行；冷启动首请求 RTF 2.78（含 cuDNN autotune），稳态 RTF 1.08（2026-09-14） | 云 GPU（AutoDL 4090 24G） | eval/reports/lip_service_check.md |
| 2026-09-14 | 端到端（含口型链路） | **口型首帧 32376 ms**；TTS 首次出声 1234 ms | 真实提问「运费怎么算」SSE 全链路，184 帧/7.3s 语音；**瓶颈为隧道传输 0.96MB/s**，非推理（2026-09-14） | 本机 + 云 GPU | eval/verify_e2e_lip.py |
| 2026-09-15 | 音画同步 | 打断（**真实链路端到端**：SSE 停止产出） | interrupt → 流结束 **5 ms**；打断后 `tts_audio` **0 条**；未收到 `done` | 真实 DeepSeek + CosyVoice；首片音频到达（1.999s）即插话打断；`eval/verify_interrupt_e2e.py` | 本机 | backend/eval/verify_interrupt_e2e.py |
| 2026-09-15 | 延迟 | 打断（**后端侧分量**） | **median 4.43ms / p95 6.2ms / max 15.5ms**（50 次） | `POST /session/{id}/interrupt` → 状态机 SPEAKING→INTERRUPTED 完成；**ASGI 内存直连**，不含前端 VAD 帧延迟与网络往返 → 端到端打断响应**仍待人工实测** | 本机 | backend/eval/reports/interrupt_path.json |
| 2026-09-15 | 延迟 | ASR 端点阈值余量（离线标定，**非真实麦克风**） | 底噪 **≤0.011 正常收尾**；**0.015 时端点永不触发**（用户说完数字人不响应） | 复现前端判定逻辑（`SILENCE_RMS=0.012`/`SILENCE_MS=1200`）+ node 忠实执行 `mic-processor.js`；合成均匀底噪 | 本机 | backend/eval/reports/asr_endpoint_calibration.json |
| 2026-09-15 | 质量 | 前端 worklet RMS 口径（一致性校验） | **worklet/理论整帧 RMS = 0.993~1.000**（修正前 0.814；瞬态场景 0.308 → **0.998**） | node 忠实执行 `mic-processor.js` 原文件 vs Python 整帧窗口 RMS，9 场景逐一对拍 | 本机 | backend/eval/reports/asr_endpoint_calibration.json |
| 2026-09-15 | 音画同步 | 打断判据去抖（离线） | 瞬态场景：命中 1 帧 / **触发 0 次**（legacy 1 次）；回声场景：37 帧 → **17 次** | legacy（固定 0.036 单帧）vs target（`max(底噪×8,0.03)` 连续 2 帧） | 本机 | 同上 |
| 2026-09-15 | 延迟 | ASR 端点阈值自适应（修复效果） | **底噪 0.015 场景：无端点 → 3.1s 正常收尾**；其余 6 场景无回归 | 离线标定脚本三列对照（固定阈值 / 自适应 / 100ms 窗口 RMS）；阈值 = max(底噪×3, 0.004) | 本机 | backend/eval/reports/asr_endpoint_calibration.json |
| 2026-09-15 | 音画同步 | 打断判据命中率（离线） | 用户语音 **23.5%** 帧命中；**模拟数字人外放 33.0%** 帧命中 | 判据 `rms > 0.036` 的命中帧占比；外放用另一段测试集音频代替 → 判据本质是"有声音检测"，**不是插话检测**，AEC 是其成立前提 | 本机 | 同上 |
| 资源 | GPU 小时成本 | 待测 | 按周累计（PRD §4 风险） | — | 成本台账（记账本） |

> 登记规则：V 脚本跑出数字后，把"数值"列从"待测"改成实测值，并在"日期"列登记；一行只记一次实测（新实测覆盖旧值时保留旧值行，追加新行，不覆盖历史）。
