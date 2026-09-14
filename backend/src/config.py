"""配置中心加载（DESIGN §4.4）+ 本地凭证注入。

规则（config.yaml 头部约定）：api_key 一律从环境变量读取（api_key_env 字段指定），不落盘。
本地开发把 key 写在 `backend/.env`（已被 .gitignore 覆盖），本模块负责注入 os.environ；
生产/CI 直接给环境变量即可，两种方式同一入口。
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

SRC_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SRC_DIR.parent
REPO_ROOT = BACKEND_DIR.parent
ENV_FILE = BACKEND_DIR / ".env"


@lru_cache(maxsize=1)
def load_config() -> dict:
    """读取 src/config.yaml（配置中心唯一入口）。"""
    path = SRC_DIR / "config.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_env_file(path: Path | None = None) -> None:
    """把 .env 中尚未存在的键注入环境变量（不覆盖已有值，故生产注入优先）。"""
    p = path or ENV_FILE
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def get_secret(env_name: str) -> str | None:
    """按 config.yaml 的 api_key_env 取密钥（先注入 .env，再读环境变量）。"""
    load_env_file()
    value = os.environ.get(env_name)
    return value or None
