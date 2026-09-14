# 口型（MuseTalk）服务化验证报告

> 日期：2026-09-14
> 环境：云 GPU AutoDL RTX 4090 24G（实例 f1e847b1b7，镜像 PyTorch 2.0.0 / py3.8 / cu118）；本机 Windows 侧仅编排
> 交付物：`backend/deploy/lip_service.py`（云侧服务）+ `backend/src/lip/musetalk_engine.py`（本机客户端）
> 契约：SPEC §2.1 `POST /api/v1/lip/infer`；响应含 `frames[] / gpu_memory_mb / infer_fps`

## 1. 结论摘要

| 项 | 实测值 | 说明 |
|---|---|---|
| GPU 生成侧吞吐 | **92 fps** | 8s 音频 200 帧，纯 UNet+VAE decode（无逐帧落盘） |
| CPU 融合侧吞吐 | **30 fps** | resize + `get_image_blending` + JPEG 编码，704×1216 |
| 端到端（服务进程内） | 9175 ms / RTF **1.115** | 8s 音频；串行 = 生成+融合累加 |
| 冷启动首请求 | RTF 2.78（2s 音频） | 首请求含 cuDNN autotune；第 2 次起降到 RTF ~1.08 |
| 显存占用 | **7064 MiB** | 含模型 + 536 帧 avatar 缓存（`torch.cuda.max_memory_allocated`） |
| 云↔本机通道带宽 | **0.96 MB/s** | 20 MB 实测；SSH 隧道 |
| 实例原生公网带宽 | 6.6 ~ 18 MB/s | Cloudflare 6.6 / 阿里云镜像 18 |

**关键判断**：
1. V-01 记录的 19.07 fps **不是 GPU 生成能力**，而是被逐帧 PNG 落盘（拼接链路）拖累；服务化去掉该链路后
   生成侧为 **92 fps = 25fps 实时的 3.7 倍**，与 RUNBOOK 坑 13 的预判一致。
2. 去掉落盘后瓶颈**转移到 CPU 融合侧（30 fps）**，以及**跨机传输（0.96 MB/s）**。

## 2. 服务化做了哪些改动（与官方 `scripts/realtime_inference.py` 的差异）

| 项 | 官方脚本 | 本服务 | 依据 |
|---|---|---|---|
| 模型/avatar 加载 | 每次进程启动重新加载（~21s） | 常驻内存，进程内单例 | 服务化必需 |
| 帧输出 | 逐帧写 PNG → ffmpeg 拼接 | 内存融合 → JPEG base64 直出 | 去掉 I/O 瓶颈（见 1） |
| 音频输入 | `AudioProcessor.get_audio_feature(wav_path)` 读文件 | 直接吃 16k float32 数组 | 省临时文件往返 |
| 预处理 | `preparation=True` 时检测/landmark/VAE 编码 | 复用 `results/avatars/avator_1` 缓存 | 等价 `preparation=False` |

## 3. 请求路径耗时分解（2.3s 音频 / 57 帧，稳态）

| 阶段 | 耗时 | 占比 |
|---|---|---|
| 音频特征（whisper mel + chunk 切分） | 17.5 ms | 0.4% |
| GPU 生成（pe → unet → vae.decode） | 2164 ms (26.3 fps) | 53% |
| CPU 融合 + JPEG 编码 | 1914 ms (29.8 fps) | 47% |
| **服务端合计** | **4096 ms** | — |
| 传输（5.87 MB @ 0.96 MB/s） | ~6100 ms | 客户端观测 |

> 端到端（本机发起）实测 10098 ms；服务端只占 41%，**传输占大头**。

## 4. 端到端链路验证（SSE 全链路，`backend/eval/verify_e2e_lip.py`）

提问「运费怎么算」，本机 → LLM → TTS → 云 GPU 口型：

| 项 | 值 |
|---|---|
| SSE 事件统计 | thinking 1 / brain_token 24 / tts_audio 37 / **lip_frame 184** / done 1 |
| 帧内容 | 704×1216（= 形象原分辨率，符合 SPEC §4.3） |
| pts_ms | 0 … 7320，单调递增 ✓ |
| TTS 首次出声 | 1234 ms |
| 口型首帧到达 | 32402 ms ← **受传输与融合瓶颈** |
| done.lip_error | `null`（无错误） |
| done.lip_infer_fps | 66.5 |

## 5. 未解决 / 待决策

1. ✅ **跨机传输 0.96 MB/s → 已决策**：逐帧 JPEG（15.52 MB/8s）在此带宽下不可用（7s 回答 ≈ 14 MB ≈ 15s 传输）。
   已按 SPEC 预留的 P2 方向落地 **H.264 整句片段**（41×，387 KB）——见 **ADR-005** 与 SPEC v1.2 修订。
   `lip.transport` 保留 `frames` 兼容路径用于 A/B 对照。
2. **CPU 融合 30 fps**：位于服务端串行路径。线程重叠实验结论**存疑**（测量时有第二个引擎进程共存抢 GPU，
   见 RUNBOOK 坑 15），需在干净环境复测。H.264 后该步变为"融合→编码器"，编码本身也有成本，**待云 GPU 复测融合+编码合计耗时**。
3. **首帧延迟未测**：SPEC 指标「口型首帧 < 400ms」需在服务端加逐帧时间戳（当前只测整段）。
4. **服务内 H.264 编码未在云上验证**：编码器已在本机用**真实产物**验证（200 帧 → h264/704×1216/yuv420p/moov 前置，
   体积 274KB vs 云端直出 387KB），但"服务进程内编码 + 传输"链路的真实耗时需云 GPU 复测。
