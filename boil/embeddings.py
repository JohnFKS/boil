"""混合 Embedding 层：硅基流动/OpenAI 兼容 → 本地哈希向量降级。

借鉴 testlearnweb 的 emb.py：
- 没配 Key 时这层不存在，检索/分组照常可用；
- 批量请求、磁盘缓存、按 index 排序；
- 失败进冷却，不在一次检索里反复打云端；
- 最后一次错误可见，不静默降级；
- 手动 probe 跳过缓存，避免缓存替云端说谎。

本地 fallback 是字符/词哈希向量（零依赖、离线），不是神经语义模型。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable

from . import config
from .utils import jwt_like_mask, normalize_base_url, read_json, write_json_atomic

LOCAL_DIM = 256
LOCAL_MODEL = "local-hash-256"
DIM = LOCAL_DIM
MODEL = LOCAL_MODEL
BATCH = 32
EMBED_CHARS = 1200
CACHE_CAP = 2000
COOLDOWN = 60.0
CONFIG_PATH = config.DATA_DIR / "embedding.json"
CACHE_PATH = config.DATA_DIR / "embedding_cache.json"
_CONFIG_OVERRIDE: Path | None = None
_CACHE_OVERRIDE: Path | None = None


def _config_path() -> Path:
    return Path(_CONFIG_OVERRIDE) if _CONFIG_OVERRIDE else CONFIG_PATH


def _cache_path() -> Path:
    return Path(_CACHE_OVERRIDE) if _CACHE_OVERRIDE else CACHE_PATH

SUGGEST = {
    "base_url": "https://api.siliconflow.cn/v1",
    "model": "BAAI/bge-m3",
    "note": "硅基流动 BAAI/bge-m3（1024 维，中文效果好）。也可填任意 OpenAI 兼容 /embeddings；不配置时自动使用本地哈希向量。",
}
DEFAULT = {
    "base_url": SUGGEST["base_url"],
    "model": SUGGEST["model"],
    "api_key": "",
    "enabled": True,
    "dim": None,
    "timeout": 30,
}
STOP = set("的了和与及或是把被在对从中为以于而也都就还又很更最一个这那".split())

LAST_ERROR = ""
_STATS = {"calls": 0, "cached": 0, "texts": 0, "ms": 0.0, "tokens": 0}
_LAST_ENGINE = LOCAL_MODEL
_fail_until = 0.0
_cache_lock = threading.RLock()
_stats_lock = threading.RLock()


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def forced_off() -> bool:
    return _env("BOIL_EMBEDDING_OFF") == "1"


def get_config() -> dict[str, Any]:
    stored = _read(_config_path(), {})
    if not isinstance(stored, dict):
        stored = {}
    cfg = dict(DEFAULT)
    cfg.update({k: v for k, v in stored.items() if k in DEFAULT})
    # 环境变量优先，方便 Docker/CI 注入，不落盘
    if _env("BOIL_EMBEDDING_KEY"):
        cfg["api_key"] = _env("BOIL_EMBEDDING_KEY")
    if _env("BOIL_EMBEDDING_BASE_URL"):
        cfg["base_url"] = _env("BOIL_EMBEDDING_BASE_URL")
    if _env("BOIL_EMBEDDING_MODEL"):
        cfg["model"] = _env("BOIL_EMBEDDING_MODEL")
    if _env("BOIL_EMBEDDING_TIMEOUT").isdigit():
        cfg["timeout"] = int(_env("BOIL_EMBEDDING_TIMEOUT"))
    # 只有"从没配过"才回填建议地址；用户显式清空过就必须尊重空值 ——
    # 否则"我把地址栏清掉了"会变成继续往第三方发文本。
    if "base_url" in stored:
        cfg["base_url"] = str(stored.get("base_url") or "").strip().rstrip("/")
    else:
        cfg["base_url"] = str(cfg.get("base_url") or SUGGEST["base_url"]).rstrip("/")
    cfg["model"] = str(cfg.get("model") or SUGGEST["model"])
    cfg["api_key"] = str(cfg.get("api_key") or "").strip()
    cfg["enabled"] = bool(cfg.get("enabled", True))
    cfg["timeout"] = int(cfg.get("timeout") or 30)
    cfg["dim"] = int(cfg["dim"]) if str(cfg.get("dim") or "").isdigit() else None
    return cfg


def set_config(base_url: str | None = None, model: str | None = None,
               api_key: str | None = None, enabled: bool | None = None,
               timeout: int | None = None) -> dict[str, Any]:
    cur = _read(_config_path(), {})
    if not isinstance(cur, dict):
        cur = {}
    if base_url is not None:
        cur["base_url"] = normalize_base_url(base_url).rstrip("/")
    if model is not None:
        cur["model"] = str(model).strip()
    if api_key is not None:
        cur["api_key"] = str(api_key).strip()
    if enabled is not None:
        cur["enabled"] = bool(enabled)
    if timeout is not None:
        cur["timeout"] = int(timeout)
    write_json_atomic(_config_path(), cur, mode=0o600)
    global _fail_until
    _fail_until = 0.0
    return get_config()


def configured() -> bool:
    c = get_config()
    return bool(c["api_key"] and c["base_url"] and c["model"] and c["enabled"] and not forced_off())


def in_cooldown() -> bool:
    return time.time() < _fail_until


def enabled() -> bool:
    return configured() and not in_cooldown()


def active_model() -> str:
    c = get_config()
    return c["model"] if configured() else LOCAL_MODEL


def last_engine() -> str:
    return _LAST_ENGINE


def public_config() -> dict[str, Any]:
    c = get_config()
    key = c["api_key"]
    return {
        "mode": "cloud" if enabled() else ("fallback" if configured() else "local"),
        "configured": configured(),
        "enabled": enabled(),
        "base_url": c["base_url"],
        "model": c["model"],
        "api_key_set": bool(key),
        "api_key_masked": jwt_like_mask(key),
        "dim": c["dim"],
        "timeout": c["timeout"],
        "suggest": SUGGEST,
        "last_error": LAST_ERROR,
        "in_cooldown": in_cooldown(),
        "stats": dict(_STATS),
    }


def _key() -> str:
    return _env("BOIL_EMBEDDING_KEY") or get_config()["api_key"]


def _url() -> str:
    return get_config()["base_url"].rstrip("/") + "/embeddings"


def _post(payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        _url(),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {_key()}",
            "Content-Type": "application/json",
            "User-Agent": "Boil/1.0 (embedding)",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _cache() -> dict:
    data = _read(_cache_path(), {})
    return data if isinstance(data, dict) else {}


def _save_cache(data: dict) -> None:
    if len(data) > CACHE_CAP:
        keep = sorted(data.items(), key=lambda kv: kv[1].get("u", 0))[-CACHE_CAP:]
        data = dict(keep)
    write_json_atomic(_cache_path(), data, mode=0o600)


def _ck(model: str, text: str) -> str:
    return hashlib.sha1(f"{model}\x00{text}".encode("utf-8")).hexdigest()


def last_error() -> str:
    return LAST_ERROR


def _note_error(message: str) -> None:
    global LAST_ERROR, _fail_until, _LAST_ENGINE
    LAST_ERROR = message[:500]
    _fail_until = time.time() + COOLDOWN
    _LAST_ENGINE = LOCAL_MODEL
    print(f"[embed] {_url()} 失败：{LAST_ERROR}（冷却 {int(COOLDOWN)}s，降级本地向量）",
          file=sys.stderr)


def embed_texts(texts: list[str], fresh: bool = False) -> list[list[float]] | None:
    """公开的云端批量接口；不可用时返回 None，由调用方降级本地。"""
    return _remote_embed_texts(texts, fresh=fresh)


def _remote_embed_texts(texts: list[str], fresh: bool = False) -> list[list[float]] | None:
    global _LAST_ENGINE
    texts = [(t or "")[:EMBED_CHARS] for t in texts]
    if not texts or not enabled():
        return None
    c = get_config()
    model = c["model"]
    with _cache_lock:
        cache = _cache()
    out: list[list[float] | None] = [None] * len(texts)
    todo: dict[str, list[int]] = {}
    with _stats_lock:
        for i, text in enumerate(texts):
            key = _ck(model, text)
            item = cache.get(key)
            if item and not fresh:
                out[i] = item["v"]
                item["u"] = round(time.time(), 1)
                _STATS["cached"] += 1
            else:
                todo.setdefault(key, []).append(i)
    if todo:
        keys = list(todo)
        for start in range(0, len(keys), BATCH):
            chunk = keys[start:start + BATCH]
            payload = {
                "model": model,
                "input": [texts[todo[k][0]] for k in chunk],
                "encoding_format": "float",
            }
            t0 = time.time()
            try:
                data = _post(payload, float(c["timeout"] or 30))
            except urllib.error.HTTPError as exc:
                body = ""
                try:
                    body = exc.read().decode("utf-8", errors="replace")[:300]
                except Exception:
                    pass
                _note_error(f"HTTP {exc.code}: {body or exc.reason}")
                return None
            except Exception as exc:  # noqa: BLE001
                _note_error(f"{type(exc).__name__}: {exc}")
                return None
            rows = (data or {}).get("data") or []
            if len(rows) != len(chunk):
                _note_error(f"返回 {len(rows)} 条，请求 {len(chunk)} 条")
                return None
            rows = sorted(rows, key=lambda r: r.get("index") if isinstance(r.get("index"), int) else 0)
            usage = (data or {}).get("usage") or {}
            ms = (time.time() - t0) * 1000
            with _stats_lock:
                _STATS["calls"] += 1
                _STATS["texts"] += len(chunk)
                _STATS["ms"] = round(_STATS["ms"] + ms, 1)
                _STATS["tokens"] += int(usage.get("prompt_tokens") or usage.get("total_tokens") or 0)
            for key, row in zip(chunk, rows):
                vec = row.get("embedding")
                if not isinstance(vec, list) or not vec:
                    _note_error("返回里没有 embedding")
                    return None
                vec = [float(x) for x in vec]
                for i in todo[key]:
                    out[i] = vec
                cache[key] = {"v": [round(x, 5) for x in vec], "u": round(time.time(), 1)}
            if rows and c["dim"] != len(rows[0].get("embedding") or []):
                cur = _read(_config_path(), {})
                if isinstance(cur, dict):
                    cur["dim"] = len(rows[0].get("embedding") or [])
                    write_json_atomic(_config_path(), cur, mode=0o600)
        with _cache_lock:
            _save_cache(cache)
    global LAST_ERROR
    LAST_ERROR = ""
    _LAST_ENGINE = model
    return [v for v in out if v is not None]


# ---------- local hash fallback ----------
def _tokens(text: str) -> list[str]:
    text = (text or "").lower()
    tokens: list[str] = []
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    for word in re.findall(r"[a-zA-Z][a-zA-Z0-9_]{1,}", text):
        tokens.append(word)
    return [t for t in tokens if t not in STOP]


def local_embed(text: str) -> list[float]:
    vec = [0.0] * LOCAL_DIM
    for token in _tokens(text):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        idx = int.from_bytes(digest[:4], "big") % LOCAL_DIM
        sign = 1.0 if digest[4] & 1 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(x * x for x in vec))
    if norm <= 1e-9:
        return vec
    return [x / norm for x in vec]


def cosine(a: Iterable[float], b: Iterable[float]) -> float:
    return float(sum(x * y for x, y in zip(a, b)))


def _vectors(texts: list[str]) -> tuple[list[list[float]], str]:
    global _LAST_ENGINE
    remote = _remote_embed_texts(texts)
    if remote is not None:
        return remote, _LAST_ENGINE
    _LAST_ENGINE = LOCAL_MODEL
    return [local_embed(t) for t in texts], LOCAL_MODEL


def embed(text: str) -> list[float]:
    vecs, _ = _vectors([text])
    return vecs[0]


def unit_text(unit: dict[str, Any]) -> str:
    parts = [
        str(unit.get("title") or ""),
        str(unit.get("essence") or ""),
        " ".join(str(x) for x in (unit.get("tags") or [])),
        " ".join(str(x) for x in (unit.get("steps") or [])),
        " ".join(str(x) for x in (unit.get("boundaries") or [])),
        " ".join(str(x) for x in (unit.get("key_points") or [])),
    ]
    return " ".join(parts)


def rank(query: str, docs: list[dict[str, Any]], text_fn) -> list[tuple[float, dict[str, Any]]]:
    if not docs:
        return []
    texts = [query] + [text_fn(d) for d in docs]
    vecs, engine = _vectors(texts)
    q = vecs[0]
    out = [(cosine(q, vecs[i + 1]), doc) for i, doc in enumerate(docs) if any(vecs[i + 1])]
    return sorted(out, key=lambda x: x[0], reverse=True)


def related_units(unit: dict[str, Any], units: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
    others = [u for u in units if u.get("id") != unit.get("id")]
    if not others:
        return []
    texts = [unit_text(unit)] + [unit_text(u) for u in others]
    vecs, engine = _vectors(texts)
    base = vecs[0]
    min_score = 0.15 if engine != LOCAL_MODEL else 0.02
    scored = [(cosine(base, vecs[i + 1]), u) for i, u in enumerate(others)]
    scored = [(s, u) for s, u in scored if s >= min_score]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        {"id": u["id"], "title": u.get("title"), "essence": u.get("essence"),
         "score": round(score, 3)}
        for score, u in scored[:limit]
    ]


def semantic_groups(paragraphs: list[str], max_size: int = 4,
                    threshold: float | None = None) -> list[list[str]]:
    """用当前向量引擎把相邻段落聚成主题组；无云端时用本地哈希向量。"""
    if len(paragraphs) <= 2:
        return [paragraphs] if paragraphs else []
    vecs, engine = _vectors(paragraphs)
    if threshold is None:
        threshold = 0.32 if engine != LOCAL_MODEL else 0.16
    groups: list[list[str]] = []
    current: list[str] = []
    centroid: list[float] | None = None
    for para, vec in zip(paragraphs, vecs):
        if current and (len(current) >= max_size or cosine(centroid or vec, vec) < threshold):
            groups.append(current)
            current = []
            centroid = None
        current.append(para)
        if centroid is None:
            centroid = vec
        else:
            centroid = [(a + b) / 2 for a, b in zip(centroid, vec)]
    if current:
        groups.append(current)
    return groups


def probe() -> dict[str, Any]:
    if not configured():
        return {"ok": False, "detail": "还没填 embedding 的 API Key", "mode": "local"}
    global _fail_until
    _fail_until = 0.0
    t0 = time.time()
    texts = ["中值定理把区间上的平均变化率与端点导数联系起来。"]
    vecs = _remote_embed_texts(texts, fresh=True)
    ms = round((time.time() - t0) * 1000, 1)
    if not vecs or not vecs[0]:
        return {"ok": False, "detail": LAST_ERROR or "没有拿到向量", "ms": ms,
                "mode": "fallback", "model": get_config()["model"]}
    return {"ok": True, "dim": len(vecs[0]), "ms": ms, "model": get_config()["model"],
            "detail": f"维度 {len(vecs[0])}，{ms}ms", "mode": "cloud",
            "stats": dict(_STATS)}
