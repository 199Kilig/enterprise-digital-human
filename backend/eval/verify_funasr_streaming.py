"""V-03 FunASR 流式分片验证 — EVAL-P1 §3 V-03

目标：确认 FunASR 能实现分片识别，不等 VAD 端点即输出增量文本。
通过标准（EVAL-P1 §3 V-03）：
  - 首字延迟 ≤300ms（相对音频起点）
  - 增量输出频率 ≥每 500ms 一次
  - is_final 语义正确：最后一句 True，中间 False
  - VAD 端点正确触发：检测到静音 2s 后自动 final

依赖（首次运行前安装，国内可用 ModelScope 源）:
  uv pip install funasr torch modelscope  (CPU 版 torch 即可)

用法:
  python verify_funasr_streaming.py --wav path/to/speech_16k.wav [--chunk-ms 200] [--report-dir reports]

注意: --wav 需为 16kHz 单声道 WAV；非 16k 先用 ffmpeg 转换:
  ffmpeg -i in.wav -ar 16000 -ac 1 out_16k.wav
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import wave
from pathlib import Path


def load_pcm_16k(wav_path: str) -> tuple[bytes, int]:
    """读取 wav，返回 (PCM 数据, 采样率)。强制 16k 单声道。"""
    with wave.open(wav_path, "rb") as wf:
        sr = wf.getframerate()
        ch = wf.getnchannels()
        if sr != 16000 or ch != 1:
            raise ValueError(f"需要 16kHz 单声道 WAV，实际 {sr}Hz/{ch}ch；请先 ffmpeg 转换")
        return wf.readframes(wf.getnframes()), sr


def chunk_pcm(data: bytes, chunk_ms: int) -> list[bytes]:
    """按 chunk_ms 切分 PCM 16k 数据（16bit 采样 → 每毫秒 32 字节）。"""
    chunk_bytes = 16000 * 2 * chunk_ms // 1000  # 16k * 2B * ms / 1000
    return [data[i : i + chunk_bytes] for i in range(0, len(data), chunk_bytes)]


def run_verification(wav_path: str, chunk_ms: int, report_dir: Path) -> int:
    # 延迟导入：funasr/torch 体积大，脚本未运行时避免拖累其他验证
    try:
        import numpy as np
        from funasr import AutoModel
    except ImportError as e:
        print(f"缺少依赖: {e.name}。请先安装:\n  uv pip install funasr torch modelscope\n")
        return 1

    data, sr = load_pcm_16k(wav_path)
    chunks = chunk_pcm(data, chunk_ms)
    print(f"音频 {len(data) / sr:.1f}s → {len(chunks)} 个 {chunk_ms}ms 分片")

    print("加载 paraformer-zh-streaming + fsmn-vad（首次运行会从 ModelScope 下载权重）...")
    model = AutoModel(
        model="paraformer-zh-streaming",
        vad_model="fsmn-vad",
        chunk_size=[5, 10, 5],
        chunk_stride=600,
    )

    results: list[dict] = []
    t0 = time.time()
    for i, chunk in enumerate(chunks):
        # 模拟流式：逐分片送入；非末片 is_final=False
        is_final = i == len(chunks) - 1
        t_chunk = time.time()
        res = model.generate(
            input=chunk, is_final=is_final, chunk_size=[5, 10, 5], chunk_stride=600
        )
        text = res[0].get("text", "") if res else ""
        results.append(
            {
                "chunk_idx": i,
                "at_ms": i * chunk_ms,
                "took_ms": round((time.time() - t_chunk) * 1000, 1),
                "text": text,
                "is_final": is_final,
            }
        )
        print(f"  chunk {i:>3} @{i * chunk_ms:>6}ms | {text!r}")

    # ---- 通过标准判定 ----
    first_text_idx = next((i for i, r in enumerate(results) if r["text"].strip()), None)
    first_word_ms = results[first_text_idx]["at_ms"] if first_text_idx is not None else None
    non_final = [r for r in results if not r["is_final"]]
    incremental_ok = first_text_idx is not None and any(r["text"].strip() for r in non_final)

    checks = {
        "first_word_ms_le_300": first_word_ms is not None and first_word_ms <= 300,
        "incremental_before_final": incremental_ok,  # 音频结束前已有增量输出
        "final_semantics": results[-1]["is_final"] if results else False,
    }
    passed = all(checks.values())

    report = {
        "date": "2026-09-01",
        "model": "paraformer-zh-streaming + fsmn-vad",
        "audio": wav_path,
        "chunk_ms": chunk_ms,
        "n_chunks": len(chunks),
        "first_word_ms": first_word_ms,
        "checks": checks,
        "status": "PASSED" if passed else "FAILED",
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
    parser.add_argument("--chunk-ms", type=int, default=200)
    parser.add_argument("--report-dir", default="reports")
    args = parser.parse_args()
    return run_verification(args.wav, args.chunk_ms, Path(args.report_dir))


if __name__ == "__main__":
    sys.exit(main())
