"""定位口型链路瓶颈：服务端计算 vs 隧道传输。

对比三个量：
  - 服务端自报 gen_ms / blend_ms / total_ms（纯计算）
  - 客户端墙钟（含 base64 + SSH 隧道）
  - 差值 = 传输/编解码开销
"""
import base64
import json
import sys
import time

import requests
import soundfile as sf  # noqa: F401  # 若不可用改 librosa

WAV = sys.argv[1] if len(sys.argv) > 1 else "../../data/testset/audio/t1-01.wav"
URL = "http://127.0.0.1:8002/api/v1/lip/infer"

data, sr = sf.read(WAV, dtype="int16")
if data.ndim > 1:
    data = data[:, 0]
pcm16 = data.tobytes()
dur = len(data) / sr
print(f"音频 {WAV}  {dur:.2f}s  sr={sr}  PCM16={len(pcm16)}B")

body = {"audio_pcm_16k_b64": base64.b64encode(pcm16).decode(), "fps": 25}
t0 = time.time()
r = requests.post(URL, json=body, timeout=600)
t_headers = time.time() - t0
raw = r.content
t_full = time.time() - t0
d = json.loads(raw)
st = d["stats"]

payload_b64_bytes = sum(len(f["frame_b64"]) for f in d["frames"])
print(f"\nHTTP {r.status_code}")
print(f"  服务端自报 : audio {st['audio_ms']}ms  gen {st['gen_ms']}ms ({st['gen_fps']}fps)  "
      f"blend {st['blend_ms']}ms ({st['blend_fps']}fps)  total {st['total_ms']}ms  RTF {st['rtf']}")
print(f"  客户端墙钟 : 首字节 {t_headers * 1000:.0f}ms  全部收完 {t_full * 1000:.0f}ms")
print(f"  响应体     : {len(raw) / 1024 / 1024:.2f} MB（base64 帧 {payload_b64_bytes / 1024 / 1024:.2f} MB）")
print(f"  帧数/显存  : {d['n_frames']} 帧 / {d['gpu_memory_mb']} MB")
transf = (t_full - t_headers) * 1000
print(f"\n  传输+解码开销 ≈ {transf:.0f}ms  → {len(raw) / 1024 / 1024 / max(transf / 1000, 0.001):.2f} MB/s")
print(f"  计算占比 {(st['total_ms'] / 1000) / t_full * 100:.0f}%   传输占比 {transf / (t_full * 1000) * 100:.0f}%")
