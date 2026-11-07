"""编译产物的内容寻址 checkpoint：批结果跨导入复用，中断 / 重启可续跑。

与 `llm.py` 的 `llm_cache` 不是一回事，也不互相替代：

* `llm_cache` 是「单次请求 → 原始返回文本」，可被 `cache_enabled` 关闭、被
  `clear_cache` 清空、被 `prune_cache` 按 mtime 淘汰，且没有「哪些批已完成」
  的概念，没法回答「上次编译跑到第几批」。
* 这里按**材料指纹**组织，存的是已解析的批单元 + 大纲 + 整轮最终结果，
  独立于模型缓存开关、可枚举、可原子标记 complete。

命中批缓存时连 `chat_json` / `parse_json_loose` 都不走；命中结果缓存时连
切分、大纲、质检都不走。只有 `services.compile_preview` 显式传入指纹才启用，
直接调 `compiler.compile_material` 的路径行为完全不变。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from . import config
from .utils import now_iso, read_json, write_json_atomic

CACHE_VERSION = 1
DIR_NAME = "compile_cache"
MAX_ENTRIES = 48
MAX_AGE_SECONDS = 7 * 24 * 3600


def digest(value: Any) -> str:
    """稳定序列化后取 sha256；键顺序、空白、Unicode 转义都不影响结果。"""
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def material_fingerprint(payload: dict[str, Any]) -> str:
    """材料级指纹。调用方必须把所有影响编译产物的输入都放进 payload。"""
    return digest({"v": CACHE_VERSION, "kind": "material", **payload})[:32]


def batch_fingerprint(payload: dict[str, Any]) -> str:
    """批级指纹：等价于该批实际发出的完整请求。"""
    return digest({"v": CACHE_VERSION, "kind": "batch", **payload})[:32]


def cache_dir() -> Path:
    return config.DATA_DIR / DIR_NAME


def _path(fp: str) -> Path:
    return cache_dir() / f"{fp}.json"


def new_state(fp: str, **meta: Any) -> dict[str, Any]:
    stamp = now_iso()
    return {"v": CACHE_VERSION, "material": fp, "created_at": stamp,
            "updated_at": stamp, "meta": meta, "outline": None,
            "content_digest": None, "batches": {}, "result": None,
            "complete": False}


def load(fp: str) -> dict[str, Any] | None:
    """读回 checkpoint；版本不符、结构损坏、材料指纹不匹配一律当未命中。"""
    data = read_json(_path(fp), None)
    if not (isinstance(data, dict) and data.get("v") == CACHE_VERSION
            and data.get("material") == fp
            and isinstance(data.get("batches"), dict)):
        return None
    return data


def save(fp: str, state: dict[str, Any]) -> None:
    """原子写。缓存写失败绝不能让编译失败。"""
    state["updated_at"] = now_iso()
    try:
        write_json_atomic(_path(fp), state, mode=0o600)
        _prune()
    except OSError:
        pass


def result_of(fp: str, content_digest: str | None = None) -> dict[str, Any] | None:
    """整轮结果复用：材料摘要必须一致，防止同名不同内容的材料拿到旧包。"""
    state = load(fp)
    if not (state and state.get("complete") and isinstance(state.get("result"), dict)):
        return None
    if content_digest is not None and state.get("content_digest") != content_digest:
        return None
    return state["result"]


def mark_complete(fp: str, result: dict[str, Any],
                  content_digest: str | None = None) -> None:
    state = load(fp) or new_state(fp)
    state["result"] = result
    state["complete"] = True
    state["content_digest"] = content_digest
    save(fp, state)


def batch_units(state: dict[str, Any] | None, key: str) -> list[dict[str, Any]] | None:
    if not state:
        return None
    units = state["batches"].get(key)
    return units if isinstance(units, list) else None


def drop(fp: str) -> None:
    try:
        os.remove(_path(fp))
    except OSError:
        pass


def clear() -> None:
    """只清编译 checkpoint，不动 llm_cache / packs（测试与手动重置用）。"""
    directory = cache_dir()
    if not directory.exists():
        return
    for item in directory.glob("*.json"):
        try:
            os.remove(item)
        except OSError:
            pass


def entries() -> list[str]:
    directory = cache_dir()
    return sorted(p.stem for p in directory.glob("*.json")) if directory.exists() else []


def _prune() -> None:
    """按修改时间淘汰，避免导入很多材料后 checkpoint 无限堆积。"""
    directory = cache_dir()
    if not directory.exists():
        return
    files = sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    cutoff = time.time() - MAX_AGE_SECONDS
    for item in files:
        if len(files) > MAX_ENTRIES or item.stat().st_mtime < cutoff:
            try:
                os.remove(item)
            except OSError:
                pass
