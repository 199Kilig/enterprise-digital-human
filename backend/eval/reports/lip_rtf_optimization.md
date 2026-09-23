# 口型 RTF 优化与流式时间线验证报告

> 日期：2026-09-23
> 环境：云 GPU AutoDL RTX 4090 24G（ffmpeg 4.2.7，NVENC 不可用）；本机 Windows 编排 + headless Chrome（CDP）
> 交付物：`backend/deploy/lip_service.py`（融合去 PIL 化）+ `backend/eval/verify_lip_stream_timeline.py`（到达时间线验证）
> 关联：ADR-010（决策：走实现优化不走架构重写）、RUNBOOK §3.20（环境记录）、ADR-005 / ADR-006（上游约束）

## 1. 结论摘要

| 项 | 优化前 | 优化后 | 说明 |
|---|---|---|---|
| 单片 `total` | 1065 ms | **711 ~ 737 ms** | 1s 音频 / 25 帧 |
| **RTF** | **1.065（>1）** | **0.72 ~ 0.74（<1）** | <1 = 口型产出快于音频播放，队列不再积压 |
| 融合耗时 | 425 ms（17.0 ms/帧） | **116 ms（4.65 ms/帧）** | 纯 numpy 替代官方 PIL 实现，**3.6×** |
| 融合吞吐 | 29.6 ~ 31 fps | **~215 fps** | 同上 |
| 端到端（浏览器，含页面处理） | 95 s（历史测量） | **29.8 s** | `lip_video` 事件跨度 11.8 s |
| 分片载荷 | 387 KB / 8s（整句） | **73 ~ 75 KB / 片** | 真实对话实测（较历史记录小 6 倍） |
| 等价性 | — | **逐像素 maxdiff = 0** | 新旧融合实现输出完全一致 |

**核心判断**：本链路瓶颈在**融合的实现方式**，不在模型产出粒度、不在传输协议、不在算力。
把官方 `get_image_blending`（PIL）换成纯 numpy 等价实现，即让 RTF 由 1.065 降到 0.71，
**并连带使"~80 秒缺口"自行消失**（真因是上游 rtf > 1 造成的队列背压，见 §4）。

## 2. 请求路径耗时分解（1s 音频 / 25 帧）

| 阶段 | 优化前 | 优化后 | 占比 | 可优化空间 |
|---|---|---|---|---|
| audio（whisper 特征） | 12 ms | ~10 ms | 1% | 已快 |
| gen（GPU 推理 25 帧） | 290 ms @86fps | 299 ~ 305 ms @82~84fps | 27% | 已接近算力上限 |
| **blend（CPU 融合）** | **425 ms** | **~116 ms** | 原 40% | ★ 本次解决 |
| 编码（libx264 veryfast crf26） | ~338 ms | ~338 ms | 32% | NVENC 不可用（见 §3） |
| **合计** | **1065 ms** | **711 ~ 737 ms** | — | — |

> 注：bench 脚本中曾出现 `gen 3326 ms`，属**冷启动假象**（cuDNN autotune / kernel 编译），
> 线上热态为 290 ~ 305 ms；尾片在 GPU 空闲后首调也会掉到 8.5 fps（`total 1847 ms`），同因。

## 3. 三条被实测否掉的方向

| 方向 | 实测 | 判定 |
|---|---|---|
| **融合搬到 GPU** | PIL 413 ms / torch GPU 394 ms（**1.0×**）/ numpy 116 ms（3.6×） | ✗ 每帧需过 PCIe 约 **5.7 MB**（ori 2.57 + face 0.17 + mask 0.39 上行、out 2.57 下行），而真实计算仅 362×362×3；**瓶颈是搬运与分配，非算力**；且结果必须回 CPU 给 ffmpeg，D2H 无法避免 |
| **编码切 NVENC** | `h264_nvenc` 失败：`OpenEncodeSessionEx failed: unsupported device (2)` / `No NVENC capable devices found` | ✗ 云上 ffmpeg 4.2.7（2019）的 NVENC SDK 不认 RTX 4090（Ada，2022）；升级 ffmpeg 会危及已跑通镜像（ADR-001 约束），代价 > 收益 |
| **融合与 GPU 推理线程重叠** | 2026-09-14 实测：GPU 51.4 fps → 23.5 fps，端到端 7.8 s → 9.4 s | ✗ 线程争用反而更慢（RUNBOOK 坑 15，代码内注释保留） |

**官方 PIL 实现的每帧开销来源**（改 numpy 的依据）：`Image.fromarray(image[:, :, ::-1])` 的负步长切片
**强制拷贝整张 2.5 MB**、`convert("L")` 每帧重做（mask 实为固定 3 通道）、`paste(..., mask)` 走 Python 层
解释执行 —— 每帧 4~5 次全图级复制。纯 numpy 用张量运算一次表达，无中间对象。

## 4. 流式到达时间线（"~80 秒缺口"溯源）

上一轮（rtf 1.065 时代）曾观测：后端 15.4 s 就把 11 片全"发出"，前端却 95 s 才
`createObjectURL`，一度怀疑卡在前端 JS。本次逐层排除：

| # | 路径 | `lip_video` 到达跨度 | 结论 |
|---|---|---|---|
| 1 | 本机 socket 直连 8010 | 4.5 s → 17.2 s（12.7 s） | 后端发送侧清白 |
| 2 | 本机 socket 经 Vite 5173 代理 | 4.4 s → 17.3 s（12.8 s） | dev 代理清白 |
| 3 | 带浏览器 header（`Accept-Encoding`/UA）重测 | 12.5 s / 17.7 s | HTTP 编码清白 |
| 4 | **真实浏览器（EventSource）** | **18.1 s → 29.8 s（11.8 s）** | 与 socket 一致 |
| 5 | **浏览器内 `createObjectURL`** | **与事件到达仅差 3 ms** | 前端读取与解析清白 |
| 6 | 回头看队列水位 | 峰值正好顶到 `queue_max = 8` | ★ **背压确诊** |

**机制**：rtf > 1 时口型产出（1065 ms/片）慢于音频播放（1000 ms/片），`lip_out_q` 持续积压至上限，
叠加前端读背压，把整条 SSE 的 `yield` 拖慢 —— 表现如同"前端收得慢"。rtf 降至 0.71 后队列不再积压，
数据顺畅流出。**同一参数的改善连带修好两个症状，前端代码一行未改。**

## 5. 未解决 / 待决策

1. **首帧延迟仍未测**（台账"延迟｜口型首帧"仍为待测）：SPEC 指标「口型首帧 < 400 ms」需在服务端
   加逐帧时间戳，当前只测整段/单片。
2. **音画绝对对齐未处理**：`useLipVideo` 的策略是"片段到达即从头播、不假装同步"，
   口型与语音的相位一致性（`audio_padding_length_left/right = 2`，即 80 ms 边界上下文是否足够）尚未度量。
   **建议对照实验**：同一段音频分两次送（整段 vs 按 1 s 分片），比较拼接处口型连续性。
3. **丢帧观测闭环缺失**：`qsize` 峰值曾贴 `queue_max = 8`，前端零消费 `lip_dropped` 事件，
   丢帧不可观测。
4. **TTS 账户欠费（本次验证时发现）**：验证末期链路稳定复现 `error` 事件，
   内容为 `{"code":"TTS_ERROR", "error_code":"Arrearage", "error_message":"Access denied,
   please make sure your account is in good standing."}`。后端、云口型（`reachable: true`）、
   LLM 均正常，**仅 TTS 出账失败**。充值后应按本报告脚本复测以确认完整时间线。
   > 附注：因欠费，`verify_lip_stream_timeline.py` 的完整时间线未能重新产出；
   > §1 与 §4 的数字来自欠费前的实测（同日早先，SSE 135 行、云侧 rtf 0.719~0.737 五连测）。
   > 脚本自身逻辑已验证：能正常跑通、并正确捕获 `error` 事件。
5. **云侧升级时的等价性复核**（ADR-010 负面后果）：融合实现已由"引用官方库"变为"自行维护"，
   升级 MuseTalk 后必须复核 `blend_frame` 与官方语义是否仍一致。

## 6. 复现方式

```bash
# 云侧服务（融合实现在此文件）
bash backend/deploy/restore-cloud-lip.sh <SSH端口>     # 自动 md5 比对上传 + 拉起 + 建隧道

# 到达时间线（口径 A：本机 socket；口径 B：真实浏览器）
python backend/eval/verify_lip_stream_timeline.py            # A
python backend/eval/verify_lip_stream_timeline.py --browser  # A + B
python backend/eval/verify_lip_stream_timeline.py --json     # 落 reports/lip_stream_timeline.json
```

**远端 bench 脚本**（不入库，云上临时）：`lip_bench_encode.py`（编码器对照）、
`lip_bench_blend.py` / `lip_bench_blend2.py`（融合实现对照：PIL / torch GPU / numpy）。
**注意**：云上跑 bench 必须 `nohup ... &` 分离（SSH 断开会被 SIGHUP 杀掉），
且输入音频要截短（拿 48s 全长跑 `list(gen_frames())` 会撑爆 24GB 显存）。
