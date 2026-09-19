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

# 跑 V-06 端到端延迟（会真实调用 DeepSeek + DashScope，消耗额度）
cd backend/eval && ../.venv/Scripts/python.exe verify_e2e_latency.py --runs 6 --report-dir reports
```

### 链路开发踩坑（LLM/TTS/流水线，2026-09-14）

1. **CosyVoice 必须用 v2**：`cosyvoice-v1` 即使显式传 `word_timestamp_enabled=true`，`sentence.words` 全为空；
   `cosyvoice-v2`（voice `longxiaochun_v2`）才返回 `{text, begin_index, end_index, begin_time, end_time}`。
   SPEC §4.3 把 word 时间戳定为音画同步硬前提 → config.yaml `tts.model` 只能是 v2。
2. **word 时间戳是「句内相对」的**：每句 `begin_time` 从 ~0 重新计数，直接拿去做口型调度必然错位。
   必须按句累计时长叠加句起点（`tts/cosyvoice.py` 的 `sent_base`）。
3. **同一句的 words 是「增量上报」的**：`sentence-begin` 事件只给前几个词，后续事件补齐。
   按句索引去重会丢词（实测 7.89s 音频只抓到 9 个词）；必须记录每句已发出的词数、只发新增部分。
4. **DashScope WS 连接可复用**（多任务同一连接实测 OK），握手 ~195ms 是每轮白付的成本 →
   `tts/cosyvoice.py` 维持长连接，连接失效才重建（实测 TTS 首包 418~513ms → 343~402ms）。
5. **SSE 端点用原始 WS 而非 SDK**：SDK 的 `additional_params` 无法透传 word_timestamp_enabled（实测无效）。
6. **A/B 归因的教训**：优化前后各 6 轮对比，端到端看着快了 158ms，但同期 LLM TTFT 自己从 656ms 掉到 512ms
   （波动 143ms）。**真正可归因的只有「重叠窗口」45.8ms**。做性能结论必须把被测段之外的波动剔掉，
   否则会把服务端抖动记成自己的功劳（见 `eval/reports/e2e_latency_*.json`）。
7. **`pipeline.overlap` 开关（config.yaml）**：保留串行模式是为了能重复做上面这个 A/B，别删。
8. **FunASR 模型首次加载耗时 ~22s**（进程内单例懒加载）→ 第一个语音请求会明显卡一下；
   演示前建议先打一次 `/api/v1/asr/chunk` 预热，别在评委面前等模型加载。
9. **ASR 分片粒度必须与 `chunk_stride` 对齐**（9600 样本 = 600ms @16k）：前端不足一块要先在本地攒够再发，
   否则模型对碎片化的输入会给出错误/空结果（`asr/streaming.py` 已按 600ms 攒块）。
10. **AudioContext 采样率可能被浏览器拒绝**：`new AudioContext({sampleRate:16000})` 在个别环境回落为硬件速率，
   此时若不重采样就把音频送 ASR，**识别结果会全错且不报错**（静默故障）。
   `useMicCapture.ts` 已加 `resample()` 兜底。
11. **VAD/端点归属（ADR-004）**：P1 由前端做——静音 1.2s 判定语句结束、说话时触发 `/interrupt` 打断；
   后端只负责"收到音频就识别"。别在后端加端点检测，那是 P2 的迁移决策（需改 ADR）。
   **静音阈值是自适应的**（2026-09-15）：`max(底噪估计×3, 0.004)`，底噪用只降不升的 min 跟踪器
   （`useMicCapture.ts` `floorRef`）——固定 0.012 在底噪 ≥0.012 的环境下会让端点**永不触发**
   （用户说完数字人不响应，且不报错）。改这段前先跑 `eval/verify_asr_endpoint.py` 看标定数据。

## 2. 端口约定

| 端口 | 用途 | 说明 |
|---|---|---|
| 8010 | backend API（P1 联调） | 8000/8001 易被本机其他项目占用，新服务避开 |
| 8002 | lip 推理服务（预留，SPEC §2.1） | 云 GPU 侧，见 config.yaml `lip.service_url` |

> 记忆坑：本机 3306/8000/5173 常被占；中文路径下 uvicorn `--reload` 不可靠（改码后手动重启）。

## 3. 云 GPU 环境（AutoDL 已租）

| 项 | 值/要求 |
|---|---|
| 平台 | AutoDL（内蒙 B 区，实例 `f1e847b1b7`） |
| SSH | `ssh -p 47618 root@connect.nmb1.seetacloud.com`（密码登录；公钥 `~/.ssh/id_ed25519` 已生成，未加控制台） |
| 镜像 | PyTorch 2.0.0 / Python 3.8.10 (ubuntu20.04) / CUDA 11.8（2026-09-02 换镜像后；满足 MuseTalk + mmcv 2.0.1 预编译轮子要求） |
| 目录 | 代码+权重在数据盘 `/root/autodl-tmp/digital-human/MuseTalk/`（持久；系统盘 30G 不够） |
| 权重 | 8.7G 全量就位（2026-09-02）：musetalk 3.2G / musetalkV15 3.2G / sd-vae / whisper / dwpose / syncnet / face-parse-bisent |
| 卡型 | 待定（建议 RTX 4090 按量；vGPU-32G ¥1.66/时 已否——1/8 算力分片不适合实时推理） |
| GPU 型号/CUDA | 有卡后补录 |

### AutoDL/MuseTalk 部署踩坑（2026-09-02，禁止重踩）

1. 官方 `download_weights.sh` 已过时：`huggingface-cli` 在 huggingface_hub 1.29 被移除 → 全部用 **`hf download`** 替代
2. `hf download` 走 xet 协议时报 `cas-server.xethub.hf.co 401` → **`export HF_HUB_DISABLE_XET=1`** 强制普通 HTTP
3. `gdown` 6.x 移除 `--id` 参数（直接传 ID）；且 **Google Drive 国内不可达**（Network is unreachable）
4. face-parse-bisent/79999_iter.pth（Google Drive 源）→ 改从 HF `AI2lab/face-parsing.PyTorch` 下（hf-mirror）
5. `hf download` 多个 `--include` 时**小 json 会静默漏下**（只下大文件）→ 每个文件单独 `--include` 补下
6. **ffmpeg 未预装** → `apt-get update && apt-get install -y ffmpeg`（V-01 前置）
7. **AutoDL 容器 `OMP_NUM_THREADS=0`**（libgomp 非法值报错）→ `export OMP_NUM_THREADS=16`
8. huggingface_hub 1.29 与 transformers 4.39 冲突 → **降级 `<1.0`**（0.36.2）
9. ~~**mmcv/mmdet/mmpose 版本死结（未解决，V-01 阻塞中）**~~ **已解决（2026-09-02 换镜像）**。真因记录：
   - **mmcv 2.0.1 在 torch 2.1.x 下无预编译轮子**（openmmlab 官方索引 `cu118/torch2.1.0` 只有 mmcv 2.1.0/2.2.0）→ pip 被迫源码编译 → 失败。源码编译失败另有环境原因：镜像默认 PATH **不含 `/usr/local/cuda/bin`（nvcc 不可见）**
   - 升 mmcv 2.1.0 路线被 mmdet 3.1.0/mmpose 1.1.0 拒绝（pin <2.1.0），死结成立
   - **破局 = 换镜像 PyTorch 2.0.0 / Python 3.8 / CUDA 11.8**（AutoDL 关机换镜像，数据盘保留）：`cu118/torch2.0.0` 索引下 **有** mmcv 2.0.1 cp38 轮子（74.4MB 直装零编译）。注意：**torch2.0.1 索引是空的，有轮子的是 torch2.0.0**（原候选 b 写"降 torch 2.0.1"是错的）
   - Python 3.8 依赖适配：gradio==5.24.0 需 ≥3.10 → 从 requirements 删除（demo UI，V-01 不用）；soundfile 0.12.1 需 ≥3.10 → **删掉 pin 让 pip 自解**（解出 0.13.1 兼容 py3.8，librosa 0.11.0 无需降版）
   - 最终验证：torch 2.0.0+cu118 / mmcv 2.0.1 / mmdet 3.1.0 / mmpose 1.1.0 / cv2 4.9.0 / numpy 1.23.5 / transformers 4.39.2 / diffusers 0.30.2 / tf 2.12.0 全部 import 通过，cuda_ok=True
10. **s3fd 人脸检测权重（85.7MB）adrianbulat 源站在云机上极慢（15-50kB/s，1h+）** → 本机下载（~450kB/s）后 sftp 上传到 `/root/.cache/torch/hub/checkpoints/s3fd-619a316812.pth`；首次推理前先备好，否则推理进程会卡在下载进度条
11. **换镜像后 ssh 有瞬时 banner reset**（连接被拒/重置 1-2 次后恢复）→ paramiko/ssh 加重试即可，非实例故障
12. **V-01 已通过（2026-09-02，4090）**：`inference.sh v1.0 normal` 等价命令跑通 exit=0，出片 yongen_yongen.mp4（8s）+ yongen_eng.mp4（60s/1500帧/704×1216@25fps/aac 音轨）；实测显存 4828MiB、UNet 6.06 it/s（批 25）、拼接 13 it/s。数据落档 `backend/eval/reports/musetalk_validation.json`，台账已回填。首帧延迟口径需 realtime 模式补测（normal 是离线批处理，不代表流式首帧）
13. **V-01 realtime 补测（2026-09-13，4090）**：`scripts.realtime_inference --version v1 --fps 25` 跑通 exit=0，出片 audio_0.mp4（8s）+ audio_1.mp4（60s/1499帧/704×1216@25fps）。**稳态 19.07 fps（52.4 ms/帧），RTF=0.763 < 1.0 —— 25fps 流式单卡单路追不上，每帧缺口 12.4ms**。1500 帧总耗时 78.64s；音频预处理：首次 1892ms、稳态 33.6ms；跑完显存释放回 4MiB。⚠️ 该值含逐帧 PNG 落盘 I/O，纯 GPU 生成侧明显更快（normal 模式 UNet 151 fps 生成），瓶颈在拼帧/落盘链路。未优化项：batch_size=20 可调、无 fp16/量化、无跳帧策略。**首帧延迟仍未测**（脚本无独立时间戳，需插桩）
14. **V-01 的 19.07fps 已证伪为"算力不足"（2026-09-14）**：口型服务化去掉逐帧落盘后，**纯 GPU 生成侧实测 92 fps**（= 25fps 实时的 3.7 倍）。瓶颈从来不在卡，而在拼帧/落盘链路。教训：**别把 I/O 瓶颈记成算力瓶颈**。
15. **跨机传输是硬约束（2026-09-14）**：本机↔云 SSH 隧道实测 **0.96 MB/s 下行 / 0.49 MB/s 上行**（20MB 文件），而实例原生公网 6.6~18 MB/s —— 瓶颈在 SSH 通道不在带宽。后果：逐帧 JPEG 方案（15.5 MB/8s）传输要 16.2s，**不可用**；同帧 H.264 CRF26 仅 387 KB（**41×**，0.39s）。SPEC §4.3 已写明视频用 VP8/H.264，§2.1 的 frame_b64 亦标注「JPEG（P1）/H.264（P2）」——**这是 P1→P2 传输升级的直接证据**。
    - **补充定位（同日，四组对照）**：① RTT 仅 **9ms**（排除高时延/窗口限制：1MB/s × 9ms = 9KB BDP，远低于默认窗口）；② 换加密算法无差别（默认 0.71 / aes128-gcm 0.92 / chacha20 0.91 MB/s）；③ 换客户端实现无差别（paramiko SFTP 0.96 vs 原生 OpenSSH scp 0.92）；④ **60MB 全程平坦 0.98~1.09 MB/s，无突发-限流拐点** → 是**硬性限速**而非链路拥塞。
    - 云机自身双向带宽：**下行 6.6~18 MB/s、上行 3.5~4.0 MB/s**（Cloudflare speed test）→ **云侧不是瓶颈**。
    - 本机侧佐证：连国内大镜像站（aliyun/ustc/tuna）的大文件下载**全部失败**（http=000 / WinError 10054），本机公网出口本身受限 → **瓶颈在本机这一侧的链路**，无法再细分到具体一跳（缺第二条参照链路）。
    - **换网对照（2026-09-14 晚，同脚本 `eval/verify_link_bandwidth.py`）**：

      | 网络 | RTT | 本机出口 | 单流 | 4 路并发 | 机制 |
      |---|---|---|---|---|---|
      | 校园网 | 19.6 ms | 被 reset（不可用） | 0.97 MB/s | 0.93 MB/s（不放大） | **硬限速**，平坦稳定 |
      | 另一网络 | 90.6 ms | 0.10 MB/s | 0.16 MB/s | 0.89 MB/s（放大 5.6×） | **高时延单通道窗口受限**，且波动大（复测 1/2/4/8 路 = 0.23/0.29/0.30/0.33 MB/s，同条件重复差 3~5 倍） |

    - 结论：**换网没有变好**（校园网更快更稳）。两个网络聚合上限都在 ~1MB/s 附近，但机制不同——校园是整形、另一网络是高时延+不稳定。**无论哪条，减载荷（H.264 41×）都是唯一解**，且校园网上 H.264 单句仅需 0.4s。
    - **⚠️ 归因修正（第三次测量后）**：三网对照显示 **~1MB/s 是这条跨机链路的共同上限**（校园 0.97 / 网络A 0.89并发 / 网络B 1.02，三次都落在 0.9~1.0）。两个不同网络给出同一个天花板 → **限制更可能在云机出口或中间路由，而不是某一个本机网络**。早前"瓶颈在本机侧"的推断据此**降级**（当时只依据"校园网连 CDN 被拦"这一条）。
    - **结论**：不用再换网络了；也不必优化隧道。要减传输量（H.264，41×）。若日后要彻底定位，需第三条参照链路（另一台机器/另一朵云）做同口径对照。
    - **一句话**：不要指望换网络解决，也不要优化隧道（加密/实现/并发都测过无效）；要减传输量。
16. **云上别跑第二个 MuseTalk 进程（2026-09-14）**：服务进程常驻占 7.9G，再起一个推理进程会在 VAE 解码时 CUDA OOM（实测 20.69 / 14.46 GiB）。**⚠️ 根因未完全定位**（按代码走查解释不了该进程为何自己申请到 14~20G；已确认的是：服务进程独占时连续 8+ 次请求从不 OOM）。做性能实验要么停服务，要么在服务内加 measure 接口。
17. **「线程重叠融合更快」结论存疑，勿引用（2026-09-14）**：首次测量（51.4→23.5 fps，结论"线程更慢"）时进程内有**残留/共存进程抢 GPU**，测量被污染；干净环境下 bench 自身又 OOM（见 16），未取得可信复测值。服务代码现为**串行**（保守且实测可用），CPU 融合并行化待专门实验。
18. **实例重建后 SSH host key 变更（2026-09-14）**：`ssh -L` 报 `REMOTE HOST IDENTIFICATION HAS CHANGED`，直接 `ssh-keygen -R "[connect.nmb1.seetacloud.com]:47618"` 清陈旧条目即可（重建实例会重新生成主机密钥，非攻击）。
19. **本机访问云服务用 SSH 隧道**：`ssh -N -L 8002:127.0.0.1:8002 -p 47618 root@connect.nmb1.seetacloud.com`（公钥已入 `authorized_keys`，免密）。隧道断了重启即可，服务本身不受影响。
20. **⚠️ 本机 `localhost` 名字解析要 ~2 秒 —— 服务互调一律写 `127.0.0.1`（2026-09-14 实测）**：同一服务同一台机器，四种组合对照（`eval/probe_call_path.py`）：

    | 调用方式 | 3 次耗时 |
    |---|---|
    | requests + `127.0.0.1` | 5.4 / 5.1 / 22.6 ms |
    | urllib + `127.0.0.1` | 3.7 / 4.0 / 2.9 ms |
    | requests + `localhost` | **2034 / 2028 / 2049 ms** |
    | urllib + `localhost` | **2029 / 2056 / 2050 ms** |

    与客户端库无关，**是名字解析**（疑似 IPv6 回退/慢解析路径）。踩坑现场：后端 `lip.service_url` 写作
    `http://localhost:8002`，每片口型白付 2 秒，直接叠加成"嘴比声音慢 2 秒"。
    **规则：本机服务互调（后端→口型服务、脚本→后端）一律写 `127.0.0.1`**；浏览器地址栏给人看的 URL 可继续用 localhost。
21. **音画偏差怎么量**：`python backend/eval/verify_lip_sync.py "问题"`——以首个 `tts_audio` 到达为音频时钟原点，
    打印每片的 `late_ms`（正=晚到/负=提前）与 TTS 合成速率。**判据：正值 >500ms 人眼可辨；负值=提前到达，排队等播不算问题**。
    排查顺序：先看 `call_ms`（口型 HTTP 调用耗时，正常应为个位数 ms，若 ~2000ms 见坑 20），
    再看 TTS 合成速率（<1 表示音频还没生成出来，口型必然追不上）。
22. **`.bat` 必须纯 ASCII + CRLF（2026-09-14 踩）**：cmd.exe 按 **OEM 代码页**（中文 Windows = GBK）解析 .bat，
    UTF-8 的中文/emoji 会变乱码，乱码字节还可能被当成命令分隔符导致脚本行为异常。
    `start-lip-tunnel.bat` / `start-fake-lip.bat` 曾因此不可用（`start.bat`/`stop.bat` 一直是纯 ASCII 所以没事）。
    自检：`LC_ALL=C grep -c $'[\x80-\xff]' xxx.bat` 必须为 **0**。
    **另外禁用 `timeout /t N`**：在 git-bash/MSYS 环境里该名字被 GNU coreutils 的 `timeout` 抢走，
    报 `timeout: invalid time interval '/t'` 并中断脚本——改用 `curl --retry --retry-delay --retry-connrefused` 等待。
23. **Python 脚本打印 emoji 在 GBK 控制台会崩（2026-09-14 踩，已复现）**：`print("⚠️")` 抛
    `UnicodeEncodeError: 'gbk' codec can't encode character '\u26a0'`，**服务/脚本当场退出**。
    双保险：① 脚本顶部 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`；
    ② .bat 里先 `chcp 65001 >nul` 再调 Python。已在 `fake_lip_server.py` / `verify_e2e_lip.py` / `verify_lip_sync.py` 落地。

### 3.10 口型推理服务（2026-09-14 落地）

| 项 | 值 |
|---|---|
| 服务代码 | `backend/deploy/lip_service.py`（部署到 `/root/autodl-tmp/digital-human/MuseTalk/lip_service.py`） |
| 本机客户端 | `backend/src/lip/musetalk_engine.py`（HTTP 客户端，`config.yaml → lip.service_url`） |
| 启动（云侧） | `cd /root/autodl-tmp/digital-human/MuseTalk && OMP_NUM_THREADS=16 nohup /root/miniconda3/bin/python -m uvicorn lip_service:app --host 0.0.0.0 --port 8002 &` |
| 启动预热 | 模型加载 9.4s + avatar 缓存 11.2s（**进程启动即加载**，首个请求不再等） |
| 本机访问 | SSH 隧道：`ssh -N -L 8002:127.0.0.1:8002 -p 47618 root@connect.nmb1.seetacloud.com`（公钥已加入 `authorized_keys`） |
| 验证 | `curl http://127.0.0.1:8002/health`；端到端 `backend/eval/verify_e2e_lip.py` |
| 依赖 | 云侧需 `fastapi` + `uvicorn`（已装 0.124.4 / 0.33.0） |
| avatar 缓存 | `results/avatars/avator_1/`（coords.pkl / latents.pt / full_imgs / mask，536 帧）——**换镜像不丢**（数据盘） |

### 3.11 跨机通道带宽（2026-09-14 实测）

| 路径 | 实测 | 说明 |
|---|---|---|
| SSH 隧道（本机↔云） | **0.96 MB/s 下行 / 0.46 MB/s 上行** | 20 MB 文件实测；**这是逐帧 JPEG 方案的硬约束** |
| 实例原生公网 | 6.6 MB/s（Cloudflare）/ 18 MB/s（阿里云镜像） | 带宽本身充足，瓶颈在 SSH 通道 |

> 影响：一句 7s 回答的逐帧 JPEG 约 14 MB → 传输 15s。**决策见 VERIFY 报告 §5。**

### 3.12 云实例关机后的恢复步骤（约 2 分钟）

**关机不丢任何东西**（与"换镜像"不同）：数据盘 `autodl-tmp`（8.7G 权重 + avatar 缓存 + `lip_service.py`）和系统盘（miniconda 里的 fastapi/uvicorn）都保留。

1. **AutoDL 控制台开机**（选有卡模式）
2. **启动口型服务**（约 21s 完成模型+avatar 加载）：
   ```bash
   ssh -p 47618 root@connect.nmb1.seetacloud.com \
     "cd /root/autodl-tmp/digital-human/MuseTalk && OMP_NUM_THREADS=16 \
      nohup /root/miniconda3/bin/python -m uvicorn lip_service:app --host 0.0.0.0 --port 8002 > /root/lip_server.log 2>&1 &"
   ```
3. **本机建隧道**：双击 `start-lip-tunnel.bat`（看到 `status:ok` 即成功）

验证：`curl http://127.0.0.1:8002/health` → `{"status":"ok","device":"cuda:0","frames_cached":536,...}`

⚠️ 若期间**换过镜像**，系统盘被重置 → 需重装 `fastapi uvicorn` 并重传 `lip_service.py`（见 §3.10）。
⚠️ **关机时的 SSH 症状**（2026-09-14 实测）：端口 47618 **TCP 可连**（1~44ms）但立刻 `kex_exchange_identification: read: Connection reset by peer`——AutoDL 网关先接受 TCP 再重置。这是"实例已关机"的正常表现，**不是网络故障**，别误判成隧道/网络问题。

### 3.13 口型传输载体（ADR-005，2026-09-14 落地）

| 项 | 值 |
|---|---|
| 默认载体 | `h264`：整句 H.264(MP4) 片段 base64（请求/配置字段 `transport`） |
| 兼容路径 | `frames`：逐帧 JPEG（P1 原样保留，**A/B 对照必须可复现**） |
| 切换点 | `config.yaml → lip.transport` 或请求体 `transport` 字段 |
| 编码器 | `backend/deploy/lip_encode.py`（**不依赖 torch/cv2**，故本机可单测） |
| 服务启动 | 同 §3.10，无需改（多装了 ffmpeg 依赖；云机 ffmpeg 4.2.7 已预装） |
| 前端 | `lip_video` 事件 → `useLipVideo` hook 按**音频时钟**（`(audioCtx.currentTime - sink.base)*1000`）排队播放 |
| 实测载荷 | 387 KB / 8s 音频（逐帧 JPEG 15.52 MB → **41×**） |

**无 GPU 时的协议联调**（云实例关机也能验整条链）：
```bash
# 1) 用一段真实产物起假口型服务（只回放，不推理）
python backend/eval/fake_lip_server.py --port 8002
# 2) 正常起后端（config 指向 localhost:8002，无需改配置）
# 3) 跑端到端：断言 lip_video 是合法 H.264、n_frames 与 MP4 实际帧数自洽、start_ms 单调
python backend/eval/verify_e2e_lip.py "运费怎么算"
```
> ⚠️ 假服务**不产生口型，性能/延迟数字一律无效**，只验协议形状；精确载荷只认云 GPU 实测。

### 3.15 前端 VAD 三处坑（2026-09-15 修，别再踩）

依据离线标定：`backend/eval/verify_asr_endpoint.py` + 报告 `backend/eval/reports/asr_mic_check.md`。

24. **worklet 的帧 RMS 必须按「整帧」算**：把 `let sum = 0` 放进 `process()` 内会被每次 128 样本的调用重置，
   而 `1600 ÷ 128 = 12.5` 不是整数 → 实际只累加了「跨帧那次调用中的前 ~65 个样本」，分母却写死 128
   （等价于 **~4ms 窗口**：RMS 系统性低估 19%，**50ms 级瞬态完全漏检**）。
   **正确写法**：`this._sumSq` 随样本累加、发帧时清零，`rms = Math.sqrt(this._sumSq / FRAME)`。
   **回归闸门**：跑 `verify_asr_endpoint.py`，它打印的「worklet/理论 RMS 一致性」应恒为 ~1.000（非 1 即口径又漂了）。
25. **静音阈值要自适应，不能写死**：固定 `0.012` 在底噪 ≥0.012 的环境（风扇/空调/临街/多人）会让端点
   **永不触发** —— 用户说完话数字人不响应，且**静默失效、不报错**（最难查的一类）。现用只降不升的
   min 跟踪器：阈值 = `max(底噪估计×3, 0.004)`。改这段前先跑标定脚本。
26. **播报期间必须另开一路「只监听、不上行」的采集**：对话采集在静音端点后已释放音轨，
   不重开就永远检测不到插话（FR-06 会退化成"只有手动按钮"）；但**不能上行音频**，
   否则数字人自己的声音会被当成用户说话送进 ASR。打断判据 `max(底噪×8, 0.03)` + **连续 2 帧（200ms）去抖**
   ——RMS 修好后瞬态会命中瞬时判据，单帧触发就会误打断。
28. **改完后端要确认"进程真的重启了"**（2026-09-15 踩）：`Stop-Process` 杀的是 `netstat` 查到的
   LISTENING PID，一旦没杀干净，**旧进程仍占着 8010** → 新进程静默启动失败 → 你以为测的是新代码，
   实际请求全打进了旧进程（症状极像"代码改了但行为没变"，会把人带进错误方向）。
   本机实测：一次打断端到端验证 FAIL（打断后仍收到 173 条 tts_audio），排查下来根因就是旧进程没死；
   同日重起后同脚本 **PASS**（打断后 tts_audio 0 条、流 5ms 结束）。
   **判定方法（唯一可靠）**：让后端日志落文件，看这次请求有没有被记录下来：
   ```bash
   cd backend && PYTHONPATH=src .venv/Scripts/python.exe -m uvicorn api.routes:app \
     --host 127.0.0.1 --port 8010 > "$LOCALAPPDATA/Temp/dh_backend.log" 2>&1 &
   grep "chat/stream" "$LOCALAPPDATA/Temp/dh_backend.log"   # 应能看到你自己刚发的请求
   ```
   确认监听者：`netstat -ano | grep ":8010" | grep -i listening`。

27. **听不见自己的声音 ≠ 没有回声风险**：判据只认「有声音」，离线实测外放音频命中判据 37 帧、触发 17 次。
   能否自打断**完全取决于浏览器 AEC**（`getUserMedia({echoCancellation:true})`），必须真机实测
   （报告 §5 第 4 项），离线试验给不出结论。

### 3.14 口型分片送检（ADR-006，2026-09-14 落地）

| 项 | 值 |
|---|---|
| 分片粒度 | `config.yaml → lip.chunk_ms`（默认 1000ms；16k 单声道 PCM16 = 32 字节/ms） |
| 尾片阈值 | `lip.min_tail_ms`（默认 200ms；短于此不单独推理） |
| 语义 | 音频一到就按片送推理，**不等整句合成完**（SPEC §2.1 v1.3 已据此修订） |
| 服务 URL | **必须 `127.0.0.1`**，禁止 `localhost`（踩坑 20） |
| 诊断日志 | 设 `LIP_DEBUG=1` 启动后端，会打印每片的派发/返回时刻与 HTTP 耗时 |
| 效果 | 首片段偏差 +5274ms → **+205ms**；后续片段**全部提前到达**（负值=排队等播，即同步） |
| 待验 | 真实云 GPU 上单片生成耗时必须 < 1000ms，否则队列积压（**开机后头号补测项**） |
| 背压 | 2026-09-17 已加：`lip_in_q` 限长（`config.yaml lip.queue_max`，默认 8）+ **满则丢最旧**；`done.lip_dropped` 暴露丢帧数。理由：TTS 合成快于单 GPU 串行推理，无界队列会让片段落后音频时钟 → **丢帧好过整体延迟**。实测（lip 服务未开、必然积压）13 片派发 / **丢 4 片**，逻辑生效 |

### 3.16 配置中心纪律（2026-09-17 收口）

`config.yaml` 的 `asr` 段曾 **6 个字段代码一个都不读**（`asr/streaming.py` 全硬编码），
且 `chunk_size` 值与实现不符（config `[5,10,5]` vs 代码 `[0,10,5]`）——写配置的人以为改了，
实际跑的永远是硬编码值。**这是最隐蔽的一类坑：改动看起来生效、验证时却"没变化"。**

已收口：`model` / `chunk_size` 接回代码（实测：把 `chunk_size` 改成 `[0,20,5]`，
`CHUNK_STRIDE` 立刻 9600 → 19200 样本、600 → 1200ms）；删除 `provider` / `vad_model` /
`streaming` / `chunk_stride` 四个名义项。

**三条纪律**：
1. 改配置前先 `grep -rn "<字段名>" backend/src/`，**确认它真的被读**；
2. 加配置项**同批改代码**，否则就是文档债务；
3. `chunk_size` 改动会改分片粒度，而**前端 `useMicCapture` 按同一粒度（9600 样本）硬编码切片**——
   两边必须一致（SPEC §5.4），改后需重跑 V-03 标定。

### 3.17 前端麦克风采集的两个致命缺陷（2026-09-17 修，别再踩）

**① "ASR 识别只能用一次" —— 异步 start 撞上只读守卫（`useMicCapture`）**

症状：第一次语音识别正常，之后点麦克风**毫无反应**（连错误都不报）。

根因链：

1. `startMic('barge')`（播报期间开打断监听）是**异步**的（`getUserMedia` + `addModule` 都要等）
2. 播报很快结束时 effect 重跑，此刻 `monitoring` 还是 `false`（`setMonitoring(true)` 尚未执行到）
   → `else if (monitoring)` 不成立 → **没人释放这次监听**
3. 随后异步 start 落地，`modeRef.current = 'barge'`
4. 而 `start()` 开头是 `if (!sessionId || modeRef.current !== null) return` → **静默返回**
   → 此后每次点麦克风都被这道守卫挡掉，**永久失灵**

修法三件套：
- `release()` 递增 `epochRef`；`start()` 完成后核对 epoch，不一致就关掉刚拿到的资源（消灭"幽灵采集"）
- `start()` 遇到**不同**模式改为**抢占**（`await release()` 后继续），不再静默 return
- ConsolePage 的 effect 里 `startMic('barge').then(...)` 完成后**再核对一次期望值**，不需要则释放

> **教训**：只读守卫（`modeRef !== null`）+ 异步获取资源 = 必然出现"幽灵状态"。
> 凡是"先检查、再异步"的地方，都要用代际/版本号让在途操作可作废。
> 这类缺陷**离线脚本测不出来**（后端连跑 3 轮全部正常），只有真机点几下才暴露。

**② "初次说话毫无反应" —— ASR 模型冷启动 22s（`routes.py` lifespan）**

症状：刚用 `start.bat` 启动后第一次说话长时间没反应，试几次后才正常。

根因：`get_model()` 冷启动阻塞 22s，而前端每 600ms 一片且 `await postChunk` 发送
→ 冷启动期间所有分片都堵在模型加载上；而 `ASR_TIMEOUT` 只统计 `_generate` 内部（**不含加载**），
所以**连超时都不报**，就是纯粹地卡住。

修法：`routes.py` 加 `lifespan`，启动即**后台线程**预热（不阻塞 uvicorn 启动；预热失败不影响服务）。

实测：预热 **23.5s** 完成；**预热后首片 227ms**（未预热时 ~22000ms）。

> 这条是**坑 8 的根治**：以前靠人手工跑 `prewarm_asr.py`，现在服务自己预热，用户无感。

## 4. 模型与下载源

- 模型权重国内优先 **ModelScope**（DESIGN §5.4 坑 2）：FunASR（paraformer-zh-streaming + fsmn-vad）、CosyVoice2、MuseTalk 权重
- 口型推理实验数据（FPS/显存）必须**实验即时落档**到 `backend/eval/reports/`，否则云实例释放后丢失（ADR-001 后果）

## 5. 变更记录

| 日期 | 变更 |
|---|---|
| 2026-09-01 | 建档：本机基线 + backend venv + 端口约定 + 云 GPU 占位 |
| 2026-09-02 | AutoDL 实例就绪：镜像/SSH/目录/8.7G 权重 + 5 条部署踩坑 |
| 2026-09-02 | **换镜像 PyTorch 2.0.0/py3.8/cu118 破解 mmcv 死结**（坑 9 已解决，真因+适配记录见 §3.9）；requirements 全量装完 import 验证过；ffmpeg 4.2.7 装好；OMP 持久化到 .bashrc |
| 2026-09-02 | **V-01 通过**：4090 上 v1.0 normal 推理 exit=0（1500 帧全长出片带音轨），显存 4828MiB / UNet 6.06 it/s；s3fd 权重上传坑（坑 10）；数据见 eval/reports/musetalk_validation.json |
| 2026-09-13 | **V-01 realtime 补测**：v1.0 realtime exit=0，稳态 19.07 fps / 52.4ms 每帧 / RTF 0.763（25fps 缺口 31%）；原始日志落档 eval/reports/raw_logs/ |
| 2026-09-14 | **接真实 LLM + TTS**：DeepSeek 流式（brain/）+ CosyVoice v2 流式（tts/），前端可听到语音；新增 V-06 端到端延迟脚本与台账行 |
| 2026-09-14 | **流水线重叠 + WS 连接复用**：overlap 实测 83%，A/B 归因净收益 45.8ms；连接复用使 TTS 首包 418~513ms→343~402ms（踩坑见 §1） |
| 2026-09-14 | **接入语音输入（ASR）**：`asr/streaming.py` + `POST /api/v1/asr/chunk` + 前端 AudioWorklet 采集与前端 VAD（ADR-004）；真实测试集 7 条识别全对；打断（FR-06）随之可用（踩坑 8~11） |
| 2026-09-14 | **口型服务化落地（云 GPU）**：`deploy/lip_service.py`（常驻模型+avatar、去逐帧落盘、JPEG 直出）+ 本机 `lip/musetalk_engine.py` 客户端 + SSE `lip_frame` 事件流 + SSH 隧道；端到端 184 帧验证通过。**关键实测：GPU 生成 92fps（V-01 的 19.07 是 I/O 瓶颈）、隧道仅 0.96MB/s、H.264 比逐帧 JPEG 小 41×**（踩坑 14~19，报告 eval/reports/lip_service_check.md） |
| 2026-09-17 | **会话持久化 + 契约/并发硬化**：新增 `GET /api/v1/session/{id}` 存活探测；前端 localStorage 恢复会话与历史（后端重启则保留历史并提示上下文已重置）；口型队列背压（`lip.queue_max`）+ `done.lip_dropped`（SPEC v1.8）；`POST /asr/chunk` 补 HTTP 契约 + `seq` 语义（SPEC v1.9） |
| 2026-09-17 | **修两个真机才暴露的缺陷（§3.17）**：① `useMicCapture` 异步 start 撞只读守卫 → "识别只能用一次"（epoch + 抢占 + 完成后核对）；② ASR 冷启动 22s → "初次说话无反应"（lifespan 后台预热，实测首片 227ms） |
| 2026-09-17 | **配置中心收口（§3.16）**：`asr` 段 6 字段代码从不读取且 `chunk_size` 值与实现不符 → `model`/`chunk_size` 接回代码、删除 4 个名义项；顺带修正 DESIGN 目录图（`vad/` 已删） |
