---
title: 实时交互链路接口与协议规范
type: spec
status: completed
date: 2026-09-01
updated: 2026-09-15
links: [./DESIGN-实时交互链路架构.md, ../03-决策/ADR-004-VAD归属.md]
---
# 《接口与协议规范》完整版

> 文件名：`SPEC-接口与协议规范.md`
> 类型：spec
> 状态：已完成（v1.0 已定稿）
> 日期：2026-09-01
> 关联：DESIGN-实时交互链路架构.md §4
> 读者：前后端开发、联调测试
> 结论：定义 SSE 事件流、WebRTC 信令、模块间数据契约及错误码，作为 P2 全链路联调的接口基线

------

## 1. 文档目的

本规范定义实时交互数字人助手中所有对外（前端←→后端）及对内（模块间）的接口协议。**P2 启动前定稿**，后续变更需走 ADR 流程（编号递增、状态标记）。

**核心原则**：

1. **唯一时钟源**：全链路以 TTS 输出的音频时间戳（`start_ms`/`end_ms`）为时钟基准，`lip`/`render` 不独立维护时间轴。
2. **流式媒体事件**（`tts_audio`、`lip_frame`）携带 `seq` 字段，前端据此检测丢包；状态类事件（`thinking`、`interrupted`、`done`、`error`）不携带 `seq`。
3. **向后兼容**：字段增删需保留 `session_id`，新字段通过 `payload` 扩展。

------

## 2. 模块间数据契约（`src/schemas.py`）

以下为各模块间传递的 Python dataclass 定义，**P1 启动即可落成代码**。

```python
# backend/src/schemas.py
from dataclasses import dataclass, field
from typing import Optional, List, Tuple
from enum import Enum
from datetime import datetime


# ============ ASR → Brain ============
@dataclass
class AsrChunk:
    """语音识别增量输出"""
    text: str                    # 增量识别文本
    is_final: bool = False       # 该分片是否为句尾（端点检测）
    start_ms: Optional[int] = None  # 本分片在用户语音流中的起始时间 = 本次增量第一个分块的起点
    end_ms: Optional[int] = None    # 本分片在用户语音流中的结束时间 = 本次增量最后一个分块的终点
                                    # v1.5 已修：一次 push 送多块时窗口必须覆盖**全部**块；
                                    #   修正前只标「最后一块」（(chunks-1)*600 → chunks*600）
    confidence: float = 0.0      # 置信度（0-1），用于调试；P1 实现恒返回 0.0（未启用）


# ============ Brain → TTS ============
@dataclass
class BrainOutput:
    """对话大脑流式输出"""
    token: str                   # 增量文本 token（流式）
    is_final: bool = False       # 是否为完整回答的最后一个 token


# ============ TTS → Lip / 前端 ============
@dataclass
class TtsSegment:
    """语音合成分片输出"""
    audio: bytes                 # PCM 单声道 音频数据（采样率见 sample_rate）
    start_ms: int                # 本分片在整句音频中的起始时间（毫秒）
    end_ms: int                  # 本分片在整句音频中的结束时间（毫秒）
    words: List[Tuple[str, int, int]] = field(default_factory=list)
    # 音画同步核心字段: [(词, 起始ms, 结束ms)]
    # 例: [("你好", 0, 300), ("世界", 300, 600)]
    sample_rate: int = 16000     # v1.1 新增。链路统一基准 16kHz，TTS 输出非 16kHz（如 CosyVoice2 24kHz）时由 tts 模块重采样后再出 TtsSegment

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


# ============ Lip → Render ============
@dataclass
class LipFrame:
    """口型驱动视频帧输出"""
    frame: bytes                 # JPEG 编码（P1）/ H.264 编码（P2 后）
    pts_ms: int                  # 展示时间戳（毫秒），基于 TTS 音频时钟
    frame_idx: int               # 帧序号，用于前端渲染调度


# ============ 会话状态 ============
class SessionState(Enum):
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"
    ERROR = "error"


@dataclass
class SessionContext:
    """会话上下文（内存中维护）"""
    session_id: str
    state: SessionState
    created_at: datetime
    last_active: datetime
    history: List[dict] = field(default_factory=list)  # 对话历史 [{role, content}]
    buffer_audio: List[TtsSegment] = field(default_factory=list)  # 待播放 TTS 队列
    buffer_lip: List[LipFrame] = field(default_factory=list)      # 待渲染口型帧队列
    # ⚠️ P1 未落地（v1.8 标注）：实际队列是 chat_stream 内的局部变量
    #    （token_q / sentence_q / audio_q / lip_in_q），这两个会话级缓冲字段目前无人使用。
    #    保留原因：P2 若做「打断后继续播」或服务端侧缓冲会用；若确定不做，应从契约删除
    #    （按 §8 变更规则，删除字段需提 ADR）。


# ============ 错误响应 ============
@dataclass
class ApiError:
    """统一错误响应格式"""
    code: str                    # 错误码，见 §6
    message: str                 # 人类可读错误描述
    session_id: Optional[str] = None
    retry_after: Optional[int] = None  # 建议重试等待秒数
```

------

## 2.1 模块间服务接口：Lip 推理服务（P1 跨机协作）

**背景**：P1 环境分工（ADR-001）下，TTS/ASR/编排跑本机，MuseTalk 跑云 GPU。lip 模块封装为 HTTP 推理服务，本机通过 API 调用；~~P1 语义为**一句音频一次请求，返回整段帧序列**，P2 再演进为流式帧。~~ **（v1.3 修订，依 ADR-006）语义为「按音频分片请求」：调用方每 ~1s 音频调用一次，返回该分片的整段素材；整句一次请求会导致口型落后一整句合成时间（实测首片 +5274ms → 分片后 +205ms）**

**端点**：

```
POST /api/v1/lip/infer
```

**请求**：

```json
{
  "audio_pcm_16k_b64": "...",          // 16kHz 单声道 PCM 的 base64（调用方保证已重采样）
  "avatar_frames_b64": ["...", "..."], // 形象帧（JPEG base64），P1 单张参考帧即可，多帧预留
  "fps": 25,                            // 输出帧率，与 MuseTalk 训练口径一致
  "session_id": "xxx"                   // 可选，显存/日志上下文
}
```

**响应**（v1.2 起：`transport=h264` 为默认，见 ADR-005）：

```json
{
  "video_b64": "...",                   // H.264(MP4) 整句片段 base64（ADR-005 默认载体）
  "duration_ms": 7320,                  // 片段时长 = 帧数/fps*1000
  "n_frames": 183,
  "fps": 25,
  "gpu_memory_mb": 8500,                // 推理显存记录（评估台账数据源）
  "infer_fps": 92.2                     // 推理帧率（评估台账数据源）
}
```

**响应**（`transport=frames` 兼容路径，P1 原样保留用于 A/B 对照）：

```json
{
  "frames": [{"frame_b64": "...", "pts_ms": 0, "frame_idx": 0}],
  "gpu_memory_mb": 8500,                // 推理显存记录（评估台账数据源）
  "infer_fps": 28.5                     // 推理帧率（评估台账数据源）
}
```

- `transport` 由请求字段或 `config.yaml → lip.transport` 决定；默认 `h264`
- `frames` 的 `pts_ms` 以请求音频的起始时间 0 为基准，与 `TtsSegment` 时间戳同源（音频时钟），供前端/评估按 SPEC §3.5 调度
- `video_b64` 的播放起始时刻同样以请求音频起点为基准：片段第 0 帧对应音频时间 0
- 两种载体的**渲染输出均为形象原分辨率**（SPEC §4.3），H.264 只是传输编码
- 失败返回 SPEC §6 错误码（`LIP_TIMEOUT` / `LIP_OOM` / `LIP_ERROR` 可扩展）；P1 无 GPU 时允许 mock 实现（返回静态帧）供本机链路联调

------

## 3. SSE 事件流规范（后端 → 前端）

### 3.1 端点

```
GET /api/v1/chat/stream?session_id={session_id}&user_id={user_id}
```

- 前端建立 SSE 连接后，即可发送音频数据（通过 WebRTC 数据通道或独立的 audio 端点，见 §4）
- 每个 `session_id` 同时只允许一个 SSE 连接

### 3.2 事件格式

符合标准 SSE 格式（`text/event-stream`）：

```
event: {event_type}
data: {JSON}
```

### 3.3 事件类型定义

| event         | 触发时机                   | payload 字段                                                 | 说明                                                |
| :------------ | :------------------------- | :----------------------------------------------------------- | :-------------------------------------------------- |
| `thinking`    | 进入对话大脑阶段           | `{"state": "thinking", "timestamp_ms": 12345}`               | 状态标记，用于前端展示"思考中"                      |
| `interrupted` | 后端收到打断信号，开始清理 | `{"state":"interrupted", "timestamp_ms": 12345}`             | 前端收到后停止接收新 TTS/口型分片，开始清理播放队列 |
| `tts_audio`   | TTS 输出每个音频分片       | `{"seq": 0, "audio_b64": "...", "start_ms": 0, "end_ms": 320, "words": [["你好",0,300]]}` | audio_b64 为 PCM 数据的 base64 编码                 |
| `lip_frame`   | 口型驱动输出视频帧（`transport=frames` 兼容路径） | `{"seq": 0, "frame_b64": "...", "pts_ms": 0, "frame_idx": 0}` | frame_b64 为 JPEG 的 base64；P1 原路径，仅 A/B 对照时启用 |
| `lip_video`   | 口型驱动输出**整句 H.264 片段**（v1.2 默认，见 ADR-005） | `{"seq": 0, "video_b64": "...", "start_ms": 0, "duration_ms": 320, "n_frames": 8, "fps": 25, "sentence_offset_ms": 0}` | video_b64 为 MP4(H.264) 的 base64；一个片段对应一句回答音频 |
| `done`        | 一次回答完整结束           | `{"total_seq_audio": 10, "total_seq_lip": 45, "duration_ms": 3200, "placeholder": false, "lip_dropped": 0}` | 前端收到后关闭本次播放循环。**`placeholder: true` = 无 `text` 的空跑流**（如 SSE 断线重连），不是一轮真实回答 → 前端必须忽略（v1.7）。**`lip_dropped` = 口型队列背压丢掉的片段数，>0 说明口型跟不上（v1.8）** |
| ⚠️ 被打断的一轮 | **不发 `done`** | — | `interrupt` 置位 `stop_requested` 后生成器立即停止产出并直接结束；`INTERRUPTED → LISTENING` 只由 `/interrupt_done` 驱动（v1.7） |
| `error`       | 任何环节出错               | `{"code": "TTS_TIMEOUT", "message": "...", "retry_after": 3}` | 见 §6 错误码                                        |

### 3.4 示例流

```
event: thinking
data: {"state":"thinking","timestamp_ms":12345}

event: tts_audio
data: {"seq":0,"audio_b64":"UklGR...","start_ms":0,"end_ms":320,"words":[["你好",0,300]]}

event: lip_frame
data: {"seq":0,"frame_b64":"/9j/4AAQ...","pts_ms":0,"frame_idx":0}

event: tts_audio
data: {"seq":1,"audio_b64":"UklGR...","start_ms":320,"end_ms":640,"words":[["世界",320,600]]}

event: lip_frame
data: {"seq":1,"frame_b64":"/9j/4AAQ...","pts_ms":320,"frame_idx":15}

event: done
data: {"total_seq_audio":2,"total_seq_lip":45,"duration_ms":3200}
```

### 3.5 前端处理约定

1. **tts_audio 首包即播**：不等 `done`，收到即开始播放音频（PCM 需转 WebRTC 可消费格式）。
2. **lip_frame 按 `pts_ms` 调度**：以音频时钟为基准，`pts_ms` 对应的音频时间到达时才渲染该帧。**帧丢弃策略**：若某帧到达时其 `pts_ms` 已落后当前音频时钟 >50ms，则丢弃（防止音画追赶时画面跳变）。（仅 `transport=frames` 路径）
2b. **lip_video 按 `start_ms` 排队播放**（v1.2 默认，见 ADR-005）：片段第 0 帧对应音频时钟的 `start_ms`；前端维护按 `start_ms` 升序的片段队列，到点播放。**片段迟到策略**：若片段到达时其 `start_ms` 已落后音频时钟（即该句已开始播报），允许立即从头播放并接受音画偏差，或丢弃该片段（由前端策略决定，实测偏差需记录进评估台账）。
3. **seq 检测**：前端维护 `last_seq_audio` 和 `last_seq_lip`，若收到非连续 seq → 上报 `WARN` 日志，不影响播放。

------

## 4. WebRTC 信令与媒体传输

### 4.1 架构选择

| 方向     | P1（最小闭环）            | P2+（全链路）                    |
| :------- | :------------------------ | :------------------------------- |
| 音频上行 | 不涉及（文本输入）        | WebRTC DataChannel（二进制 PCM） |
| 音频下行 | 不涉及（本地播放测试）    | WebRTC RTP 音频流（Opus）        |
| 视频下行 | 不涉及（Gradio 本地显示） | WebRTC RTP 视频流（VP8/H.264）   |

**P2 采用完整 WebRTC SDP 交换**，不采用 MSE 简化方案（为后续低延迟实时交互保留一致性）。

### 4.2 信令端点

```
POST /api/v1/webrtc/offer
请求: { "session_id": "xxx", "sdp": "v=0..." }
响应: { "sdp": "v=0...", "type": "answer" }

POST /api/v1/webrtc/ice
请求: { "session_id": "xxx", "candidate": "..." }
响应: { "status": "ok" }
```

### 4.3 SDP 约束

- 音频：Opus，**采样率 16kHz**，单声道，码率自适应（32-64 kbps）
- 视频：VP8（优先）或 H.264，**帧率 25fps**（与 MuseTalk 训练口径一致）。输出分辨率 = **输入形象视频的分辨率**（MuseTalk 仅人脸区域固定 256×256，非输出尺寸；V-01 实测后回填实际值）

### 4.4 音频上行（用户语音输入）

P2 阶段采用 **WebRTC DataChannel** 传输原始 PCM 分片（而非 RTP），原因：

- 便于 ASR 端接收分片并控制分片边界
- DataChannel 延迟可控，且与 SSE 下行解耦

**二进制包格式**：

```
// 包结构（二进制 arraybuffer）
// [0-7]   timestamp_ms (BigInt64)   — 采集时间戳
// [8-11]  data_length (Uint32)      — PCM 数据字节数
// [12-15] seq (Uint32)              — 分片序号
// [16:]   PCM 16kHz 单声道 原始数据
```

```javascript
// 前端发送示例
const header = new ArrayBuffer(16);
new BigInt64Array(header, 0)[0] = BigInt(Date.now());
new Uint32Array(header, 8)[0] = pcmData.byteLength;
new Uint32Array(header, 12)[0] = seq++;
const packet = concat(header, pcmData);
dc.send(packet);
```

------

## 5. 会话控制接口

### 5.1 创建会话

```
POST /api/v1/session
请求: { "user_id": "optional" }
响应: { "session_id": "xxx", "created_at": "2026-09-01T..." }
```

### 5.1.1 会话存活探测（v1.8）

```
GET /api/v1/session/{session_id}
响应: { "session_id": "xxx", "state": "listening", "created_at": "...", "history_len": 0 }
不存在 → 404 SESSION_NOT_FOUND
```

用途：前端从本地存储恢复会话前探测是否仍存活。会话是**进程内内存态**（P1），后端重启即全体失效——
前端据此区分「复用同一会话（后端 `ctx.history` 还在，**多轮上下文连续**）」与「新建会话但保留本地历史」。
只读端点，不改状态、不刷新 `last_active`。

### 5.2 主动打断

```
POST /api/v1/session/{session_id}/interrupt
响应: { "status": "interrupted" }
      { "status": "ignored", "state": "<当前状态>" }   ← 未生效
```

等价于 VAD 自动检测触发的打断，后端行为一致。

**两种返回的含义（v1.4 补全）**：
- `interrupted`：转移生效，本次打断成功（仅 `SPEAKING` 态可能返回）
- `ignored`：**信号未生效**，携带当前状态。两种情形：① 转移表未定义该转移（`LISTENING`/`THINKING` 态收到打断）；
  ② 转移表定义但 `allow=False`（`INTERRUPTED` 清理期重复收到打断，见 DESIGN-打断 §4.1 ❌ 行）
- 前端必须按 `status` 判断本次打断是否生效，**不能只看 HTTP 200**（清理期重复信号返回 200 但未生效）

### 5.2.1 打断清理确认

```
POST /api/v1/session/{session_id}/interrupt_done
请求: { "session_id": "xxx" }
响应: { "status": "cleaned", "session_id": "xxx" }
```

**说明**：前端播放器完成当前分片播放并清空队列后，调用此端点通知后端。后端收到后完成 `INTERRUPTED → LISTENING` 状态转移。

### 5.3 关闭会话

```
DELETE /api/v1/session/{session_id}
响应: { "status": "closed" }
```

释放所有缓冲区及 GPU 推理上下文（若有）。

------

## 6. 错误码表

| 错误码              | HTTP状态 | 说明                         | 前端处理                             |
| :------------------ | :------- | :--------------------------- | :----------------------------------- |
| `SESSION_NOT_FOUND` | 404      | session_id 不存在或已过期    | 重新创建会话                         |
| `SESSION_CONFLICT`  | 409      | 同一 session 已有活跃连接    | 等待或强制复用                       |
| `ASR_TIMEOUT`       | 503      | 语音识别超时（**单块 >3s**）  | 提示用户重试（v1.5 已实现：`asr/streaming.py ASR_TIMEOUT_S`；事后判定，不中断推理） |
| `ASR_ERROR`         | 503      | ASR 模型推理异常             | 兜底提示（v1.4 由 500 更正为 503，与实现一致）       |
| `LLM_TIMEOUT`       | 503      | 大模型首 token >1.5s         | 静默重试一次，仍失败则降级话术       |
| `LLM_ERROR`         | 500      | 大模型 API 返回异常          | 返回预设话术 + 记录日志              |
| `TTS_TIMEOUT`       | 503      | 语音合成首包 >1s             | 降级为备用 TTS 或提示                |
| `TTS_NO_TIMESTAMP`  | 500      | TTS 未返回 word-level 时间戳 | 触发 Plan B（FunASR 字级时间戳对齐） |
| `LIP_TIMEOUT`       | 503      | 口型推理首帧 >500ms          | 静默丢帧，继续播放音频（无声画面）   |
| `LIP_OOM`           | 503      | GPU 显存不足                 | 降级批处理大小或拒绝新会话           |
| `WEBRTC_SDP_ERROR`  | 400      | SDP 交换失败                 | 提示刷新页面                         |

------

## 7. 时序约束（设计基线）

| 环节                      | 超时阈值                       | 超时后行为             |
| :------------------------ | :----------------------------- | :--------------------- |
| ASR 连续静音（前端 VAD，ADR-004） | 时长 **1.2s**；**静音阈值自适应**：`max(环境底噪×3, 0.004)`，区间 [0.004, 0.036]（v1.6） | 触发 VAD 端点（is_final=True）→ 进入 thinking |
| LLM 首 token              | 1.5s                           | 重试一次，失败降级     |
| TTS 首包                  | 1s                             | 降级备用 TTS           |
| Lip 首帧                  | 500ms                          | 丢帧，继续播音频       |
| 端到端（用户说完 → 首包） | 2s | 超时打点记录，不做阻塞 |

------

## 8. 版本与变更管理

| 版本     | 日期           | 变更内容                                                     | 作者 |
| :------- | :------------- | :----------------------------------------------------------- | :--- |
| v0.1     | 2026-09-01     | 初稿，基于 DESIGN §4 扩充                                    | -    |
| **v1.0** | **2026-09-01** | **定稿：补 interrupt_done 端点、interrupted 事件、seq 原则修正、错误码统一、DataChannel 二进制传输** | -    |
| **v1.1** | **2026-09-01** | **勘误：TtsSegment 增 sample_rate 字段、新增 §2.1 lip 推理服务接口、§4.3 分辨率表述修正、§7 端到端口径统一为"首包"** | -    |
| **v1.2** | **2026-09-14** | **口型传输载体改为 H.264 整句片段：§2.1 响应增 `video_b64`/`duration_ms`/`n_frames`（`frames` 保留为兼容路径）、§3.3 新增 `lip_video` 事件、§3.5 增片段排队播放约定。**依据 ADR-005**（实测跨机链路 0.96MB/s，逐帧 JPEG 16.2s/句 vs H.264 0.39s，41×）** | -    |
| **v1.3** | **2026-09-14** | **口型改为按音频分片请求：§2.1 语义由"一句一次请求"修订为"每 ~1s 音频分片一次请求"。**依据 ADR-006**（整句送检导致首片段迟到 +5274ms；分片后 +205ms，后续片段全部提前到达）** | - |
| **v1.8** | **2026-09-17** | **① §5.1.1 新增会话存活探测端点**（前端刷新恢复会话用）；**② `done` 增 `lip_dropped`**：口型输入队列改限长 + **满则丢最旧**（TTS 合成快于单 GPU 串行口型推理，无界队列会让片段滞后于音频时钟 → 音画同步崩）；**③ §2 标注 `buffer_audio/buffer_lip` P1 未落地**；**④ TTS 连接独占**——DashScope 音频帧是裸二进制、不带 `task_id`，并发使用同一连接会把 A 句音频当成 B 句的，`CosyVoiceTts.stream` 加独占锁（当前串行消费下为防御性） | - |
| **v1.7** | **2026-09-15** | **打断真正生效（FR-06）**：`/interrupt` 置位会话级 `stop_requested`，`/chat/stream` 生成器轮询到即停止产出（不再发 token/TTS/口型）且**不发 `done`**；`done` 新增 `placeholder` 字段标识空跑流。依据：此前打断只改状态机状态，生成器照旧把整轮发完 → 用户感受"无法打断"（实测证据 `verify_interrupt_e2e.py`：修复后 interrupt → 流结束 5ms、后续 tts_audio 0 条） | - |
| **v1.6** | **2026-09-15** | **前端 VAD 三项修复（依据离线标定，证据 `backend/eval/reports/asr_mic_check.md`）**：① §7 静音阈值改为自适应 `max(底噪估计×3, 0.004)`（底噪用只降不升的 min 跟踪器）——固定 0.012 在底噪 ≥0.012 的环境下端点永不触发；② `mic-processor.js` 的帧 RMS 改为**整帧口径**（原实现等价于 ~4ms 窗口，低估 19%、漏检 50ms 瞬态；修正后 worklet/理论一致性 0.993~1.000）；③ 打断判据改为 `max(底噪×8, 0.03)` + **连续 2 帧（200ms）去抖**（RMS 修正后瞬态会命中瞬时判据） | - |
| **v1.5** | **2026-09-15** | **ASR 实现收口：`ASR_TIMEOUT` 落地（单块 >3s；事后判定而非中断推理——FunASR 同步阻塞，强杀线程会破坏模型状态）；`AsrChunk.start_ms/end_ms` 修正为覆盖本次增量全部分块；§6 去掉 ASR_TIMEOUT「未实现」标注** | - |
| **v1.4** | **2026-09-15** | **ASR 口径收口（文档与实现对齐）：§7 静音阈值由「>2s」改为「前端标定、当前 1.2s（ADR-004）」；§6 `ASR_ERROR` 由 500 更正为 503、标注 `ASR_TIMEOUT` 未实现；§2 `AsrChunk` 标注 start_ms/end_ms 语义偏差与 confidence 未启用；§5.2 补全 `interrupt` 响应区分 `interrupted`/`ignored`（清理期重复打断原本误报为 interrupted，已修）** | - |

**变更规则**：

- 新增字段：向后兼容，无需 ADR
- 删除/修改已有字段：需提 ADR，标记状态变更，保留旧版本文件
