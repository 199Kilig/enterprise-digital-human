"""流式 vs 离线 ASR 模型精度对比 —— 决定要不要上 two-pass。

**为什么要这个脚本**：低延迟用的 `paraformer-zh-streaming` 是为速度牺牲精度的模型；
FunASR 的离线版 `paraformer-zh` 精度更高。"端点后用离线模型把整句重识别一遍"
（two-pass：流式出中间结果保延迟 + 离线出最终文本保准确率）是行业标准做法，
但**只有当流式模型在真实样本上确实明显更差时才值得做**——本脚本就是那个判据。

两列口径：
  streaming = 复现前端链路（600ms 分片喂 `StreamingAsr`，与 `useMicCapture` 一致）
  offline   = 整段音频一次喂 `paraformer-zh`

输出：每条的字准率（1 - CER）+ 错例逐字对照 + 总体结论（差值是否够大）。

⚠️ 首次运行会从 ModelScope 下载 `paraformer-zh` 权重（约 0.9GB），可能几分钟。

用法：
    cd backend/eval && ../.venv/Scripts/python.exe verify_asr_models.py
    ../.venv/Scripts/python.exe verify_asr_models.py --only t1-01 t6-01     # 指定条目
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import wave
from pathlib import Path

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

SR = 16000
REPO = Path(__file__).resolve().parents[2]
SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
AUDIO_DIR = REPO / "data" / "testset" / "audio"
REPORT = Path(__file__).resolve().parent / "reports" / "asr_model_compare.json"

STREAM_CHUNK_SAMPLES = 9600  # 600ms @16k，与 asr/streaming.py 的 CHUNK_STRIDE 一致

# 期望文本取自 docs/eval/EVAL-测试集定义.md（只列 data/testset/audio 里已有的 7 条）
EXPECT: dict[str, str] = {
    "t1-01": "你们家运费怎么算",
    "t1-02": "下单后多久能发货",
    "t2-01": "我想退货流程怎么走",
    "t2-02": "退款一般多久到账",
    "t3-01": "你们发货要多久那运费呢包邮的话几天能到",
    "t6-01": "在吗",
    "t6-02": "谢谢",
}


def normalize(s: str) -> str:
    """去标点/空白，统一比较口径（识别结果不产出标点，期望文本里有）。"""
    return re.sub(r"[，。？！、；：\s,.?!;:]", "", s or "")


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = cur
    return prev[-1]


def cer(ref: str, hyp: str) -> float:
    ref, hyp = normalize(ref), normalize(hyp)
    if not ref:
        return 0.0 if not hyp else 1.0
    return levenshtein(ref, hyp) / len(ref)


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        n, ch = w.getnframes(), w.getnchannels()
        data = np.frombuffer(w.readframes(n), dtype="<i2").astype(np.float32) / 32768.0
        if ch == 2:
            data = data.reshape(-1, 2).mean(axis=1)
    return data


_warmed = False


def run_streaming(audio: np.ndarray) -> tuple[str, float]:
    """复现前端：600ms 分片逐片 push，最后一片 end=True。

    ⚠️ 必须先预热 `get_model()`：模型冷启动约 22s，若算进第一条的计时，会把
    7 条的平均耗时虚高到秒级，与 offline（其加载时间被单独排除）不可比。
    """
    global _warmed
    import asr.streaming as streaming

    if not _warmed:
        streaming.get_model()  # 预热，与 offline 的计时口径对齐
        _warmed = True

    asr = streaming.StreamingAsr()
    t0 = time.perf_counter()
    for off in range(0, len(audio), STREAM_CHUNK_SAMPLES):
        piece = audio[off: off + STREAM_CHUNK_SAMPLES]
        is_last = off + STREAM_CHUNK_SAMPLES >= len(audio)
        asr.push((piece * 32767).astype("<i2").tobytes(), is_last=is_last)
    return asr.full_text, (time.perf_counter() - t0) * 1000


_offline_model = None


def run_offline(audio: np.ndarray) -> tuple[str, float]:
    """整段音频一次喂离线模型（paraformer-zh）。"""
    global _offline_model
    if _offline_model is None:
        from funasr import AutoModel

        t_load = time.perf_counter()
        print("  加载 paraformer-zh（首次可能需下载 ~0.9GB）…", flush=True)
        _offline_model = AutoModel(model="paraformer-zh")
        print(f"  加载完成 {time.perf_counter() - t_load:.1f}s", flush=True)
    t0 = time.perf_counter()
    res = _offline_model.generate(input=audio, batch_size_s=300)
    text = (res[0].get("text") if res else "") or ""
    return text.strip(), (time.perf_counter() - t0) * 1000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=None, help="只跑指定条目，如 t1-01 t6-01")
    a = ap.parse_args()

    items = a.only or sorted(EXPECT)
    rows = []
    print(f"{'条目':8s} {'期望':26s} {'streaming':26s} {'offline':26s}  CER(流/离)")
    print("-" * 118)
    for key in items:
        wav = AUDIO_DIR / f"{key}.wav"
        if not wav.exists():
            print(f"{key:8s} 缺音频，跳过")
            continue
        audio = read_wav(wav)
        s_text, s_ms = run_streaming(audio)
        o_text, o_ms = run_offline(audio)
        exp = EXPECT.get(key, "")
        s_cer, o_cer = cer(exp, s_text), cer(exp, o_text)
        rows.append({"id": key, "expect": exp, "streaming": s_text, "offline": o_text,
                     "cer_streaming": round(s_cer, 4), "cer_offline": round(o_cer, 4),
                     "streaming_ms": round(s_ms, 1), "offline_ms": round(o_ms, 1)})
        print(f"{key:8s} {exp:26s} {s_text:26s} {o_text:26s}  {s_cer:.3f} / {o_cer:.3f}")

    if not rows:
        print("没有可比对的数据")
        return 1

    ms = sum(r["cer_streaming"] for r in rows) / len(rows)
    mo = sum(r["cer_offline"] for r in rows) / len(rows)
    print("\n" + "=" * 118)
    print(f"平均 CER：streaming {ms:.3f}（字准率 {1 - ms:.1%}） | offline {mo:.3f}（字准率 {1 - mo:.1%}）")
    print(f"平均耗时：streaming {sum(r['streaming_ms'] for r in rows) / len(rows):.0f}ms | "
          f"offline {sum(r['offline_ms'] for r in rows) / len(rows):.0f}ms（不含首次模型加载）")
    delta = ms - mo
    if delta >= 0.05:
        print(f"\n判定：offline 明显更准（CER 差 {delta * 100:.1f} 个百分点）→ **two-pass 值得做**")
    elif delta > 0.01:
        print(f"\n判定：offline 略优（差 {delta * 100:.1f}pt）→ 收益有限，优先查参数/热词")
    else:
        print(f"\n判定：两者打平（差 {delta * 100:.1f}pt）→ **two-pass 在合成音频上无收益**；"
              f"要判断真实场景，需要真实麦克风样本（用 ASR_DEBUG=1 采集后重跑本脚本口径）")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({"date": "2026-09-17", "rows": rows,
                                  "mean_cer_streaming": round(ms, 4),
                                  "mean_cer_offline": round(mo, 4)},
                                 ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
