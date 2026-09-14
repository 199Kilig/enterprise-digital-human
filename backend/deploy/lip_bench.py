"""口型服务性能拆解：GPU 生成 vs CPU 融合 各自多快，以及并行时的争用代价。

一次性回答三个问题（都在同一进程内跑，避免重复加载模型）：
  A. 纯 GPU 生成（含 VAE decode）多久
  B. 纯 CPU 融合（resize + get_image_blending）多久
  C. 把 B 放线程与 A 重叠 —— 是否变快（含 torch/cv2 线程数限制的对照）
"""
import os
import queue
import sys
import threading
import time

import cv2
import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lip_service import SAMPLE_RATE, LipEngine  # noqa: E402
from musetalk.utils.blending import get_image_blending  # noqa: E402
from musetalk.utils.utils import datagen  # noqa: E402

WAV = "data/audio/yongen.wav"
FPS = 25


def blend_one(eng: LipEngine, idx: int, res_frame: np.ndarray) -> np.ndarray:
    bbox = eng.coord_list_cycle[idx % len(eng.coord_list_cycle)]
    ori = eng.frame_list_cycle[idx % len(eng.frame_list_cycle)]
    x1, y1, x2, y2 = bbox
    up = cv2.resize(res_frame.astype(np.uint8), (x2 - x1, y2 - y1))
    mask = eng.mask_list_cycle[idx % len(eng.mask_list_cycle)]
    mbox = eng.mask_coords_list_cycle[idx % len(eng.mask_coords_list_cycle)]
    return get_image_blending(ori, up, bbox, mask, mbox)


def gen_frames(eng: LipEngine, pcm: np.ndarray):
    """纯 GPU 生成（与融合解耦），yield 每帧。"""
    features, length = eng._audio_features(pcm)
    chunks = eng.audio_processor.get_whisper_chunk(
        features, eng.device, eng.weight_dtype, eng.whisper, length,
        fps=FPS, audio_padding_length_left=2, audio_padding_length_right=2,
    )
    for whisper_batch, latent_batch in datagen(chunks, eng.input_latent_list_cycle, eng.batch_size):
        af = eng.pe(whisper_batch.to(eng.device))
        lb = latent_batch.to(device=eng.device, dtype=eng.unet.model.dtype)
        pred = eng.unet.model(lb, eng.timesteps, encoder_hidden_states=af).sample
        pred = pred.to(device=eng.device, dtype=eng.vae.vae.dtype)
        for f in eng.vae.decode_latents(pred):
            yield f


def main() -> None:
    import librosa

    t0 = time.time()
    eng = LipEngine()
    print(f"[bench] 引擎就绪 {time.time() - t0:.1f}s", flush=True)
    pcm, _ = librosa.load(WAV, sr=SAMPLE_RATE)

    # ---- A: 纯 GPU 生成 ----
    t = time.time()
    frames = list(gen_frames(eng, pcm))
    gen_s = time.time() - t
    n = len(frames)
    print(f"A 纯GPU生成   : {n} 帧 {gen_s * 1000:.0f}ms → {n / gen_s:.1f} fps", flush=True)

    # ---- B: 纯 CPU 融合 ----
    t = time.time()
    outs = [blend_one(eng, i, f) for i, f in enumerate(frames)]
    blend_s = time.time() - t
    print(f"B 纯CPU融合   : {n} 帧 {blend_s * 1000:.0f}ms → {n / blend_s:.1f} fps", flush=True)

    # ---- B2: 融合 + JPEG 编码 ----
    t = time.time()
    for o in outs:
        cv2.imencode(".jpg", o, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    jpg_s = time.time() - t
    print(f"B2 +JPEG编码  : {n} 帧 {jpg_s * 1000:.0f}ms → {n / jpg_s:.1f} fps", flush=True)

    # ---- C: 线程重叠（默认线程数） ----
    def run_threaded() -> dict:
        q: queue.Queue = queue.Queue(maxsize=16)
        done = []

        def consumer():
            while True:
                it = q.get()
                if it is None:
                    return
                idx, f = it
                done.append(blend_one(eng, idx, f))

        th = threading.Thread(target=consumer, daemon=True)
        th.start()
        t = time.time()
        produced = 0
        for f in gen_frames(eng, pcm):
            q.put((produced, f))
            produced += 1
        g_ms = (time.time() - t) * 1000
        q.put(None)
        th.join()
        return {"gen_ms": round(g_ms), "total_ms": round((time.time() - t) * 1000), "frames": len(done)}

    c1 = run_threaded()
    print(f"C 线程重叠(默认): gen {c1['gen_ms']}ms total {c1['total_ms']}ms ({c1['frames']} 帧)", flush=True)

    # ---- D: 限制 torch/cv2 线程数后再重叠（验证 CPU 线程争用假设） ----
    torch.set_num_threads(1)
    cv2.setNumThreads(1)
    c2 = run_threaded()
    print(f"D 线程重叠(1线程): gen {c2['gen_ms']}ms total {c2['total_ms']}ms ({c2['frames']} 帧)", flush=True)

    # ---- E: 串行基线（先全生成再融合），同进程对照 ----
    t = time.time()
    fs = list(gen_frames(eng, pcm))
    g = (time.time() - t) * 1000
    t = time.time()
    for i, f in enumerate(fs):
        blend_one(eng, i, f)
    b = (time.time() - t) * 1000
    print(f"E 串行总耗时   : gen {g:.0f}ms + blend {b:.0f}ms = {g + b:.0f}ms", flush=True)

    print(f"\n理论最优(完全并行) = max(gen, blend) = {max(gen_s, blend_s) * 1000:.0f}ms "
          f"→ {n / max(gen_s, blend_s):.1f} fps", flush=True)


if __name__ == "__main__":
    main()
