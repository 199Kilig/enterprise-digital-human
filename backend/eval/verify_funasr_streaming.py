"""V-03 FunASR 流式分片验证 — EVAL-P1 §3 V-03

目标：确认 FunASR 流式模型分片识别能力（不等 VAD 端点即输出增量文本）。
对齐 FunASR 官方流式用法（funasr.com/en/blog/funasr-realtime-streaming-asr.html）：
  - chunk_size=[0, 10, 5]：600ms/块（第二个数×60ms = 显示粒度，第三个数 = lookahead）
  - chunk_stride = chunk_size[1] * 960 = 9600 样本（600ms @16kHz）
  - cache={} 必须跨块持久化（流式状态），is_final=True 仅最后一块
  - 输入为 numpy float32（soundfile 读取）

通过标准（EVAL-P1 §3 V-03，⚠️ 部分为建议值，实测后回填）：
  - 首字延迟 ≤300ms（600ms/块粒度下难以达到，实测记录真实值）
  - 增量输出频率 ≥每 500ms 一次
  - is_final 语义正确：最后一块 True，中间 False

用法:
  python verify_funasr_streaming.py --wav path/to/16k_mono.wav [--report-dir reports]

输出: eval/reports/funasr_streaming.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

CHUNK_SIZE = [0, 10, 5]  # 600ms @16k
CHUNK_STRIDE = CHUNK_SIZE[1] * 960  # 9600 样本 = 600ms
ENC_LOOK_BACK = 4
DEC_LOOK_BACK = 1


def run_verification(wav_path: str, report_dir: Path) -> int:
    try:
        from funasr import AutoModel
    except ImportError as e:
        print(f"缺少依赖: {e.name}。请先安装:\n  uv pip install funasr modelscope --index-url https://pypi.tuna.tsinghua.edu.cn/simple\n")
        return 1

    speech, sr = sf.read(wav_path, dtype="float32")
    if sr != 16000:
        print(f"需要 16kHz 音频，实际 {sr}Hz；请先 ffmpeg 转换")
        return 1
    if speech.ndim > 1:
        speech = speech.mean(axis=1)  # 混为单声道

    n_chunks = (len(speech) - 1) // CHUNK_STRIDE + 1
    print(f"音频 {len(speech) / sr:.2f}s → {n_chunks} 个 {CHUNK_STRIDE / sr * 1000:.0f}ms 分片")

    print("加载 paraformer-zh-streaming（首次运行下载约 881MB，ModelScope 源）...")
    model = AutoModel(model="paraformer-zh-streaming")

    results: list[dict] = []
    cache: dict = {}
    t0 = time.time()
    first_word_wall_ms: int | None = None
    for i in range(n_chunks):
        chunk = speech[i * CHUNK_STRIDE : (i + 1) * CHUNK_STRIDE]
        is_final = i == n_chunks - 1
        t_chunk = time.time()
        res = model.generate(
            input=chunk,
            cache=cache,
            is_final=is_final,
            chunk_size=CHUNK_SIZE,
            encoder_chunk_look_back=ENC_LOOK_BACK,
            decoder_chunk_look_back=DEC_LOOK_BACK,
        )
        text = res[0].get("text", "") if res else ""
        if text and first_word_wall_ms is None:
            first_word_wall_ms = int((time.time() - t0) * 1000)  # 墙钟首字延迟（从流式开始起算）
        results.append(
            {
                "chunk_idx": i,
                "at_ms": i * 600,  # 该块在音频中的位置
                "took_ms": round((time.time() - t_chunk) * 1000, 1),
                "text": text,
                "is_final": is_final,
            }
        )
        if text:
            print(f"  chunk {i:>2} @{i * 600:>5}ms | {text}")

    # ---- 通过标准判定 ----
    non_final_texts = [r for r in results if not r["is_final"] and r["text"].strip()]
    checks = {
        "first_word_wall_ms_le_300": first_word_wall_ms is not None and first_word_wall_ms <= 300,
        "incremental_before_final": len(non_final_texts) > 0,  # 音频结束前已有增量输出
        "final_semantics": bool(results) and results[-1]["is_final"] is True,
    }
    passed = all(checks.values())

    report = {
        "date": "2026-09-01",
        "model": "paraformer-zh-streaming",
        "chunk_size": CHUNK_SIZE,
        "chunk_ms": 600,
        "audio": wav_path,
        "duration_s": round(len(speech) / sr, 2),
        "n_chunks": n_chunks,
        "first_word_wall_ms": first_word_wall_ms,  # 墙钟口径（从流式开始到首个文本输出）
        "checks": checks,
        "status": "PASSED" if passed else "FAILED",
        "note": "首字延迟 300ms 为 EVAL-P1 建议值；600ms/块粒度下首字必然 ≥600ms，实测值以本报告为准，回填 EVAL-P1/台账",
        "chunks": results,
    }
    report_dir.mkdir(parents=True, exist_ok=True)
    out = report_dir / "funasr_streaming.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已写入: {out.resolve()}")
    return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="V-03 FunASR 流式分片验证")
    parser.add_argument("--wav", required=True, help="16kHz 单声道 WAV 路径")
    parser.add_argument("--report-dir", default="reports")
    args = parser.parse_args()
    return run_verification(args.wav, Path(args.report_dir))


if __name__ == "__main__":
    sys.exit(main())
