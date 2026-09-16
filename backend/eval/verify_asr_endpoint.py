"""ASR 端点/静音阈值标定 + 前端 RMS 口径核对（FR-01 端点判定 / FR-06 打断前置）。

被测对象是**前端**的两处判定逻辑（ADR-004：VAD/端点在前端）：
  `frontend/public/worklets/mic-processor.js`（每 100ms 一帧 + 帧 RMS）
  `frontend/src/hooks/useMicCapture.ts`（静音阈值 / 端点 / 打断判据）

本脚本做两件事：

  1. **端点阈值标定**：固定测试集真实音频 + 人工场景（底噪 / 句中停顿 / 瞬态 / 模拟回声），
     离线复现前端判定逻辑，区分两类错误：
       - `BREAK_PREMATURE`：端点落在真实有声段内部 → 截断用户正在说的话（严重）
       - `NO_ENDPOINT`：有声段结束后端点始终不触发 → 用户说完数字人不响应（漏触发）
     有声段用短时能量自行检测（不依赖文件时长——edge-tts 音频首尾自带静音，用文件时长会误判）。

  2. **RMS 口径校验**：`mic_rms_probe.mjs` 用 node **忠实执行 worklet 原文件**（不手抄逻辑）
     取 `worklet` 序列，与 Python 实现的「整 100ms 窗口 RMS」（`theory`）逐场景比对。
     比值应恒为 1.000 —— 这就是 worklet RMS 计算正确性的证据。
     （2026-09-15 之前该比值为 0.81~0.83、瞬态场景 0.31：worklet 的 sum 分母口径写错，
       详见 `reports/asr_mic_check.md` §2。）

判定配置对照：
    legacy = 固定静音阈值 0.012 + 固定打断判据 0.036 单帧        （2026-09-15 之前）
    target = 自适应静音阈值 max(底噪×3, 0.004) + 打断判据 max(底噪×8, 0.03) 连续 2 帧（当前）

用法：
    cd backend/eval && ../.venv/Scripts/python.exe verify_asr_endpoint.py --report-dir reports
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

SR = 16000
FRAME = 1600          # 100ms @16k（mic-processor.js 的 FRAME）
RENDER_QUANTUM = 128  # Web Audio 每次 process() 的样本数
REPO = Path(__file__).resolve().parents[2]
MIC_JS = REPO / "frontend" / "public" / "worklets" / "mic-processor.js"
PROBE = Path(__file__).resolve().parent / "mic_rms_probe.mjs"
AUDIO_DIR = REPO / "data" / "testset" / "audio"

# ---- 前端常量（与 useMicCapture.ts 对齐；改动前端须同步这里，否则标定失效）----
SILENCE_RMS = 0.012   # 静音阈值初始值 / legacy 固定阈值
SILENCE_MS = 1200     # 连续静音判端点
FLOOR_MIN = 0.004     # 自适应阈值下限
FLOOR_MULT = 3.0      # 静音阈值 = 底噪估计 × 3
BARGE_MULT = 8.0      # 打断判据 = 底噪估计 × 8
BARGE_MIN = 0.03      # 打断判据绝对下限
BARGE_FRAMES = 2      # 打断去抖：连续超阈值帧数（100ms/帧）
LEGACY_BARGE_MULT = 3.0  # 2026-09-15 之前的固定打断判据 = SILENCE_RMS × 3

NOISE_FLOOR = 0.002   # RUNBOOK：实测环境噪声约 0.002~0.008


# ---------------- 音频 IO ----------------

def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        assert w.getsampwidth() == 2, f"{path.name} 不是 16bit"
        n, rate, ch = w.getnframes(), w.getframerate(), w.getnchannels()
        data = np.frombuffer(w.readframes(n), dtype="<i2").astype(np.float32) / 32768.0
        if ch == 2:
            data = data.reshape(-1, 2).mean(axis=1)
    if rate != SR:
        idx = (np.arange(int(len(data) / rate * SR)) * rate / SR).astype(int)
        data = data[np.clip(idx, 0, len(data) - 1)]
    return data


def write_pcm16(path: Path, x: np.ndarray) -> None:
    path.write_bytes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


def rms(x: np.ndarray) -> float:
    if not x.size:
        return 0.0
    return float(math.sqrt(float(np.mean(x.astype(np.float64) ** 2)) + 1e-12))


def noise(n: int, level: float, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    w = rng.uniform(-1.0, 1.0, n)
    return (w / rms(w) * level).astype(np.float32)


def active_spans(x: np.ndarray, min_ms: int = 60, gap_ms: int = 200) -> list[tuple[float, float]]:
    """短时能量粗检测有声段（10ms 窗）。用于判定端点是否发生「说话中途」。

    门限自适应：用本信号自身帧能量的低分位估底噪（20 分位）×3。
    固定门限在「底噪本身就高于门限」的场景（如底噪 0.011）会把整段判成有声 → 假阳性。
    """
    win, step_ms = 160, 10
    n = len(x) // win
    if n == 0:
        return []
    e = np.sqrt((x[: n * win].reshape(n, win).astype(np.float64) ** 2).mean(axis=1))
    floor_est = float(np.percentile(e, 20))
    act = e > max(floor_est * 3.0, 0.006)
    gap_frames = gap_ms // step_ms
    spans, start = [], None
    for i, a in enumerate(act):
        if a and start is None:
            start = i
        elif not a and start is not None:
            if act[i: i + gap_frames].any():
                continue  # 短暂间隙并入同一段
            if (i - start) * step_ms >= min_ms:
                spans.append((start * step_ms / 1000, i * step_ms / 1000))
            start = None
    if start is not None and (n - start) * step_ms >= min_ms:
        spans.append((start * step_ms / 1000, n * step_ms / 1000))
    return spans


# ---------------- 场景构造 ----------------

def build_scenes(voice: np.ndarray, other: np.ndarray) -> list[dict]:
    pad = np.zeros(int(0.3 * SR), dtype=np.float32)
    tail = np.zeros(int(2.5 * SR), dtype=np.float32)

    def wrap(v: np.ndarray) -> np.ndarray:
        return np.concatenate([pad, v, tail])

    base = wrap(voice)

    def mix(sig: np.ndarray, level: float, seed: int) -> np.ndarray:
        return sig + noise(len(sig), level, seed)

    pause1 = np.concatenate([voice[: len(voice) // 2], np.zeros(SR, dtype=np.float32), voice[len(voice) // 2:]])
    pause15 = np.concatenate([voice[: len(voice) // 2], np.zeros(int(1.5 * SR), dtype=np.float32),
                              voice[len(voice) // 2:]])
    transient = noise(int(4 * SR), NOISE_FLOOR, 13).astype(np.float32)
    transient[int(1.0 * SR): int(1.05 * SR)] = noise(int(0.05 * SR), 0.2, 14)

    return [
        {"id": "clean", "desc": "底噪0.002 + 语音 + 2.5s尾静音",
         "sig": mix(base, NOISE_FLOOR, 7), "note": "基线：应正常收尾"},
        {"id": "floor_0.006", "desc": "底噪0.006", "sig": mix(base, 0.006, 8), "note": "中等噪声："},
        {"id": "floor_0.011", "desc": "底噪0.011（紧贴旧阈值0.012）", "sig": mix(base, 0.011, 9),
         "note": "临界环境"},
        {"id": "floor_0.015", "desc": "底噪0.015（>旧阈值）", "sig": mix(base, 0.015, 15),
         "note": "legacy 下端点永不触发；target 应恢复"},
        {"id": "pause_1.0s", "desc": "语音中插 1.0s 静音（<1.2s）",
         "sig": mix(wrap(pause1), NOISE_FLOOR, 10), "note": "句中停顿：不应断句"},
        {"id": "pause_1.5s", "desc": "语音中插 1.5s 静音（>1.2s）",
         "sig": mix(wrap(pause15), NOISE_FLOOR, 11), "note": "设计上会断句（切句代价，记录）"},
        {"id": "transient_50ms", "desc": "底噪中 50ms 强噪声脉冲（键盘/咳嗽）",
         "sig": transient, "note": "检验瞬态是否被当插话"},
        {"id": "silence_only", "desc": "2s 纯底噪（无人声）", "sig": noise(2 * SR, NOISE_FLOOR, 12),
         "note": "录音后不说话：1.2s 后收尾属设计"},
        {"id": "echo_selfplay", "desc": "另一段语音模拟「数字人外放被麦克风收到」",
         "sig": mix(wrap(other), NOISE_FLOOR, 16), "note": "无 AEC 时必然命中打断判据（离线不可验 AEC）"},
    ]


# ---------------- 前端逻辑复现 ----------------

def rms_series_worklet(sig: np.ndarray) -> list[float]:
    """node 探针忠实执行 frontend/public/worklets/mic-processor.js（不手抄逻辑）。"""
    with tempfile.TemporaryDirectory() as td:
        pcm = Path(td) / "in.pcm"
        write_pcm16(pcm, sig)
        r = subprocess.run(["node", str(PROBE), str(MIC_JS), str(pcm), str(RENDER_QUANTUM)],
                           capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        raise RuntimeError(f"node 探针失败: {r.stderr[:300]}")
    return json.loads(r.stdout)["rms"]


def rms_series_theory(sig: np.ndarray) -> list[float]:
    """理论口径：整 100ms 窗口 RMS（worklet 应当与之一致，比值 1.000）。"""
    n = len(sig) // FRAME
    return [] if n == 0 else [rms(b) for b in sig[: n * FRAME].reshape(n, FRAME)]


def judge(series: list[float], adaptive: bool = False, debounce: int = 1) -> dict:
    """复现 useMicCapture.ts 的判定。

    adaptive=True：底噪估计 floor **只降不升**（min 跟踪）→ 静音阈值 = max(floor×FLOOR_MULT, FLOOR_MIN)，
                   打断判据 = max(floor×BARGE_MULT, BARGE_MIN)；
    adaptive=False（legacy）：静音阈值固定 SILENCE_RMS，打断判据固定 SILENCE_RMS×3。
    debounce：打断需连续超阈值的帧数。
    """
    floor = SILENCE_RMS
    silent, endpoint_frame = 0, None
    barge_hits, barge_triggers, run = 0, 0, 0
    for i, r in enumerate(series):
        if adaptive:
            floor = min(floor, r)
            thr_silence = max(floor * FLOOR_MULT, FLOOR_MIN)
            thr_barge = max(floor * BARGE_MULT, BARGE_MIN)
        else:
            thr_silence = SILENCE_RMS
            thr_barge = SILENCE_RMS * LEGACY_BARGE_MULT

        if r > thr_barge:
            barge_hits += 1
            run += 1
            if run >= debounce:
                barge_triggers += 1
                run = 0
        else:
            run = 0

        if r < thr_silence:
            silent += 100
            if silent >= SILENCE_MS and endpoint_frame is None:
                endpoint_frame = i
        else:
            silent = 0

    return {"endpoint_ms": None if endpoint_frame is None else (endpoint_frame + 1) * 100,
            "barge_frames": barge_hits, "barge_triggers": barge_triggers, "frames": len(series),
            "floor_est": round(floor, 5)}


def _finish(j: dict, s: list[float], spans: list[tuple[float, float]]) -> dict:
    ep = None if j["endpoint_ms"] is None else j["endpoint_ms"] / 1000
    j["rms_mean"] = round(float(np.mean(s)), 5) if s else 0.0
    j["rms_max"] = round(float(np.max(s)), 5) if s else 0.0
    j["barge_ratio"] = round(j["barge_frames"] / j["frames"], 3) if j["frames"] else 0.0
    in_speech = bool(ep is not None and any(a <= ep <= b for a, b in spans))
    if in_speech:
        j["verdict"] = "BREAK_PREMATURE"      # 说话中被断句 → 截断用户语音
    elif spans and ep is None:
        j["verdict"] = "NO_ENDPOINT"          # 用户说完了却收不了尾 → 漏触发
    elif spans and ep is not None and ep < spans[-1][1] + 1.0:
        j["verdict"] = "SHORT_TAIL"           # 收尾早于「有声段结束+1.2s」，偏敏感
    else:
        j["verdict"] = "OK"
    return j


def evaluate(scene: dict, worklet: list[float], theory: list[float],
             spans: list[tuple[float, float]]) -> dict:
    out = {"id": scene["id"], "desc": scene["desc"], "note": scene["note"],
           "spans": [[round(a, 2), round(b, 2)] for a, b in spans]}
    mw, mt = (np.mean(worklet) if worklet else 0.0), (np.mean(theory) if theory else 0.0)
    out["rms_consistency"] = round(float(mw / mt), 4) if mt > 0 else None
    out["legacy"] = _finish(judge(worklet, adaptive=False, debounce=1), worklet, spans)
    out["target"] = _finish(judge(worklet, adaptive=True, debounce=BARGE_FRAMES), worklet, spans)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report-dir", default="reports")
    ap.add_argument("--wav", default="t1-01.wav", help="人声素材")
    ap.add_argument("--echo-wav", default="t3-01.wav", help="模拟数字人外放的音频")
    a = ap.parse_args()

    voice = read_wav(AUDIO_DIR / a.wav)
    other = read_wav(AUDIO_DIR / a.echo_wav)
    print(f"人声素材 {a.wav} {len(voice) / SR:.2f}s RMS {rms(voice):.4f}；"
          f"外放素材 {a.echo_wav} {len(other) / SR:.2f}s RMS {rms(other):.4f}")
    print(f"legacy：静音阈值 {SILENCE_RMS} / 打断判据 {SILENCE_RMS * LEGACY_BARGE_MULT:.3f} 单帧")
    print(f"target：静音阈值 max(底噪×{FLOOR_MULT}, {FLOOR_MIN}) / "
          f"打断判据 max(底噪×{BARGE_MULT}, {BARGE_MIN}) 连续 {BARGE_FRAMES} 帧\n")

    scenes = build_scenes(voice, other)
    results = []
    for sc in scenes:
        spans = active_spans(sc["sig"])
        worklet = rms_series_worklet(sc["sig"])
        theory = rms_series_theory(sc["sig"])
        results.append(evaluate(sc, worklet, theory, spans))

    hdr = (f"{'场景':18s} {'配置':8s} {'rms均值':>8s} {'rms峰':>8s} {'端点s':>6s} "
           f"{'barge帧':>7s} {'barge触发':>9s}  判定")
    print(hdr); print("-" * len(hdr))
    for r in results:
        for label in ("legacy", "target"):
            j = r[label]
            ep = "—" if j["endpoint_ms"] is None else f"{j['endpoint_ms'] / 1000:.1f}"
            print(f"{r['id']:18s} {label:8s} {j['rms_mean']:8.5f} {j['rms_max']:8.5f} {ep:>6s} "
                  f"{j['barge_frames']:>7d} {j['barge_triggers']:>9d}  {j['verdict']}")
        print(f"{'':18s} worklet/theory RMS 一致性={r['rms_consistency']}  spans={r['spans']}")

    print("\n" + "=" * 100)
    print("结论")
    print("=" * 100)
    for label in ("legacy", "target"):
        broken = [r["id"] for r in results if r[label]["verdict"] == "BREAK_PREMATURE"]
        missing = [r["id"] for r in results if r[label]["verdict"] == "NO_ENDPOINT"]
        short = [r["id"] for r in results if r[label]["verdict"] == "SHORT_TAIL"]
        echo = next(r for r in results if r["id"] == "echo_selfplay")[label]
        tr = next(r for r in results if r["id"] == "transient_50ms")[label]
        print(f"  {label:7s} 说话中被断句={broken or '无'}；收不了尾={missing or '无'}；收尾偏早={short or '无'}")
        print(f"          回声场景：命中 {echo['barge_frames']} 帧 / 判定触发 {echo['barge_triggers']} 次；"
              f"瞬态场景：命中 {tr['barge_frames']} 帧 / 判定触发 {tr['barge_triggers']} 次")

    cons = [r["rms_consistency"] for r in results if r["rms_consistency"] is not None]
    print(f"\n  worklet vs 理论整帧 RMS 一致性："
          f"min={min(cons)} max={max(cons)}（1.000 = worklet 口径正确）")

    out_dir = Path(a.report_dir)
    if not out_dir.is_absolute():
        out_dir = Path(__file__).resolve().parent / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {"date": "2026-09-15", "voice_wav": a.wav, "echo_wav": a.echo_wav,
               "mic_js": str(MIC_JS.relative_to(REPO)),
               "thresholds": {"legacy": {"silence_rms": SILENCE_RMS,
                                         "barge": SILENCE_RMS * LEGACY_BARGE_MULT, "debounce": 1},
                              "target": {"floor_min": FLOOR_MIN, "floor_mult": FLOOR_MULT,
                                         "barge_mult": BARGE_MULT, "barge_min": BARGE_MIN,
                                         "debounce": BARGE_FRAMES}},
               "rms_consistency_worklet_over_theory": cons, "results": results}
    (out_dir / "asr_endpoint_calibration.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {out_dir / 'asr_endpoint_calibration.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
