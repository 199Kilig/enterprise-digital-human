# prompts/ — 双读者机制的实操层

四个场景，四个文件：

| 场景 | 用哪个 | 何时用 |
|---|---|---|
| 让 AI **写**一份新文档 | write-doc.md | 新建 PRD/DESIGN/ADR/PROGRESS/RETRO 时 |
| 把已有文档**喂给** AI 当输入 | feed-agent.md | 给 agent 派活前，防止它通读叙事 |
| 把长文档**转写**成 AI 视图 | feed-agent.md 下半段 | 文档很长时，先投影再干活 |
| 把 `_inbox/` 素材**蒸馏**成正式文档 | distill.md | 阶段结束时，过程产物转成果文档 |

## 核心约定（四个 prompt 共同依赖）

- **MUST 区块**（验收标准/接口/约束/命令）= 事实，AI 必须提取、逐条满足
- **HUMAN 区块**（背景/叙事/解释）= 人读的，AI 投喂时忽略
- 文档必须先读 `docs/_templates/` 对应模板，严格按章节填；缺章节 = 不合格
- 文件名用 `类型-<中文语义短语>.md`（PRD- / DESIGN- / ADR-NNN- / PROGRESS-日期- / RETRO- / TECH-<技术>-<语义>）
- 新文档写完必须在 `docs/README.md` 文档地图登记一行；TECH 笔记登记到 `docs/06-知识/README.md` tech-index 台账
- **交付前过内容质量自检**（结论一句话/每条可验证/无藏事实/真实锚点/扫描测试；清单见 distill.md 与 write-doc.md）
- 生成后跑 `python scripts/validate_docs.py`，FAIL 就改到 PASS

## 提示

- 写文档时把 write-doc.md 里的 `{{类型}}` `{{标题}}` 替换成真实值，其余原样发给 AI
- 投喂纪律是**每次**给 AI 喂项目文档时的固定前缀，不要省
