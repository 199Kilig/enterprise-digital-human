"""MuseTalk 口型引擎（本地侧）——云 GPU 推理服务的客户端。

架构（ADR-001 双环境分工）：
    本机编排（本文件） ──HTTP──> 云 GPU lip 服务（backend/deploy/lip_service.py）
    POST {service_url}/api/v1/lip/infer

为什么是 HTTP 客户端而不是本地直接跑：MuseTalk 需要 GPU 常驻显存与 8.7G 权重，
本机 RTX 3050 放不下（ADR-001 已决策）。接口契约见 SPEC §2.1。

服务端返回：{frames: [{frame_b64, pts_ms, frame_idx}], gpu_memory_mb, infer_fps, stats}
本类转成 schemas.LipFrame（bytes 形式）交给上层。

⚠️ 实测（2026-09-14，见 RUNBOOK 坑 15）：8s 音频端到端 7.8s（RTF 0.97）、
   冷启动首请求更慢；**这是同步阻塞调用**，编排层必须按句并发/流水线，别串行阻塞播报。
"""
from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from typing import List, Optional

from schemas import LipFrame

from .engine import LipEngine


class LipServiceError(RuntimeError):
    """口型服务不可用/推理失败（上层据此降级到 mock 或提示）。"""


class MuseTalkLipEngine(LipEngine):
    """调用云 GPU 上的 MuseTalk 服务（SPEC §2.1）。

    transport（ADR-005）：
      - "h264"：整句 H.264 片段（默认）。返回 dict 含 `video`(bytes)/`duration_ms`/`n_frames`
      - "frames"：逐帧 JPEG（P1 兼容路径，A/B 对照用）。返回 dict 含 `frames`(List[LipFrame])
    返回值带 `transport` 字段，调用方据此决定发 `lip_video` 还是 `lip_frame` 事件。
    """

    def __init__(
        self,
        service_url: str = "http://127.0.0.1:8002",
        timeout_s: float = 120.0,
        transport: str = "h264",
        crf: int = 26,
    ) -> None:
        self.service_url = service_url.rstrip("/")
        self.timeout_s = timeout_s
        self.transport = transport
        self.crf = crf

    # --- 健康检查（供 /health 只读端点使用） ---
    def health(self, timeout_s: float = 2.0) -> Optional[dict]:
        try:
            with urllib.request.urlopen(f"{self.service_url}/health", timeout=timeout_s) as resp:  # noqa: S310
                return json.loads(resp.read().decode("utf-8"))
        except Exception:  # noqa: BLE001 — 健康检查失败即视为不可用
            return None

    def infer(
        self,
        audio_pcm_16k: bytes,
        avatar_frames_b64: List[str],
        fps: int,
        session_id: Optional[str] = None,
    ) -> dict:
        body = {
            "audio_pcm_16k_b64": base64.b64encode(audio_pcm_16k).decode("ascii"),
            "avatar_frames_b64": avatar_frames_b64 or [],
            "fps": fps,
            "session_id": session_id,
            "transport": self.transport,
            "crf": self.crf,
        }
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(  # noqa: S310
            f"{self.service_url}/api/v1/lip/infer",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:  # noqa: S310
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise LipServiceError(f"lip 服务返回 {exc.code}: {detail}") from exc
        except Exception as exc:  # noqa: BLE001 — 网络/超时统一成领域异常
            raise LipServiceError(f"lip 服务不可达: {type(exc).__name__}: {exc}") from exc

        wall_ms = int((time.time() - t0) * 1000)
        common = {
            "gpu_memory_mb": int(payload.get("gpu_memory_mb", 0)),
            "infer_fps": float(payload.get("infer_fps", 0.0)),
            "wall_ms": wall_ms,
            "stats": payload.get("stats", {}),
        }

        if payload.get("transport") == "h264" or "video_b64" in payload:
            return {
                "transport": "h264",
                "video": base64.b64decode(payload["video_b64"]),
                "duration_ms": int(payload.get("duration_ms", 0)),
                "n_frames": int(payload.get("n_frames", 0)),
                "fps": int(payload.get("fps", fps)),
                **common,
            }

        frames = [
            LipFrame(
                frame=base64.b64decode(f["frame_b64"]),
                pts_ms=int(f["pts_ms"]),
                frame_idx=int(f["frame_idx"]),
            )
            for f in payload.get("frames", [])
        ]
        return {"transport": "frames", "frames": frames, "n_frames": len(frames), "fps": fps, **common}


def build_lip_engine(cfg: dict | None = None) -> LipEngine:
    """按 config.yaml 的 lip 段构造引擎（mock / musetalk 二选一）。"""
    cfg = (cfg or {}).get("lip", {}) if cfg is not None else {}
    if cfg.get("mock", True):
        from .engine import MockLipEngine

        return MockLipEngine()
    return MuseTalkLipEngine(
        service_url=cfg.get("service_url", "http://127.0.0.1:8002"),
        transport=cfg.get("transport", "h264"),
        crf=int(cfg.get("crf", 26)),
    )
