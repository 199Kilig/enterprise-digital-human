"""Lip 推理服务（SPEC §2.1）：POST /api/v1/lip/infer

部署（AutoDL 实例，有卡）:
  LIP_ENGINE=musetalk python -m uvicorn lip.server:app --host 0.0.0.0 --port 8002
本机联调（mock）:
  python -m uvicorn lip.server:app --port 8002
"""
from __future__ import annotations

import base64
import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from lip.engine import MockLipEngine, MuseTalkLipEngine


class LipInferRequest(BaseModel):
    audio_pcm_16k_b64: str  # 16kHz 单声道 PCM 的 base64（调用方保证已重采样）
    avatar_frames_b64: list[str] = []  # 形象帧（JPEG base64），P1 单张参考帧即可
    fps: int = 25  # 输出帧率，与 MuseTalk 训练口径一致
    session_id: str | None = None  # 显存/日志上下文


app = FastAPI(title="Lip 推理服务（SPEC §2.1）")

_engine = (
    MuseTalkLipEngine() if os.environ.get("LIP_ENGINE") == "musetalk" else MockLipEngine()
)


@app.post("/api/v1/lip/infer")
def lip_infer(req: LipInferRequest) -> dict:
    try:
        audio = base64.b64decode(req.audio_pcm_16k_b64)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail={"code": "LIP_ERROR", "message": "audio_pcm_16k_b64 解码失败"},
        )
    if not audio:
        raise HTTPException(
            status_code=400,
            detail={"code": "LIP_ERROR", "message": "音频为空"},
        )
    try:
        result = _engine.infer(audio, req.avatar_frames_b64, req.fps, req.session_id)
    except NotImplementedError:
        raise HTTPException(
            status_code=503,
            detail={"code": "LIP_ERROR", "message": "MuseTalk 引擎未实现（V-01 后落地）"},
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=503,
            detail={"code": "LIP_ERROR", "message": f"推理失败: {e}"},
        )
    return {
        "frames": [
            {
                "frame_b64": base64.b64encode(f.frame).decode("ascii"),
                "pts_ms": f.pts_ms,
                "frame_idx": f.frame_idx,
            }
            for f in result["frames"]
        ],
        "gpu_memory_mb": result["gpu_memory_mb"],
        "infer_fps": round(result["infer_fps"], 2),
    }
