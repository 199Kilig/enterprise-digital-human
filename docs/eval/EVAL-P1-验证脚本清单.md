---
title: P1 验证脚本清单
type: eval
status: completed
date: 2026-09-01
updated: 2026-09-01
links: [../02-方案/DESIGN-实时交互链路架构.md, ../03-决策/ADR-001-环境策略.md]
---
# 《P1验证脚本清单》完整版

> 文件名：`EVAL-P1-验证脚本清单.md`
> 类型：eval
> 状态：已完成（v1.0 已定稿）
> 日期：2026-09-01
> 关联：ADR-001-环境策略.md、SPEC-接口与协议规范.md
> 读者：开发实现（P1阶段验证负责人）
> 结论：定义P1阶段必须执行的5个前置验证脚本及其通过标准，确保核心依赖在编码前就绪

------

## 1. 文档目的

按照PRD §4风险清单的要求——"启动后第一天验证流式语音合成时间戳可用性"——本清单定义P1阶段（文本→TTS→口型→视频）**必须先行验证**的5个关键点。

**核心原则**：先验证模型能力，再写编排代码。**不在盲区上盖楼。**

------

## 2. 验证脚本清单总览

| 序号 | 脚本名称                        | 验证目标                                             | 预计耗时 | 优先级 |
| :--- | :------------------------------ | :--------------------------------------------------- | :------- | :----- |
| V-01 | `verify_musetalk_gpu.py`        | MuseTalk在云GPU推理可用性（FPS/显存）                | 30min    | P0     |
| V-02 | `verify_cosyvoice_timestamp.py` | CosyVoice是否输出word-level时间戳（云端/开源双路线） | 20min    | P0     |
| V-03 | `verify_funasr_streaming.py`    | FunASR流式分片能力                                   | 15min    | P0     |
| V-04 | `verify_llm_streaming.py`       | DeepSeek API流式输出（SSE格式）                      | 10min    | P1     |
| V-05 | `verify_sync_measure.py`        | 音画同步测量脚本原型验证（含工具自校准）             | 30min    | P1     |

> **注**：V-06（端到端Mock闭环）为 P1 交付物，非前置验证。V-01~V-05 全部通过后执行，产出第一个 demo 视频。

------

## 3. 各脚本详细定义

### V-01: MuseTalk GPU推理验证

**目标**：确认在目标云GPU实例上，MuseTalk能正常推理且满足帧率要求。

**操作步骤**：

```bash
# 1. 克隆 MuseTalk 仓库
git clone https://github.com/TMElyralab/MuseTalk.git
cd MuseTalk

# 2. 按官方 README.md 安装依赖（Python 3.10, CUDA 11.7+）
# 参考: https://github.com/TMElyralab/MuseTalk?tab=readme-ov-file#installation

# 3. 下载权重（参考官方 README#download-weights）
# 目录结构应包含:
# ./models/musetalk/musetalk.json + pytorch_model.bin
# ./models/dwpose/dw-ll_ucoco_384.pth
# ./models/face-parse-bisent/...

# 4. 实时推理验证（使用官方脚本）
python -m scripts.realtime_inference \
    --inference_config configs/inference/realtime.yaml \
    --batch_size 4
```

**通过标准**：

| 指标       | 目标值         | 记录字段             |
| :--------- | :------------- | :------------------- |
| 推理帧率   | ≥25 fps        | `lip_fps`            |
| 显存占用   | ≤12GB          | `gpu_memory_mb`      |
| 首帧延迟   | ≤500ms         | `lip_first_frame_ms` |
| 无CUDA错误 | 运行30秒无异常 | `status`             |

**输出**：`eval/reports/musetalk_validation.json`

```json
{
  "date": "2026-09-01",
  "gpu_type": "A100",
  "cuda_version": "12.2",
  "lip_fps": 28.5,
  "gpu_memory_mb": 8500,
  "lip_first_frame_ms": 380,
  "status": "PASSED"
}
```

**失败预案**：

- 显存超标 → 降低batch_size（官方支持可调）
- 帧率不足 → 启用preparation预计算（官方realtime模式）
- 无法运行 → 备选 Wav2Lip（显存约 2-4GB）。若启用 Wav2Lip 兜底，需提交 ADR 修订 ADR-003（选型变更须走 ADR 流程）。

### V-02: CosyVoice时间戳验证（双路线）

**目标**：确认目标TTS方案是否支持word-level时间戳输出——这是音画同步的前提。

**操作步骤**：

```python
# verify_cosyvoice_timestamp.py — 双路线验证

# ===== 路线1: 云端 DashScope CosyVoice（支持时间戳）=====
def verify_dashscope():
    from dashscope.audio.tts import SpeechSynthesizer
    response = SpeechSynthesizer.call(
        model="cosyvoice-v1",
        text="你好世界，这是时间戳测试。",
        enable_word_timestamp=True,  # ✅ 云端 API 支持
        format="pcm",
        sample_rate=16000,
    )
    # 检查 response 中是否含 word_timestamp 字段
    # 输出结构预期: 每个字带有 start_ms/end_ms

# ===== 路线2: 开源 CosyVoice（原生不支持时间戳）=====
# 预期结果：不支持 → 触发 Plan B
def verify_opensource():
    from cosyvoice.cli.cosyvoice import CosyVoice
    cosyvoice = CosyVoice(model_path="path/to/CosyVoice")
    # 注意: 开源版无 enable_word_timestamp 参数
    # 测试预期: 输出的 segment 中不含 words/start_ms/end_ms 字段 → FAIL

# ===== Plan B: FunASR SeACo-Paraformer（字级时间戳对齐）=====
# 零新增依赖，复用已有 FunASR 技术栈
def plan_b_funasr_align():
    from funasr import AutoModel
    # 使用 SeACo-Paraformer（支持字级时间戳输出）
    model = AutoModel(model="seaco-paraformer")
    # 或 paraformer-zh-streaming + 字级对齐
    res = model.generate(input="test.wav", return_timestamp=True)
    # 提取字级时间戳，转换为 TtsSegment.words 格式
    # 返回: [("你", 0, 150), ("好", 150, 300), ...]
```

**通过标准**：

| 检查项                                       | 目标                          |
| :------------------------------------------- | :---------------------------- |
| **路线1（云端）**：`word_timestamp` 字段存在 | 每个字带时间戳，精度≤50ms     |
| **路线2（开源）**：明确记录不支持            | 触发 Plan B，不以 FAIL 计     |
| **Plan B**：SeACo-Paraformer 字级时间戳可用  | 对齐误差≤100ms（放宽至100ms） |

**输出**：`eval/reports/cosyvoice_timestamp.json`

```json
{
  "date": "2026-09-01",
  "route": "cloud",  // "cloud" | "opensource" | "plan_b"
  "timestamp_supported": true,
  "sample_text": "你好世界，这是时间戳测试。",
  "first_word": {"word": "你好", "start_ms": 0, "end_ms": 320},
  "tts_first_packet_ms": 280,
  "status": "PASSED"
}
```

### V-03: FunASR流式分片验证

**目标**：确认FunASR能实现分片识别，不等VAD端点即输出增量文本。

**操作步骤**：

```python
# verify_funasr_streaming.py
from funasr import AutoModel

model = AutoModel(
    model="paraformer-zh-streaming",  # ✅ 流式模型（离线版为 paraformer-zh）
    vad_model="fsmn-vad",
    chunk_size=[5, 10, 5],
    chunk_stride=600,
)

# 模拟流式输入：每200ms送入一个音频块
def simulate_stream(audio_path):
    for chunk in audio_chunks(audio_path, chunk_ms=200):
        result = model.generate(input=chunk, is_final=False)
        yield result  # 增量输出

# 检查是否在音频结束前就有输出
first_result = next(simulate_stream("test.wav"))
print(f"First result before audio ends: {first_result.get('text')}")
```

**通过标准**：

| 检查项             | 目标                        |
| :----------------- | :-------------------------- |
| 首字延迟           | ≤300ms                      |
| 增量输出频率       | 至少每500ms输出一次         |
| `is_final`语义正确 | 最后一句为True，中间为False |
| VAD端点正确触发    | 检测到静音2s后自动final     |

**输出**：`eval/reports/funasr_streaming.json`

```json
{
  "date": "2026-09-01",
  "first_word_ms": 180,
  "avg_chunk_interval_ms": 320,
  "vad_endpoint_detected": true,
  "status": "PASSED"
}
```

### V-04: LLM流式输出验证

**目标**：确认DeepSeek API（或其他选型）能输出流式SSE事件，格式符合预期。

**操作步骤**：调用API并检查SSE事件流结构。

**通过标准**：首token ≤500ms；事件结构包含`choices[0].delta.content`字段；支持`stop`信号。

**输出**：`eval/reports/llm_streaming.json`

### V-05: 音画同步测量脚本原型（含工具自校准）

**目标**：验证同步测量方法是否可行——**在正式开发前先确认"测量方法"本身有效**。

**方法**：口型帧中唇部运动强度（像素变化量）与音频能量包络做互相关（cross-correlation），峰值位置即时差。

**原型代码**：

```python
# eval/sync_measure_prototype.py
import numpy as np
from scipy.signal import correlate

def measure_av_sync(
    audio_energy: np.ndarray,
    lip_motion: np.ndarray,
    frame_ms: int = 20  # 默认20ms（25fps）
) -> int:
    """
    计算音画时差（毫秒）
    audio_energy: 音频能量包络（每帧对应值）
    lip_motion: 口型运动强度序列（每帧对应值）
    frame_ms: 每帧时长（毫秒）
    返回: audio领先lip的毫秒数（正=音频领先）

    v1.1 修正（自校准发现 v1.0 bug）：
      1. 用 mode='full' + 显式 lag 轴，替代 mode='same'+n//2（±1 帧换算歧义）
      2. scipy.signal.correlate(a, v)[k] = Σ a[n+k]·v[n]，峰值 k = v 领先 a 的帧数；
         此处 a=audio, v=lip，故取负返回（v1.0 符号相反，负偏移场景会测错）
    """
    audio_norm = (audio_energy - audio_energy.mean()) / (audio_energy.std() + 1e-9)
    lip_norm = (lip_motion - lip_motion.mean()) / (lip_motion.std() + 1e-9)
    correlation = correlate(audio_norm, lip_norm, mode='full')
    lags = np.arange(-(len(audio_norm) - 1), len(lip_norm))
    lag_idx = int(lags[np.argmax(correlation)])
    return -lag_idx * frame_ms
```

**通过标准**：

1. **工具自校准（必须先做）**：对人工合成的测试数据（已知偏移量 +100ms）运行测量脚本，确认测量结果与已知偏移偏差 ≤10ms。
2. **实测验证**：至少对3个人工标注的视频测试，测量结果与肉眼观察偏差 ≤30ms。

> 对应 DESIGN §5.4 坑 5"评估工具校准优先"。

------

## 4. 验证执行计划

| 顺序 | 脚本           | 环境       | 执行时机  | 阻塞后续         |
| :--- | :------------- | :--------- | :-------- | :--------------- |
| 1    | V-01 MuseTalk  | 云GPU      | 第1天上午 | 阻塞P1全流程     |
| 2    | V-02 CosyVoice | 云GPU/本机 | 第1天上午 | 阻塞音画同步设计 |
| 3    | V-03 FunASR    | 本机       | 第1天下午 | 阻塞P2全链路     |
| 4    | V-04 LLM       | 本机       | 第1天下午 | 阻塞P2全链路     |
| 5    | V-05 同步测量  | 本机       | 第2天     | 阻塞FR-07验收    |

**总验证耗时**：V-01~V-05 约 1 天（含环境搭建）——符合PRD"启动后第一天验证"的要求。

> **V-06（端到端Mock闭环）**：此为 P1 交付物，非前置验证。在 V-01~V-05 全部通过后执行，产出第一个 demo 视频。

------

## 5. 验证结果台账

每个脚本的验证结果统一记录在 `eval/reports/` 目录下，格式见各脚本通过标准中的JSON模板。

**验证失败时**：

1. 记录失败原因（代码、截图、日志）
2. 在 `eval/reports/FAILURE_LOG.md` 中追加一条
3. 触发对应的失败预案（见各脚本"失败预案"节）
4. 若预案也失败，**暂停该方向开发，重新评估模型选型**

------

## 6. 版本与变更管理

| 版本     | 日期           | 变更内容                                                     | 作者 |
| :------- | :------------- | :----------------------------------------------------------- | :--- |
| v0.1     | 2026-09-01     | 初稿                                                         | -    |
| **v1.0** | **2026-09-01** | **定稿：修正CosyVoice API（云端/开源双路线+Plan B改用FunASR）、修正FunASR模型名、修正V-05 frame_ms+自校准、V-06移出、清除引用标记** | -    |
| **v1.1** | **2026-09-01** | **勘误：V-05 原型修正（自校准发现 v1.0 bug——mode='same'+n//2 有 ±1 帧换算歧义、correlate 峰值符号相反），改为 mode='full'+显式 lag 轴+取负** | -    |
