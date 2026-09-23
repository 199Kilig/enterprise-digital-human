"""MuseTalk 口型推理服务（SPEC §2.1：POST /api/v1/lip/infer）。

部署位置：AutoDL 实例 `/root/autodl-tmp/digital-human/MuseTalk/lip_service.py`（需在 MuseTalk 仓库根目录运行）
启动：
  /root/miniconda3/bin/python -m uvicorn lip_service:app --host 0.0.0.0 --port 8002
自检（不起 HTTP，直接跑一段音频并落 mp4）：
  /root/miniconda3/bin/python lip_service.py --selftest data/audio/yongen.wav --out /tmp/selftest.mp4

设计要点（与官方 scripts/realtime_inference.py 的差异，都是有意的）：
1. **常驻内存**：模型与 avatar 预处理结果（coords/latents/frames/masks）只加载一次，
   请求间复用。官方脚本每次进程启动都重新加载。
2. **不落盘**：官方按帧写 PNG 再 ffmpeg 合成（V-01 实测 19.07 fps，含 I/O）；
   本服务直接在内存里做融合并 JPEG 编码返回，去掉整条磁盘链路。
3. **音频走内存**：官方 AudioProcessor.get_audio_feature 以文件路径为输入；
   此处直接吃 16k float32 数组，省掉临时 wav 读写。
4. 预处理（人脸检测/landmark/VAE 编码）不在请求路径上——用 results/avatars/{id} 的缓存，
   与官方 `preparation: False` 分支等价。
"""
from __future__ import annotations

import argparse
import base64
import os
import pickle
import sys
import time
from typing import List, Optional

import cv2
import numpy as np
import torch

MUSETALK_ROOT = os.path.dirname(os.path.abspath(__file__))
if MUSETALK_ROOT not in sys.path:
    sys.path.insert(0, MUSETALK_ROOT)

from musetalk.utils.audio_processor import AudioProcessor  # noqa: E402
from musetalk.utils.preprocessing import read_imgs  # noqa: E402
from musetalk.utils.utils import datagen, load_all_model  # noqa: E402

from lip_encode import EncodeError, encode_frames_to_mp4  # noqa: E402

SAMPLE_RATE = 16000
FPS_DEFAULT = 25
# 传输载体默认值（ADR-005）：h264 = 整句 H.264 片段；frames = 逐帧 JPEG（P1 兼容，A/B 对照用）
TRANSPORT_DEFAULT = os.environ.get("LIP_TRANSPORT", "h264")


def _to_luma(mask: np.ndarray) -> np.ndarray:
    """等价 PIL `Image.convert("L")`：3 通道 → 单通道。

    官方 blending.py 的 get_image_blending 里有这一步；云端 mask PNG 以 3 通道存盘，
    实测三通道取值相同（E3），故取首通道即可。
    """
    if mask.ndim == 2:
        return mask
    if mask.ndim == 3 and mask.shape[2] == 3:
        if (np.array_equal(mask[:, :, 0], mask[:, :, 1])
                and np.array_equal(mask[:, :, 0], mask[:, :, 2])):
            return mask[:, :, 0]
        return (0.299 * mask[:, :, 0] + 0.587 * mask[:, :, 1]
                + 0.114 * mask[:, :, 2]).astype(np.uint8)
    raise ValueError(f"不支持的 mask 形状: {mask.shape}")


def blend_frame(ori: np.ndarray, face_up: np.ndarray, bbox, mask: np.ndarray,
                crop_box) -> np.ndarray:
    """把生成脸融合回原图（等价官方 `get_image_blending`，但不用 PIL）。

    **为什么重写**（2026-09-23 实测 E4）：官方实现每帧做 2 次 numpy↔PIL 全图复制
    （`Image.fromarray(image[:, :, ::-1])` 的负步长切片强制拷贝 2.5MB）、固定 mask 每帧
    `convert("L")`、以及 PIL 带 mask 的 paste（Python 层）。纯 numpy 重写后：
      PIL 413ms/25帧 (16.5ms/帧) → numpy 116ms/25帧 (4.65ms/帧) = **3.6x**
      且与官方实现**逐像素完全一致**（maxdiff=0）。
    另测 torch GPU 版只快 1.0x —— 瓶颈是每帧 ~5.7MB 的 H2D/D2H 传输而非计算，
    且融合结果最终要回 CPU 交给 ffmpeg，故此处刻意不碰显卡。

    PIL 语义：body.paste(face_large, crop_box[:2], mask_image)
            → 区域内 out = merged*a + base*(1-a)，a = mask/255
    """
    x, y, x1, y1 = bbox
    x_s, y_s, x_e, y_e = crop_box
    mask_l = _to_luma(mask)
    if mask_l.shape != (y_e - y_s, x_e - x_s):
        raise ValueError(f"mask 形状 {mask_l.shape} 与 crop_box 区域 "
                         f"{(y_e - y_s, x_e - x_s)} 不符")

    a = mask_l[..., None].astype(np.float32) / 255.0
    out = ori.copy()
    region = out[y_s:y_e, x_s:x_e]                      # 视图
    merged = region.copy()
    merged[y - y_s:y1 - y_s, x - x_s:x1 - x_s] = face_up
    # RHS 的 region 此时仍是原值，与 PIL 的 body*(1-a) + face_large*a 一致
    out[y_s:y_e, x_s:x_e] = np.rint(merged * a + region * (1.0 - a)).astype(np.uint8)
    return out


class LipEngine:
    """常驻的 MuseTalk 推理引擎（单 avatar，P1 语义见 SPEC §2.1）。"""

    def __init__(
        self,
        unet_model_path: str = "./models/musetalk/pytorch_model.bin",
        unet_config: str = "./models/musetalk/musetalk.json",
        vae_type: str = "sd-vae",
        whisper_dir: str = "./models/whisper",
        avatar_id: str = "avator_1",
        version: str = "v1",
        batch_size: int = 20,
        gpu_id: int = 0,
    ) -> None:
        self.device = torch.device(f"cuda:{gpu_id}" if torch.cuda.is_available() else "cpu")
        self.batch_size = batch_size
        self.version = version
        # v1 的 avatar 缓存在 results/avatars/ 下（v15 在 results/v15/avatars/）
        self.avatar_root = (
            f"./results/{version}/avatars/{avatar_id}" if version != "v1" else f"./results/avatars/{avatar_id}"
        )
        self.avatar_id = avatar_id

        t0 = time.time()
        self.vae, self.unet, self.pe = load_all_model(
            unet_model_path=unet_model_path, vae_type=vae_type, unet_config=unet_config, device=self.device
        )
        self.timesteps = torch.tensor([0], device=self.device)
        # 半精度（官方 realtime_inference.py 同款；V-01 验证过的路径）
        self.pe = self.pe.half().to(self.device)
        self.vae.vae = self.vae.vae.half().to(self.device)
        self.unet.model = self.unet.model.half().to(self.device)

        self.audio_processor = AudioProcessor(feature_extractor_path=whisper_dir)
        self.weight_dtype = self.unet.model.dtype
        from transformers import WhisperModel

        self.whisper = WhisperModel.from_pretrained(whisper_dir)
        self.whisper = self.whisper.to(device=self.device, dtype=self.weight_dtype).eval()
        self.whisper.requires_grad_(False)
        print(f"[lip] 模型加载完成 {time.time() - t0:.1f}s device={self.device}", flush=True)

        self._load_avatar()

    # ---------- avatar（预处理结果常驻） ----------
    def _load_avatar(self) -> None:
        t0 = time.time()
        base = self.avatar_root
        if not os.path.isdir(base):
            raise RuntimeError(f"avatar 缓存不存在: {base}（需先在云端跑一次 preparation）")

        self.input_latent_list_cycle = torch.load(os.path.join(base, "latents.pt"), map_location="cpu")
        with open(os.path.join(base, "coords.pkl"), "rb") as f:
            self.coord_list_cycle = pickle.load(f)
        img_paths = [
            os.path.join(base, "full_imgs", p) for p in sorted(os.listdir(os.path.join(base, "full_imgs")))
        ]
        self.frame_list_cycle = read_imgs(img_paths)
        with open(os.path.join(base, "mask_coords.pkl"), "rb") as f:
            self.mask_coords_list_cycle = pickle.load(f)
        mask_paths = [os.path.join(base, "mask", p) for p in sorted(os.listdir(os.path.join(base, "mask")))]
        self.mask_list_cycle = read_imgs(mask_paths)
        print(
            f"[lip] avatar '{self.avatar_id}' 载入完成 {time.time() - t0:.1f}s "
            f"frames={len(self.frame_list_cycle)} latents={len(self.input_latent_list_cycle)}",
            flush=True,
        )

    # ---------- 热路径 ----------
    def _audio_features(self, pcm: np.ndarray) -> tuple[list, int]:
        """等价于 AudioProcessor.get_audio_feature，但直接吃内存里的 float32（省磁盘往返）。"""
        segment_length = 30 * SAMPLE_RATE
        features = []
        for i in range(0, len(pcm), segment_length):
            segment = pcm[i : i + segment_length]
            af = self.audio_processor.feature_extractor(
                segment, return_tensors="pt", sampling_rate=SAMPLE_RATE
            ).input_features
            if self.weight_dtype is not None:
                af = af.to(dtype=self.weight_dtype)
            features.append(af)
        return features, len(pcm)

    @torch.no_grad()
    def _recon_frames(self, pcm_float32: np.ndarray, fps: int) -> tuple[List[np.ndarray], dict]:
        """阶段一：音频 → 256×256 生成帧（GPU）。返回 (recon_frames, 统计)。"""
        t_audio = time.time()
        features, length = self._audio_features(pcm_float32)
        whisper_chunks = self.audio_processor.get_whisper_chunk(
            features,
            self.device,
            self.weight_dtype,
            self.whisper,
            length,
            fps=fps,
            audio_padding_length_left=2,
            audio_padding_length_right=2,
        )
        audio_ms = (time.time() - t_audio) * 1000
        video_num = len(whisper_chunks)

        # ⚠️ 实测结论（2026-09-14，见 RUNBOOK 坑 15）：把融合放进线程与 GPU 重叠**反而更慢**——
        #    线程争用使 GPU 侧 51.4fps → 23.5fps，端到端 7.8s → 9.4s。故此处保持串行。
        t_gen = time.time()
        gen = datagen(whisper_chunks, self.input_latent_list_cycle, self.batch_size)
        recon_frames: List[np.ndarray] = []
        for whisper_batch, latent_batch in gen:
            audio_feature_batch = self.pe(whisper_batch.to(self.device))
            latent_batch = latent_batch.to(device=self.device, dtype=self.unet.model.dtype)
            pred_latents = self.unet.model(
                latent_batch, self.timesteps, encoder_hidden_states=audio_feature_batch
            ).sample
            pred_latents = pred_latents.to(device=self.device, dtype=self.vae.vae.dtype)
            recon_frames.extend(self.vae.decode_latents(pred_latents))
        gen_ms = (time.time() - t_gen) * 1000
        return recon_frames, {
            "audio_ms": round(audio_ms, 1),
            "gen_ms": round(gen_ms, 1),
            "gen_fps": round(video_num / (gen_ms / 1000), 1) if gen_ms > 0 else 0.0,
            "video_num": video_num,
        }

    def _blended_iter(self, recon_frames: List[np.ndarray], fps: int):
        """阶段二：逐帧融合回原分辨率（CPU）。

        **生成器**：704×1216 每帧 2.5MB，200 帧=514MB，不能先 list 再编码（ADR-005 内存约束）。
        调用方边取边编码/边编码 JPEG。
        """
        for idx, res_frame in enumerate(recon_frames):
            bbox = self.coord_list_cycle[idx % len(self.coord_list_cycle)]
            ori_frame = self.frame_list_cycle[idx % len(self.frame_list_cycle)]
            x1, y1, x2, y2 = bbox
            try:
                res_up = cv2.resize(res_frame.astype(np.uint8), (x2 - x1, y2 - y1))
            except Exception:  # noqa: BLE001 — 与原实现一致：异常帧跳过
                continue
            mask = self.mask_list_cycle[idx % len(self.mask_list_cycle)]
            mask_box = self.mask_coords_list_cycle[idx % len(self.mask_coords_list_cycle)]
            # 官方 get_image_blending 的 numpy 等价实现（E4 实测：3.6x 且 maxdiff=0）
            yield idx, blend_frame(ori_frame, res_up, bbox, mask, mask_box)

    def _stats(self, base: dict, blend_ms: float, n_out: int, fps: int,
               total_ms: float, audio_s: float) -> dict:
        stats = dict(base)
        stats.update({
            "blend_ms": round(blend_ms, 1),
            "blend_fps": round(n_out / (blend_ms / 1000), 1) if blend_ms > 0 else 0.0,
            "frames_out": n_out,
            "total_ms": round(total_ms, 1),
            "audio_s": round(audio_s, 3),
        })
        stats["rtf"] = round((total_ms / 1000) / audio_s, 3) if audio_s else 0.0
        return stats

    @torch.no_grad()
    def infer_jpeg(
        self, pcm_float32: np.ndarray, fps: int = FPS_DEFAULT, jpeg_quality: int = 80
    ) -> tuple[List[tuple[int, float, bytes]], dict]:
        """音频 → [(frame_idx, pts_ms, jpeg_bytes)]（`transport=frames` 兼容路径）。"""
        t0 = time.time()
        recon_frames, base = self._recon_frames(pcm_float32, fps)
        video_num = base["video_num"]

        t_blend = time.time()
        results: List[tuple[int, float, bytes]] = []
        for idx, merged in self._blended_iter(recon_frames, fps):
            ok, buf = cv2.imencode(".jpg", merged, [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality])
            if ok:
                results.append((idx, idx * 1000.0 / fps, buf.tobytes()))
        blend_ms = (time.time() - t_blend) * 1000

        results.sort(key=lambda r: r[0])
        total_ms = (time.time() - t0) * 1000
        stats = self._stats(base, blend_ms, len(results), fps, total_ms, len(pcm_float32) / SAMPLE_RATE)
        print(
            f"[lip] transport=frames frames={video_num} audio={base['audio_ms']}ms "
            f"gen={base['gen_ms']}ms gen_fps={base['gen_fps']} total={total_ms:.0f}ms rtf={stats['rtf']}",
            flush=True,
        )
        return results, stats

    @torch.no_grad()
    def infer_video(
        self, pcm_float32: np.ndarray, fps: int = FPS_DEFAULT, crf: int = 26, preset: str = "veryfast"
    ) -> tuple[bytes, dict]:
        """音频 → MP4(H.264) 字节（ADR-005 默认路径）。

        帧以**生成器**形式喂给编码器，内存恒定（见 ADR-005 / lip_encode 注释）。
        """
        t0 = time.time()
        recon_frames, base = self._recon_frames(pcm_float32, fps)

        t_blend = time.time()
        n_out = 0

        def blended():
            nonlocal n_out
            for _idx, merged in self._blended_iter(recon_frames, fps):
                n_out += 1
                yield merged

        mp4 = encode_frames_to_mp4(blended(), fps=fps, crf=crf, preset=preset)
        blend_ms = (time.time() - t_blend) * 1000
        total_ms = (time.time() - t0) * 1000
        stats = self._stats(base, blend_ms, n_out, fps, total_ms, len(pcm_float32) / SAMPLE_RATE)
        stats["mp4_bytes"] = len(mp4)
        stats["crf"] = crf
        print(
            f"[lip] transport=h264 frames={n_out} mp4={len(mp4) / 1024:.0f}KB crf={crf} "
            f"audio={base['audio_ms']}ms gen={base['gen_ms']}ms({base['gen_fps']}fps) "
            f"total={total_ms:.0f}ms rtf={stats['rtf']}",
            flush=True,
        )
        return mp4, stats

    def infer(self, pcm_float32: np.ndarray, fps: int = FPS_DEFAULT) -> tuple[List[np.ndarray], float]:
        """兼容接口：返回原始帧（自检/调试用，不做 JPEG）。"""
        recon_frames, base = self._recon_frames(pcm_float32, fps)
        frames = [merged for _idx, merged in self._blended_iter(recon_frames, fps)]
        return frames, float(base["gen_fps"])


# ---------------- FastAPI 层（SPEC §2.1） ----------------
_engine: Optional[LipEngine] = None


def _engine_args() -> dict:
    return {
        "unet_model_path": os.environ.get("LIP_UNET", "./models/musetalk/pytorch_model.bin"),
        "unet_config": os.environ.get("LIP_UNET_CFG", "./models/musetalk/musetalk.json"),
        "vae_type": os.environ.get("LIP_VAE", "sd-vae"),
        "whisper_dir": os.environ.get("LIP_WHISPER", "./models/whisper"),
        "avatar_id": os.environ.get("LIP_AVATAR", "avator_1"),
        "version": os.environ.get("LIP_VERSION", "v1"),
        "batch_size": int(os.environ.get("LIP_BATCH", "20")),
    }


def get_engine() -> LipEngine:
    global _engine
    if _engine is None:
        _engine = LipEngine(**_engine_args())
    return _engine


try:
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel

    class LipInferRequest(BaseModel):
        audio_pcm_16k_b64: str
        avatar_frames_b64: List[str] = []
        fps: int = FPS_DEFAULT
        session_id: Optional[str] = None
        transport: Optional[str] = None   # h264（默认，ADR-005）| frames（P1 兼容）
        crf: int = 26                     # H.264 质量，越大越小越糊

    app = FastAPI(title="MuseTalk lip service (SPEC §2.1)")

    @app.on_event("startup")
    def _warmup() -> None:
        get_engine()  # 启动即加载，避免首个请求等模型

    @app.get("/health")
    def health() -> dict:
        eng = get_engine()
        free, total = torch.cuda.mem_get_info() if torch.cuda.is_available() else (0, 0)
        return {
            "status": "ok",
            "avatar": eng.avatar_id,
            "version": eng.version,
            "device": str(eng.device),
            "transport_default": TRANSPORT_DEFAULT,
            "gpu_free_mb": int(free // (1024 * 1024)),
            "gpu_total_mb": int(total // (1024 * 1024)),
            "frames_cached": len(eng.frame_list_cycle),
        }

    @app.post("/api/v1/lip/infer")
    def lip_infer(req: LipInferRequest) -> dict:
        try:
            pcm_bytes = base64.b64decode(req.audio_pcm_16k_b64)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail={"code": "LIP_ERROR", "message": f"音频解码失败: {exc}"}) from exc
        if not pcm_bytes:
            raise HTTPException(status_code=400, detail={"code": "LIP_ERROR", "message": "音频为空"})

        transport = (req.transport or TRANSPORT_DEFAULT).lower()
        if transport not in ("h264", "frames"):
            raise HTTPException(status_code=400, detail={"code": "LIP_ERROR", "message": f"未知 transport: {transport}"})

        pcm = np.frombuffer(pcm_bytes, dtype="<i2").astype(np.float32) / 32768.0
        eng = get_engine()
        t0 = time.time()
        try:
            if transport == "h264":
                mp4, stats = eng.infer_video(pcm, fps=req.fps, crf=req.crf)
            else:
                payload_raw, stats = eng.infer_jpeg(pcm, fps=req.fps)
                mp4 = None
        except (EncodeError, torch.cuda.OutOfMemoryError) as exc:
            torch.cuda.empty_cache()
            code = "LIP_OOM" if isinstance(exc, torch.cuda.OutOfMemoryError) else "LIP_ERROR"
            raise HTTPException(status_code=503, detail={"code": code, "message": str(exc)[:300]}) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail={"code": "LIP_ERROR", "message": f"{type(exc).__name__}: {exc}"[:300]}) from exc

        gpu_mb = int(torch.cuda.max_memory_allocated() // (1024 * 1024)) if torch.cuda.is_available() else 0
        n_frames = int(stats.get("frames_out", stats.get("video_num", 0)))

        if transport == "h264":
            body = {
                "transport": "h264",
                "video_b64": base64.b64encode(mp4).decode("ascii"),
                "duration_ms": int(round(n_frames * 1000.0 / req.fps)),
                "n_frames": n_frames,
                "fps": req.fps,
                "gpu_memory_mb": gpu_mb,
                "infer_fps": stats["gen_fps"],
                "wall_ms": int((time.time() - t0) * 1000),
                "stats": stats,
            }
        else:
            frames = [
                {"frame_b64": base64.b64encode(jpg).decode("ascii"), "pts_ms": int(pts), "frame_idx": idx}
                for idx, pts, jpg in payload_raw
            ]
            body = {
                "transport": "frames",
                "frames": frames,
                "n_frames": len(frames),
                "fps": req.fps,
                "gpu_memory_mb": gpu_mb,
                "infer_fps": stats["gen_fps"],
                "wall_ms": int((time.time() - t0) * 1000),
                "stats": stats,
            }
        return body

except ImportError:  # 自检模式无需 fastapi
    app = None  # type: ignore[assignment]


def _selftest(wav: str, out: str, fps: int, transport: str) -> int:
    import librosa

    eng = get_engine()
    pcm, sr = librosa.load(wav, sr=SAMPLE_RATE)
    assert sr == SAMPLE_RATE, sr
    audio_s = len(pcm) / SAMPLE_RATE

    if transport == "h264":
        t0 = time.time()
        mp4, stats = eng.infer_video(pcm, fps=fps)
        wall = time.time() - t0
        print(f"\n自检(transport=h264)：音频 {audio_s:.2f}s → {stats['frames_out']} 帧 → MP4 {len(mp4) / 1024:.0f} KB")
        print(
            f"  端到端 {wall * 1000:.0f}ms | 音频特征 {stats['audio_ms']}ms | GPU 生成 {stats['gen_ms']}ms "
            f"({stats['gen_fps']} fps) | 融合+编码 {stats['blend_ms']:.0f}ms | RTF {wall / audio_s:.3f}"
        )
        if out:
            with open(out, "wb") as fh:
                fh.write(mp4)
            print(f"  已写 {out}")
        return 0

    t0 = time.time()
    payload, stats = eng.infer_jpeg(pcm, fps=fps)
    wall = time.time() - t0
    print(f"\n自检(transport=frames)：音频 {audio_s:.2f}s → JPEG 帧 {len(payload)} 张")
    print(
        f"  端到端 {wall * 1000:.0f}ms | 音频特征 {stats['audio_ms']}ms | GPU 生成 {stats['gen_ms']}ms "
        f"({stats['gen_fps']} fps) | RTF {wall / audio_s:.3f}"
    )

    if payload and out:
        first = cv2.imdecode(np.frombuffer(payload[0][2], dtype=np.uint8), cv2.IMREAD_COLOR)
        h, w = first.shape[:2]
        print(f"  输出分辨率 {w}x{h}")
        writer = cv2.VideoWriter(out, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
        for _idx, _pts, jpg in payload:
            writer.write(cv2.imdecode(np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR))
        writer.release()
        print(f"  已写 {out}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", help="直接跑一段 wav（不起 HTTP）")
    ap.add_argument("--out", default="")
    ap.add_argument("--fps", type=int, default=FPS_DEFAULT)
    ap.add_argument("--transport", default=TRANSPORT_DEFAULT, choices=["h264", "frames"])
    a = ap.parse_args()
    if a.selftest:
        out = a.out or (f"/tmp/selftest_{a.transport}.mp4")
        raise SystemExit(_selftest(a.selftest, out, a.fps, a.transport))
    print("用法: python lip_service.py --selftest data/audio/yongen.wav [--transport h264|frames]")
