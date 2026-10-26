"""小工具：时间、ID、JSON、文本处理。"""
from __future__ import annotations

import json
import math
import os
import re
from urllib.parse import urlparse
import secrets
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

UTC = timezone.utc


def now() -> datetime:
    return datetime.now(UTC)


def now_iso() -> str:
    return now().isoformat(timespec="seconds").replace("+00:00", "Z")


def iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso(value: str | None, default: datetime | None = None) -> datetime:
    if not value:
        return default or now()
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except (ValueError, TypeError):
        return default or now()


def days_between(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 86400.0


def iso_after(seconds: float) -> str:
    return iso(now() + timedelta(seconds=seconds))


def today_str(dt: datetime | None = None) -> str:
    return (dt or datetime.now().astimezone()).strftime("%Y-%m-%d")


_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"


def _base36(n: int, width: int = 0) -> str:
    if n == 0:
        s = "0"
    else:
        chars = []
        while n:
            n, r = divmod(n, 36)
            chars.append(_ALPHABET[r])
        s = "".join(reversed(chars))
    return s.rjust(width, "0")


def new_id(prefix: str = "") -> str:
    """时间有序 + 随机后缀，便于排序且不依赖外部库。"""
    stamp = _base36(time.time_ns(), 12)
    rand = secrets.token_hex(4)
    core = f"{stamp}{rand}"
    return f"{prefix}_{core}" if prefix else core


def slugify(text: str, fallback: str = "item") -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", text, flags=re.UNICODE)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text[:64] or fallback


def normalize_base_url(value: Any, field: str = "Base URL") -> str:
    """把"模型地址"这种一眼能填错的东西，在保存时就拦下来。

    以前不校验：填成 `ht tp://x` 也回一句"已启用"，用户要到第一次调用失败才知道坏了，
    而失败信息是网络层的，看不出是自己粘错了地址。
    """
    url = str(value or "").strip()
    if not url:
        return ""
    if re.search(r"\s", url):
        raise ValueError(f"{field} 里不能有空格，看起来是把别的东西一起粘进来了")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"{field} 要写成 https://主机/路径 的形式，"
                         f"例如 https://api.openai.com/v1（现在这个是 {url[:40]}）")
    return url.rstrip("/")


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def round4(value: float) -> float:
    return round(float(value), 4)


def truncate(text: str, length: int = 180) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= length else text[: length - 1] + "…"


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def write_json_atomic(path: Path, data: Any, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name, dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def jwt_like_mask(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 10:
        return "…" + key[-2:]
    return key[:5] + "…" + key[-4:]


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?；;\n])\s*", text or "")
    return [p.strip() for p in parts if p.strip()]


def split_cn(text: str, limit: int = 8) -> list[str]:
    """把长文本切成短句/短语，用于关键点。"""
    parts = re.split(r"[。！？!?；;，,、\n]+", text or "")
    out = []
    for p in parts:
        p = p.strip(" \t-•·")
        if 2 <= len(p) <= 42:
            out.append(p)
    return out[:limit]


def strip_markdown(text: str) -> str:
    text = re.sub(r"```[\s\S]*?```", " ", text or "")
    text = re.sub(r"[#>*_`~\-]{1,}", " ", text)
    text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def _fuzzy_keyword_present(text: str, keyword: str) -> bool:
    """演示模式的中文模糊匹配：精确命中，或关键二元组覆盖达到阈值。"""
    text_low = (text or "").lower()
    kw = (keyword or "").strip()
    if not kw:
        return False
    if kw.lower() in text_low:
        return True
    if len(kw) >= 4 and all("\u4e00" <= c <= "\u9fff" for c in kw):
        bigrams = [kw[i:i + 2] for i in range(len(kw) - 1)]
        if not bigrams:
            return False
        hit = sum(1 for bg in bigrams if bg in text_low)
        # 至少两个二元组命中，且覆盖率 >= 0.35；避免单个常见词造成假阳性
        return hit >= 2 and hit / len(bigrams) >= 0.35
    tokens = re.findall(r"[a-zA-Z]{3,}", kw.lower())
    if tokens:
        hits = sum(1 for t in tokens if t in text_low)
        return hits >= max(2, int(len(tokens) * 0.6))
    return False


def keyword_hits(text: str, keywords: Iterable[str]) -> list[str]:
    hits = []
    for kw in keywords:
        kw = (kw or "").strip()
        if kw and _fuzzy_keyword_present(text, kw):
            hits.append(kw)
    return hits


def coverage(text: str, keywords: Iterable[str]) -> tuple[float, list[str], list[str]]:
    kws = [k for k in keywords if k and len(k) >= 2]
    if not kws:
        return 0.0, [], []
    hits = keyword_hits(text, kws)
    missed = [k for k in kws if k not in hits]
    return len(hits) / len(kws), hits, missed


def parse_int(value: Any, default: int, low: int = 0, high: int = 9999) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return int(clamp(n, low, high))


def parse_float(value: Any, default: float, low: float = 0.0, high: float = 1.0) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        n = default
    return float(clamp(n, low, high))


def human_minutes(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} 分钟"
    h, m = divmod(minutes, 60)
    return f"{h} 小时" + (f" {m} 分钟" if m else "")


def safe_div(a: float, b: float, default: float = 0.0) -> float:
    try:
        return a / b if b else default
    except ZeroDivisionError:
        return default


def linreg(points: list[tuple[float, float]]) -> tuple[float, float]:
    """最小二乘，用于保留率趋势。返回 (slope, intercept)。"""
    n = len(points)
    if n < 2:
        return 0.0, points[0][1] if points else 0.0
    sx = sum(p[0] for p in points)
    sy = sum(p[1] for p in points)
    sxx = sum(p[0] * p[0] for p in points)
    sxy = sum(p[0] * p[1] for p in points)
    denom = n * sxx - sx * sx
    if math.isclose(denom, 0.0):
        return 0.0, sy / n
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    return slope, intercept
