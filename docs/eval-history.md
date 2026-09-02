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
| 延迟 | LLM 首 token | **~740ms**（503~1108ms 波动） | deepseek-chat 流式首 token，3 次实测（V-04，2026-09-02） | 本机 | eval/reports/llm_streaming.json |
| 延迟 | TTS 首包 | 待测 | 首包即播（V-02） | 云 GPU/本机 | eval/reports/cosyvoice_timestamp.json |
| 延迟 | 口型首帧 | 待测 | MuseTalk 首帧（V-01） | 云 GPU | eval/reports/musetalk_validation.json |
| 延迟 | 端到端 | 待测 | 用户说完→首帧（V-06） | 云 GPU | eval/reports/e2e_mock.json |
| 音画同步 | 时差 | 待测 | 音频能量 vs 口型运动互相关（V-05） | 本机 | eval/reports/sync_measure.json |
| 音画同步 | 打断响应 | 待测 | 插话→数字人停止（V-06 + T4 条目） | 云 GPU | eval/reports/interrupt_measure.json |
| 质量 | 口型人工评分 | 待测 | 1-5 分，固定抽查 5 条（见 EVAL-测试集定义 §4.1） | 人工 | eval/reports/quality_score.json |
| 稳定性 | 连续对话时长 | 待测 | 30 分钟不掉帧（P4 压测） | 云 GPU | eval/reports/stability.json |
| 稳定性 | 掉帧率/失败率 | 待测 | 压测脚本 | 云 GPU | eval/reports/stability.json |
| 并发 | 单卡路数 | 待测 | 并发脚本（P3） | 云 GPU | eval/reports/concurrency.json |
| 资源 | GPU 显存 / FPS | 待测 | MuseTalk 推理（V-01） | 云 GPU | eval/reports/musetalk_validation.json |
| 资源 | GPU 小时成本 | 待测 | 按周累计（PRD §4 风险） | — | 成本台账（记账本） |

> 登记规则：V 脚本跑出数字后，把"数值"列从"待测"改成实测值，并在"日期"列登记；一行只记一次实测（新实测覆盖旧值时保留旧值行，追加新行，不覆盖历史）。
