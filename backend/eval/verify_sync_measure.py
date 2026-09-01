"""V-05 音画同步测量脚本原型（含工具自校准）— EVAL-P1 §3 V-05

方法：口型运动强度（像素变化量）与音频能量包络做互相关，峰值位置即时差。
通过标准（EVAL-P1 §3 V-05）：
  1. 工具自校准：合成已知偏移（+100ms 等）数据，测量偏差 ≤10ms（先校准再实测，DESIGN §5.4 坑 5）
  2. 实测（可选）：对人工标注视频，测量与肉眼偏差 ≤30ms（需真实数据，本脚本只做自校准）

用法:
  python verify_sync_measure.py [--report-dir reports]

输出: eval/reports/sync_measure.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from scipy.signal import correlate

DEFAULT_FRAME_MS = 20  # 25fps → 40ms/帧？否：EVAL-P1 定义 frame_ms=20 为采样粒度
# EVAL-P1 V-05 原型使用 frame_ms=20（每帧对应值），此处保持一致


def measure_av_sync(audio_energy: np.ndarray, lip_motion: np.ndarray, frame_ms: int = 20) -> int:
    """计算音画时差（毫秒）。返回正 = audio 领先 lip。

    scipy.signal.correlate(a, v)[k] = Σ a[n+k]·v[n]，峰值 k = **v 领先 a** 的帧数；
    这里 a=audio, v=lip，故 audio 领先 lip = -(峰值 k)。取负后返回。
    用 mode='full' + 显式 lag 轴，避免 mode='same' 的 ±1 帧换算歧义。
    """
    if len(audio_energy) != len(lip_motion):
        raise ValueError("audio_energy 与 lip_motion 长度必须一致")
    audio_norm = (audio_energy - audio_energy.mean()) / (audio_energy.std() + 1e-9)
    lip_norm = (lip_motion - lip_motion.mean()) / (lip_motion.std() + 1e-9)
    correlation = correlate(audio_norm, lip_norm, mode="full")
    lags = np.arange(-(len(audio_norm) - 1), len(lip_norm))
    lag_idx = int(lags[np.argmax(correlation)])
    return -lag_idx * frame_ms  # k = lip 领先 audio → 取负得 audio 领先 lip


def _shift(sig: np.ndarray, frames: int) -> np.ndarray:
    """将信号延后 frames 帧（frames>0：前置补零；frames<0：提前，尾部补零）。"""
    if frames >= 0:
        return np.pad(sig, (frames, 0))[: len(sig)]
    return np.pad(sig, (0, -frames))[-len(sig):]


def synth_signals(n_frames: int = 500, offset_ms: int = 100, frame_ms: int = 20, seed: int = 42):
    """合成已知偏移的测试信号：audio 领先 lip 恰好 offset_ms（正=音频先出，口型滞后）。

    构造方式：base 为公共信号，audio = base（完整结构），lip = base 平移
    offset_frames 帧。这样负偏移时 audio 依然完整，避免边缘截断污染测量。
    """
    rng = np.random.default_rng(seed)
    base = np.zeros(n_frames)
    # 模拟音节脉冲：每 25 帧一个发音段，幅度随机
    for i in range(0, n_frames, 25):
        win = np.hanning(12)
        base[i : i + 12] += rng.uniform(0.5, 1.0) * win
    offset_frames = round(offset_ms / frame_ms)
    audio = base.copy()
    lip = _shift(base, offset_frames)  # lip 延后 offset_frames → audio 领先 lip
    return audio, lip


def self_calibrate(frame_ms: int = 20) -> list[dict]:
    """自校准：多组已知偏移，验证测量误差 ≤10ms。

    名义偏移可能被帧粒度取整（如 50ms=2.5 帧→2 帧=40ms），
    误差按"实际合成偏移"计算，避免把取整误判为测量误差。
    """
    results = []
    for offset_ms in (50, 100, 200, -100):
        audio, lip = synth_signals(offset_ms=offset_ms, frame_ms=frame_ms)
        measured = measure_av_sync(audio, lip, frame_ms)
        actual = round(offset_ms / frame_ms) * frame_ms
        results.append(
            {
                "offset_ms": offset_ms,
                "actual_offset_ms": actual,
                "measured_ms": measured,
                "error_ms": int(measured - actual),
            }
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="V-05 音画同步测量脚本（自校准）")
    parser.add_argument("--report-dir", default="reports", help="报告输出目录（默认 reports/）")
    parser.add_argument("--frame-ms", type=int, default=DEFAULT_FRAME_MS)
    args = parser.parse_args()

    cal = self_calibrate(args.frame_ms)
    max_error = max(abs(r["error_ms"]) for r in cal)
    passed = max_error <= 10  # EVAL-P1 通过标准：偏差 ≤10ms

    report = {
        "date": "2026-09-01",
        "frame_ms": args.frame_ms,
        "method": "cross-correlation(audio_energy, lip_motion)",
        "calibration": cal,
        "max_error_ms": max_error,
        "status": "PASSED" if passed else "FAILED",
        "note": "自校准通过后，实测口径见 EVAL-测试集定义 §4.2（人工抽检 T4/T5 条目，与脚本值对照偏差≤30ms）",
    }

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    out = report_dir / "sync_measure.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n报告已写入: {out.resolve()}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
