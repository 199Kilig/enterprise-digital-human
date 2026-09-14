"""lip：口型驱动模块（MuseTalk 推理服务化，SPEC §2.1）

- engine.py：抽象接口 + MockLipEngine（无 GPU 联调）
- musetalk_engine.py：云 GPU 服务客户端（真实推理，ADR-001 双环境分工）
"""
from .engine import LipEngine, MockLipEngine  # noqa: F401
from .musetalk_engine import LipServiceError, MuseTalkLipEngine, build_lip_engine  # noqa: F401

__all__ = [
    "LipEngine",
    "MockLipEngine",
    "MuseTalkLipEngine",
    "LipServiceError",
    "build_lip_engine",
]
