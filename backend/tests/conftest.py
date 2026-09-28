"""pytest 全局夹具与临时目录隔离。

**为什么需要这个文件（不是仪式）**：口型编码相关模块（`deploy/lip_encode.py`、
`eval/fake_lip_server.py`）要把 MP4 产物落盘，因此依赖临时目录。而系统 temp
在两类真实环境里不可用：

  1. 本机受限/沙箱化运行时 —— 系统 temp 整块不可写，且**随机后缀目录**
     （`mkdtemp`）会被直接拒绝，只有**固定路径**才放行；
  2. 云 GPU 的系统盘通常很小，数据盘才挂在 `/root/autodl-tmp` 这类大分区。

于是这里在**测试进程内**把 `LIP_TMPDIR` 指到仓库内的 `backend/.tmp-lip/`，
并预先建好各模块要用的**固定子目录**（`lipencwork` / `lipprobework` /
`fakesegwork`，命名与 `lip_encode._tempdir` 的约定一致）。
配合 .gitignore 的 `.tmp-*/` 规则：随用随建、不进库、跑完清空。

副作用：测试能否跑不再取决于宿主机的 temp 权限。
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

# backend/ 目录（本文件位于 backend/tests/）
BACKEND_DIR = Path(__file__).resolve().parent.parent
TMP_ROOT = BACKEND_DIR / ".tmp-lip"

# 与各模块 `_tempdir(prefix)` 的固定目录命名约定保持同步：
#   lip_encode._tempdir("lipenc_")   -> lipencwork
#   lip_encode._tempdir("lipprobe_") -> lipprobework
#   fake_lip_server segment_for()    -> fakesegwork
FIXED_WORK_DIRS = ("lipencwork", "lipprobework", "fakesegwork")


def pytest_configure(config: pytest.Config) -> None:
    """pytest 启动时准备好固定工作目录，并导出 LIP_TMPDIR。"""
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    for name in FIXED_WORK_DIRS:
        (TMP_ROOT / name).mkdir(exist_ok=True)
    os.environ["LIP_TMPDIR"] = str(TMP_ROOT)


@pytest.fixture(scope="session", autouse=True)
def _clean_lip_tmpdir() -> None:
    """跑完清空工作目录内容（目录本身保留，供下次复用）。

    逐项删除而不是 rmtree 整个根：受限环境下删除偶发被拒时，
    只跳过删不掉的那一项，不让清理动作把测试结果带崩。
    """
    yield
    for name in FIXED_WORK_DIRS:
        work = TMP_ROOT / name
        if not work.is_dir():
            continue
        for child in work.iterdir():
            try:
                shutil.rmtree(child) if child.is_dir() else child.unlink()
            except OSError:
                # 清不掉就留着（已被 .gitignore 忽略），不掩盖真实的测试结果
                pass
