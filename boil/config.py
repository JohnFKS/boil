"""Boil 全局配置。

所有路径都相对项目根目录；数据默认落在 data/，可用 BOIL_DATA_DIR 覆盖。
"""
from __future__ import annotations

import os
from pathlib import Path

VERSION = "1.4.2"
APP_NAME = "Boil"
APP_CN = "烧水"
DEFAULT_USER = "local"

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"
CONTENT_DIR = ROOT / "content"
PROMPTS_DIR = ROOT / "prompts"
DATA_DIR = Path(os.environ.get("BOIL_DATA_DIR", str(ROOT / "data"))).resolve()
PACKS_DIR = DATA_DIR / "packs"
DB_PATH = DATA_DIR / "boil.db"
LLM_CONFIG_PATH = DATA_DIR / "llm.json"
LLM_STATE_PATH = DATA_DIR / "llm_state.json"

HOST = os.environ.get("BOIL_HOST", "127.0.0.1")
PORT = int(os.environ.get("BOIL_PORT", "7860"))
TOKEN = os.environ.get("BOIL_TOKEN", "").strip()
LLM_OFF = os.environ.get("BOIL_LLM_OFF", "") == "1"
DEMO_USER_NAME = os.environ.get("BOIL_USER_NAME", "学习者")

MAX_BODY_BYTES = 12 * 1024 * 1024
HTTP_BACKLOG = int(os.environ.get("BOIL_BACKLOG", "128"))


def _bounded_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


# 单次编译内的批次并发数。Dots 单 Key 默认 60 RPM / 1,500,000 TPM，
# 每批一次请求时 8 并发仍在额度内；默认 4 留余量，可显式调到 8。
COMPILE_CONCURRENCY = _bounded_int("BOIL_COMPILE_CONCURRENCY", 4, 1, 8)


def ensure_dirs() -> None:
    for p in (DATA_DIR, PACKS_DIR):
        p.mkdir(parents=True, exist_ok=True)


def read_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return default
