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

缓存版本不需要人工维护：`CACHE_VERSION` 由 `boil/**/*.py` 与 `prompts/**/*.md`
的内容算出，任何改动编译产物的代码 / 提示词变更都会让旧 checkpoint 自动失效。
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

# --- 缓存版本（自动推导）---------------------------------------------------
# 结构版本：只有 checkpoint 的字段格式改了才需要动这个数字。
_SCHEMA_VERSION = 1
# 扫描范围：能改变编译产物的后端代码与提示词。前端 / 测试 / 工具脚本刻意排除，
# 免得改个按钮文案就把整站编译缓存清空；清单本身也随源码一起进入 hash。
_VERSION_SOURCES = ((Path("boil"), "**/*.py"), (Path("prompts"), "**/*.md"))


def _auto_version() -> str:
    """把源码内容本身做成版本：相关文件一改，旧 checkpoint 自动失效。

    用文件内容而不是 mtime：同一份代码每次启动都得到同一个值，git checkout、
    重新部署、改文件时间戳都不会误失效。读不到的文件跳过，绝不让缓存逻辑
    把服务启动卡住。只在模块导入时算一次，之后 CACHE_VERSION 是常量。
    """
    h = hashlib.sha256()
    h.update(str(_SCHEMA_VERSION).encode("ascii"))
    for rel, pattern in _VERSION_SOURCES:
        root = config.ROOT / rel
        if not root.exists():
            continue
        for path in sorted(root.glob(pattern)):
            if "__pycache__" in path.parts or not path.is_file():
                continue
            h.update(str(path.relative_to(config.ROOT)).encode("utf-8"))
            try:
                h.update(path.read_bytes())
            except OSError:
                pass
            h.update(b"\0")
    # 可选盐：运维想不改代码就强制清缓存时使用，默认不设置
    salt = os.environ.get("BOIL_COMPILE_CACHE_VERSION", "").strip()
    if salt:
        h.update(salt.encode("utf-8"))
    return f"{_SCHEMA_VERSION}.{h.hexdigest()[:16]}"


CACHE_VERSION = _auto_version()
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
            "complete": False, "deepen": None}


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


def deepen_fingerprint(payload: dict[str, Any]) -> str:
    """一次按需深化的内容寻址键：等价于该次真正发出的请求。

    刻意不含 pack_id / unit_id：同一份材料重复导入、或 pack 重装后重新深化，
    只要骨架与取回的原文一致就应命中，不必为同一个知识点再烧一次模型。
    """
    return digest({"v": CACHE_VERSION, "kind": "deepen", **payload})[:32]


def deepen_unit_of(fp: str) -> dict[str, Any] | None:
    """读回深化结果。独立于 llm_cache：不受它开关 / 清空 / 按 mtime 淘汰影响。"""
    state = load(fp)
    unit = state.get("deepen") if state else None
    return unit if isinstance(unit, dict) else None


def mark_deepen(fp: str, unit: dict[str, Any]) -> None:
    state = load(fp) or new_state(fp)
    state["deepen"] = unit
    save(fp, state)


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
