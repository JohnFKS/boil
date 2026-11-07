"""零依赖 OpenAI 兼容 LLM 网关。

- 配置存 data/llm.json（权限 0600），API Key 永不返回给前端；
- 支持普通与流式（SSE）调用、JSON 模式、宽松 JSON 修复、失败重试；
- 失败必须可见：last_error 会出现在设置页和 /api/health；
- 未配置 Key 时由各业务层走本地确定性引擎，不在这里偷偷伪造模型回答。
"""
from __future__ import annotations

import http.client
import hashlib
import json
import os
import re
import socket
import ssl
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Iterable, Iterator
from urllib.parse import urlsplit

from . import config
from .utils import (jwt_like_mask, normalize_base_url, now_iso, read_json,
                   write_json_atomic)

DEFAULT_BASE = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_AUTH_HEADER = "Authorization"
DEFAULT_AUTH_PREFIX = "Bearer "
READ_IDLE = 120
CONNECT_TIMEOUT = 20
MAX_RETRIES = 2
RESERVED_PAYLOAD_KEYS = {
    "model", "messages", "stream", "max_tokens", "temperature",
    "response_format", "tools", "tool_choice", "stop",
}

# Dots（dots3-note-prev）平台约束：512K 上下文窗口，输入 tokens + max_tokens
# 不得超过上限；限流按「单个 API Key」分别计算 RPM / TPM。
DOTS_CONTEXT_WINDOW = 524_288
DEFAULT_RPM = 60
DEFAULT_TPM = 1_500_000
RATE_WINDOW_SECONDS = 60.0
DOTS_HOST_HINT = "askdiandian.com"
DOTS_MODEL = "dots3-note-prev"

PROVIDERS = [
    {"id": "dots", "name": "Dots（小红书）",
     "base_url": "https://note3-prev-api.askdiandian.com/v1",
     "model": "dots3-note-prev", "deep_model": "dots3-note-prev",
     "auth_header": "api-key", "auth_prefix": "",
     "extra_body": {"chat_template_kwargs": {"enable_thinking": False}}},
    {"id": "openai", "name": "OpenAI", "base_url": "https://api.openai.com/v1",
     "model": "gpt-4o-mini", "deep_model": "gpt-4o"},
    {"id": "deepseek", "name": "DeepSeek", "base_url": "https://api.deepseek.com/v1",
     "model": "deepseek-chat", "deep_model": "deepseek-reasoner"},
    {"id": "qwen", "name": "通义千问（DashScope 兼容）",
     "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
     "model": "qwen-plus", "deep_model": "qwen-max"},
    {"id": "moonshot", "name": "Moonshot Kimi", "base_url": "https://api.moonshot.cn/v1",
     "model": "moonshot-v1-8k", "deep_model": "moonshot-v1-32k"},
    {"id": "ollama", "name": "Ollama（本地）", "base_url": "http://127.0.0.1:11434/v1",
     "model": "qwen2.5:7b", "deep_model": "qwen2.5:14b"},
    {"id": "custom", "name": "自定义 OpenAI 兼容端点", "base_url": "", "model": "", "deep_model": ""},
]


class LLMError(Exception):
    def __init__(self, message: str, detail: str = "", code: str = "llm_error",
                 hint: str = ""):
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.code = code
        self.hint = hint

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "detail": self.detail,
                "hint": self.hint}


_HTTP_HINTS: dict[int, str] = {
    400: "请求参数或 JSON 格式不正确：检查 model / messages / max_tokens；输入 tokens 与 max_tokens 之和不得超过 524,288。",
    401: "API Key 缺失或无效：Dots 必须用 api-key 请求头传完整 Key（无 Bearer 前缀），且不能写进 URL、前端或日志。",
    403: "Key 有效但无权访问该资源：确认平台已开通 dots3-note-prev、Key 未过期且未被停用。",
    404: "地址或模型不存在：Base URL 通常要以 /v1 结尾；也可能是模型名拼错。",
    408: "网关超时：把超时调大，或换更快的模型；长思考类模型建议提高超时。",
    409: "并发冲突：稍后重试；Boil 已按指数退避重试两次。",
    413: "请求体过大：缩短材料或降低单次编译的字符数。",
    422: "参数校验失败：多为额外参数（extra_body）与网关不兼容，可清空高级参数再试。",
    429: "过于频繁：默认限流为单 Key 60 次/分钟、1,500,000 tokens/分钟。Boil 已按 Retry-After 退避重试；仍失败请降低并发或改用极速索引导入。",
    500: "服务内部错误：通常是服务商抖动，稍后重试。",
    502: "上游无响应：网关到模型的链路断了。",
    503: "服务不可用：模型正在维护或过载。",
    504: "网关超时：思考型模型常在长推理时触发；Boil 对上游改用流式接收来规避这类空闲超时。",
}

_NETWORK_HINTS: dict[str, str] = {
    "dns": "域名解析失败：检查 Base URL 的域名，以及本机是否需要代理/VPN。",
    "refused": "连接被拒绝：端口没监听或服务没起来；本地模型（Ollama 等）要先启动。",
    "timeout": "连接或读取超时：提高设置里的超时，或换延迟更低的模型。",
    "tls": "TLS 握手失败：证书链或代理拦截了 HTTPS，检查系统证书与代理设置。",
    "proxy": "代理返回异常：本机代理环境变量可能把请求转到了别处。",
    "network": "网络不通：确认能访问该地址，或在同一网络下重试。",
}


def _classify_exception(exc: BaseException) -> tuple[str, str]:
    """把 urllib/socket 异常链归到可行动的分类，并给一句人话建议。"""
    seen: list[str] = []
    node: BaseException | None = exc
    while node is not None and len(seen) < 6:
        seen.append(f"{type(node).__name__}: {node}")
        text = str(node).lower()
        name = type(node).__name__.lower()
        if "getaddrinfo" in text or "nameresolution" in name or "name or service not known" in text:
            return "dns", _NETWORK_HINTS["dns"]
        if "refused" in text:
            return "refused", _NETWORK_HINTS["refused"]
        if "timed out" in text or name in ("timeouterror", "sockettimeout"):
            return "timeout", _NETWORK_HINTS["timeout"]
        if "ssl" in name or "certificate" in text or "tls" in text:
            return "tls", _NETWORK_HINTS["tls"]
        if "proxy" in text or "tunnel connection" in text:
            return "proxy", _NETWORK_HINTS["proxy"]
        node = node.__cause__ or node.__context__
        if node is exc:
            break
    return "network", _NETWORK_HINTS["network"]


def _http_hint(status: int) -> str:
    if status in _HTTP_HINTS:
        return _HTTP_HINTS[status]
    if 400 <= status < 500:
        return "请求被拒绝：核对模型名、参数与 Key 的权限。"
    if status >= 500:
        return "网关侧错误：稍后重试；持续失败请换模型或看服务商状态页。"
    return ""


def _safe_detail(text: str) -> str:
    """回显的服务端内容可能带 Key：把 sk-/Bearer 之类掩掉再落盘或回传。"""
    out = re.sub(r"(sk|ak|key|token)[-_=]([A-Za-z0-9_-]{6,})",
                 lambda m: f"{m.group(1)}-…{m.group(2)[-4:]}", str(text or ""), flags=re.I)
    out = re.sub(r"(bearer\s+)[A-Za-z0-9._-]{8,}", lambda m: f"{m.group(1)}…{m.group(0)[-4:]}",
                 out, flags=re.I)
    # 网关有时会把整个请求头回显回来；配置里的完整 Key 一律按字面掩掉
    try:
        key = str(get_config().get("api_key") or "")
        if len(key) >= 8:
            out = out.replace(key, f"…{key[-4:]}")
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# Dots 协议适配：认证自检、RPM/TPM 限流、上下文预算、token 用量
# ---------------------------------------------------------------------------


def usage_path():
    return config.DATA_DIR / "llm_usage.json"


def _retry_after_seconds(headers: Any) -> float:
    """429/503 常带 Retry-After（秒数或 HTTP 日期），优先听服务端的。"""
    try:
        raw = str(headers.get("Retry-After") or "").strip()
    except AttributeError:
        return 0.0
    if not raw:
        return 0.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(raw)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return 0.0


def estimate_prompt_tokens(messages: list[dict[str, Any]]) -> int:
    """中文约 1~2 字符/token、英文约 4 字符/token；取 2 字符/token 偏保守。"""
    chars = 0
    for item in messages or []:
        content = item.get("content") if isinstance(item, dict) else None
        if isinstance(content, str):
            chars += len(content)
        elif isinstance(content, list):
            chars += sum(len(str(x.get("text") or "")) for x in content
                         if isinstance(x, dict))
    return int(chars / 2) + 16


def estimate_request_tokens(messages: list[dict[str, Any]],
                            max_tokens: int | None = None) -> int:
    return estimate_prompt_tokens(messages) + int(max_tokens or 0) + 64


def _fit_context_budget(messages: list[dict[str, Any]], max_tokens: int | None,
                        cfg: dict[str, Any]) -> int | None:
    """输入 tokens + max_tokens 不得超过上下文窗口（Dots 为 524,288）。"""
    window = int(cfg.get("context_window") or DOTS_CONTEXT_WINDOW)
    prompt = estimate_prompt_tokens(messages)
    if prompt + 1024 >= window:
        raise LLMError(
            "输入超出模型上下文窗口",
            f"估算输入 {prompt:,} tokens，窗口 {window:,} tokens；请拆分材料",
            "context_overflow",
            "按章节分批导入，或改用极速索引（按需深化只送本小节原文）。")
    if not max_tokens:
        return None
    ceiling = max(256, window - prompt - 1024)
    return min(int(max_tokens), ceiling)


class _RateLimiter:
    """单进程内按 Key 指纹做 60 秒滑动窗口限流，主动避免打到 429。

    Dots 的 RPM / TPM 都按单个 API Key 计算；这里不存明文 Key，只用
    sha256 前 16 位区分不同 Key 的窗口。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._windows: dict[str, deque] = {}
        self._stats = {"requests": 0, "waits": 0, "wait_seconds": 0.0}

    def acquire(self, scope: str, rpm: int, tpm: int, tokens: int) -> None:
        if rpm <= 0 and tpm <= 0:
            return
        while True:
            with self._lock:
                now = time.monotonic()
                window = self._windows.setdefault(scope, deque())
                while window and now - window[0][0] >= RATE_WINDOW_SECONDS:
                    window.popleft()
                delay = 0.0
                if rpm > 0 and len(window) >= rpm:
                    delay = max(delay, RATE_WINDOW_SECONDS - (now - window[0][0]) + 0.01)
                if tpm > 0:
                    used = sum(t for _, t in window)
                    short = used + max(1, tokens) - tpm
                    if short > 0:
                        for stamp, spent in window:
                            short -= spent
                            if short <= 0:
                                delay = max(delay, RATE_WINDOW_SECONDS - (now - stamp) + 0.01)
                                break
                        else:
                            delay = max(delay, 0.05)
                if delay <= 0:
                    window.append((now, max(1, tokens)))
                    self._stats["requests"] += 1
                    return
                self._stats["waits"] += 1
                self._stats["wait_seconds"] += delay
            time.sleep(min(delay, RATE_WINDOW_SECONDS))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._stats)


_limiter = _RateLimiter()
_usage_lock = threading.Lock()


def rate_limit_stats() -> dict[str, Any]:
    cfg = get_config()
    snap = _limiter.snapshot()
    return {"rpm": cfg["rpm"], "tpm": cfg["tpm"],
            "window_seconds": RATE_WINDOW_SECONDS,
            "requests": snap["requests"], "waits": snap["waits"],
            "wait_seconds": round(snap["wait_seconds"], 3)}


def _empty_usage() -> dict[str, Any]:
    return {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
            "total_tokens": 0, "estimated_calls": 0, "by_model": {},
            "since": now_iso(), "updated_at": ""}


def usage_stats() -> dict[str, Any]:
    data = read_json(usage_path(), {}) or {}
    out = _empty_usage()
    if isinstance(data, dict):
        for key in out:
            if key in data:
                out[key] = data[key]
    return out


def reset_usage_stats() -> None:
    try:
        usage_path().unlink(missing_ok=True)
    except OSError:
        pass


def _record_usage(usage: dict[str, Any] | None, model: str,
                  prompt_guess: int, completion_guess: int) -> None:
    """把每次真实调用的 token 账记下来；网关没回 usage 时用估算并单独计数。"""
    try:
        with _usage_lock:
            data = read_json(usage_path(), {}) or {}
            if not isinstance(data, dict):
                data = {}
            given = usage if isinstance(usage, dict) else {}
            prompt = int(given.get("prompt_tokens") or prompt_guess or 0)
            completion = int(given.get("completion_tokens") or completion_guess or 0)
            total = int(given.get("total_tokens") or (prompt + completion) or 0)
            data["calls"] = int(data.get("calls") or 0) + 1
            data["prompt_tokens"] = int(data.get("prompt_tokens") or 0) + prompt
            data["completion_tokens"] = int(data.get("completion_tokens") or 0) + completion
            data["total_tokens"] = int(data.get("total_tokens") or 0) + total
            if not given:
                data["estimated_calls"] = int(data.get("estimated_calls") or 0) + 1
            by_model = data.get("by_model")
            if not isinstance(by_model, dict):
                by_model = {}
            entry = by_model.get(model) or {"calls": 0, "total_tokens": 0}
            entry["calls"] = int(entry.get("calls") or 0) + 1
            entry["total_tokens"] = int(entry.get("total_tokens") or 0) + total
            by_model[model] = entry
            data["by_model"] = by_model
            data["since"] = data.get("since") or now_iso()
            data["updated_at"] = now_iso()
            write_json_atomic(usage_path(), data, mode=0o600)
    except Exception:
        pass  # 记账失败绝不能影响一次成功回答


def config_warnings(cfg: dict[str, Any] | None = None) -> list[str]:
    """把文档里的硬约束变成设置页可见的自检，而不是等 401 才猜。"""
    cfg = cfg or get_config()
    out: list[str] = []
    url = str(cfg.get("base_url") or "").lower()
    model = str(cfg.get("model") or "")
    key = str(cfg.get("api_key") or "")
    if key and key in url:
        out.append("Base URL 里疑似包含 API Key：请立刻删除并重新生成 Key。")
    if DOTS_HOST_HINT in url or model == DOTS_MODEL:
        if cfg.get("auth_header") != "api-key" or cfg.get("auth_prefix"):
            out.append("Dots 要求 api-key 请求头且不带 Bearer 前缀，当前认证配置会返回 401。")
        if model != DOTS_MODEL:
            out.append(f"Dots 当前文档对应模型为 {DOTS_MODEL}，请核对模型名。")
        extra = cfg.get("extra_body") or {}
        thinking = extra.get("chat_template_kwargs") if isinstance(extra, dict) else None
        # 运行时已在 _apply_extra_body 里兜底注入 false，不必再让用户手动补；
        # 只有用户显式开了深度思考才需要说清代价（dots 只有 max 档，无法调低）。
        if isinstance(thinking, dict) and thinking.get("enable_thinking") is True:
            out.append("已显式开启深度思考：dots 仅支持 max 档，教材抽取会明显变慢并增加 token 消耗。")
    return out


def _env_config() -> dict[str, Any]:
    return {
        "base_url": os.environ.get("BOIL_LLM_BASE_URL", DEFAULT_BASE),
        "api_key": os.environ.get("BOIL_LLM_KEY", ""),
        "model": os.environ.get("BOIL_LLM_MODEL", DEFAULT_MODEL),
        "deep_model": os.environ.get("BOIL_LLM_DEEP_MODEL", ""),
        "temperature": 0.5,
        "max_tokens": None,
        "timeout": READ_IDLE,
        "auth_header": os.environ.get("BOIL_LLM_AUTH_HEADER", DEFAULT_AUTH_HEADER),
        "auth_prefix": os.environ.get("BOIL_LLM_AUTH_PREFIX", DEFAULT_AUTH_PREFIX),
        "rpm": os.environ.get("BOIL_LLM_RPM", DEFAULT_RPM),
        "tpm": os.environ.get("BOIL_LLM_TPM", DEFAULT_TPM),
        "context_window": os.environ.get("BOIL_LLM_CONTEXT_WINDOW", DOTS_CONTEXT_WINDOW),
        "extra_body": {},
    }


def _normalize_auth_prefix(header: str, prefix: Any) -> str:
    if prefix is None:
        return DEFAULT_AUTH_PREFIX
    value = str(prefix).replace("\r", "").replace("\n", "")
    if header.lower() == "authorization" and value.strip().lower() == "bearer":
        return "Bearer "
    return value


def _text(value: Any, default: str = "") -> str:
    """data/llm.json 是用户可以手改的文件：类型不对就当没写，别让用户的一次打字错误把 AI 层整个打崩。"""
    return value.strip() if isinstance(value, str) else default


def _number(value: Any, default: Any, cast) -> Any:
    if isinstance(value, bool) or value is None:
        return default
    try:
        return cast(value)
    except (TypeError, ValueError):
        return default


def get_config() -> dict[str, Any]:
    base = _env_config()
    stored = read_json(config.LLM_CONFIG_PATH, {}) or {}
    if not isinstance(stored, dict):
        stored = {}
    cfg = {**base, **{k: v for k, v in stored.items() if v is not None}}
    # 同 embeddings：显式清空过就保持空，别让"清掉地址"变成"发去 OpenAI"。
    # 优先级：文件里写过的值（含空串）> 环境变量 > 内置默认。
    stored_url = stored.get("base_url")
    if isinstance(stored_url, str) and stored_url.strip():
        url = stored_url.strip()
    elif isinstance(stored_url, str):
        url = ""
    else:
        url = _text(base.get("base_url")) or DEFAULT_BASE
    cfg["base_url"] = url.rstrip("/")
    cfg["model"] = _text(cfg.get("model")) or DEFAULT_MODEL
    cfg["deep_model"] = _text(cfg.get("deep_model")) or cfg["model"]
    cfg["api_key"] = _text(cfg.get("api_key"))
    cfg["temperature"] = _number(cfg.get("temperature"), 0.5, float)
    cfg["timeout"] = _number(cfg.get("timeout"), READ_IDLE, int)
    cfg["max_tokens"] = _number(cfg.get("max_tokens"), None, int)
    header = str(cfg.get("auth_header") or DEFAULT_AUTH_HEADER).strip() or DEFAULT_AUTH_HEADER
    if not re.fullmatch(r"[A-Za-z0-9-]+", header):
        header = DEFAULT_AUTH_HEADER
    cfg["auth_header"] = header
    prefix = cfg.get("auth_prefix")
    if prefix is None:
        prefix = DEFAULT_AUTH_PREFIX
    cfg["auth_prefix"] = _normalize_auth_prefix(header, prefix)
    extra = cfg.get("extra_body")
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except ValueError:
            extra = {}
    if not isinstance(extra, dict):
        extra = {}
    cfg["extra_body"] = extra
    cfg["cache_enabled"] = bool(cfg.get("cache_enabled", True))
    cfg["stream_upstream"] = bool(cfg.get("stream_upstream", True))
    cfg["rpm"] = max(0, _number(cfg.get("rpm"), DEFAULT_RPM, int))
    cfg["tpm"] = max(0, _number(cfg.get("tpm"), DEFAULT_TPM, int))
    cfg["context_window"] = max(4096, _number(cfg.get("context_window"),
                                              DOTS_CONTEXT_WINDOW, int))
    return cfg


def set_config(payload: dict[str, Any]) -> dict[str, Any]:
    cur = get_config()
    incoming = payload.get("base_url")
    out = {
        "base_url": normalize_base_url(incoming if str(incoming or "").strip()
                                       else cur["base_url"]).rstrip("/"),
        "model": (payload.get("model") or cur["model"]).strip(),
        "deep_model": (payload.get("deep_model") or "").strip(),
        "temperature": payload.get("temperature", cur["temperature"]),
        "max_tokens": payload.get("max_tokens", cur["max_tokens"]),
        "timeout": payload.get("timeout", cur["timeout"]),
        "rpm": payload.get("rpm", cur["rpm"]),
        "tpm": payload.get("tpm", cur["tpm"]),
        "context_window": payload.get("context_window", cur["context_window"]),
    }
    header = str(payload.get("auth_header", cur["auth_header"]) or DEFAULT_AUTH_HEADER).strip()
    if not re.fullmatch(r"[A-Za-z0-9-]+", header):
        raise ValueError("auth_header 必须是合法的 HTTP 头名称")
    out["auth_header"] = header or DEFAULT_AUTH_HEADER
    prefix = payload.get("auth_prefix", cur["auth_prefix"])
    if prefix is None:
        prefix = cur["auth_prefix"]
    out["auth_prefix"] = str(prefix).replace("\r", "").replace("\n", "")
    extra = cur.get("extra_body") or {}
    if "extra_body" in payload:
        raw = payload.get("extra_body")
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            extra = {}
        elif isinstance(raw, dict):
            extra = raw
        elif isinstance(raw, str):
            try:
                extra = json.loads(raw)
            except ValueError as exc:
                raise ValueError("extra_body 必须是合法 JSON 对象") from exc
            if not isinstance(extra, dict):
                raise ValueError("extra_body 必须是 JSON 对象")
        else:
            raise ValueError("extra_body 必须是 JSON 对象")
    out["extra_body"] = extra
    for flag in ("cache_enabled", "stream_upstream"):
        out[flag] = bool(payload[flag]) if flag in payload else bool(cur.get(flag, True))
    # None = 不修改；空字符串 = 显式清空 Key（回到演示模式）
    if "api_key" in payload and payload["api_key"] is not None:
        key = str(payload["api_key"]).strip()
        out["api_key"] = key
    else:
        out["api_key"] = cur["api_key"]
    write_json_atomic(config.LLM_CONFIG_PATH, out, mode=0o600)
    return get_config()


def mask(key: str) -> str:
    return jwt_like_mask(key)


def enabled() -> bool:
    if config.LLM_OFF:
        return False
    cfg = get_config()
    # 没有地址的 Key 不该被当成"可以开始发请求"：那会退到默认厂商端点
    return bool(cfg.get("api_key") and cfg.get("base_url"))


def mode() -> str:
    return "live" if enabled() else "demo"


def model_name(deep: bool = False) -> str:
    cfg = get_config()
    if not enabled():
        return "local/deterministic-tutor"
    return cfg.get("deep_model") if deep and cfg.get("deep_model") else cfg.get("model")


def _base_url() -> str:
    url = get_config()["base_url"].rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    return url + "/chat/completions"


def last_error() -> dict[str, Any] | None:
    state = read_json(config.LLM_STATE_PATH, {}) or {}
    if not state:
        return None
    return state


def _note_error(message: str, detail: str = "", code: str = "llm_error",
                hint: str = "") -> None:
    cfg = {}
    try:
        cfg = get_config()
    except Exception:
        cfg = {}
    write_json_atomic(config.LLM_STATE_PATH, {
        "message": message[:300],
        "detail": _safe_detail(detail)[:1200],
        "code": code,
        "hint": hint,
        "at": now_iso(),
        "base_url": cfg.get("base_url", ""),
        "model": cfg.get("model", ""),
        "api_key_masked": mask(cfg.get("api_key", "")) if cfg.get("api_key") else "",
    }, mode=0o600)


def _clear_error() -> None:
    try:
        config.LLM_STATE_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def public_config() -> dict[str, Any]:
    cfg = get_config()
    err = last_error()
    return {
        "mode": mode(),
        "enabled": enabled(),
        "base_url": cfg["base_url"],
        "model": cfg["model"],
        "deep_model": cfg["deep_model"],
        "api_key_set": bool(cfg["api_key"]),
        "api_key_masked": mask(cfg["api_key"]),
        "temperature": cfg["temperature"],
        "max_tokens": cfg["max_tokens"],
        "timeout": cfg["timeout"],
        "auth_header": cfg["auth_header"],
        "auth_prefix": cfg["auth_prefix"],
        "extra_body": cfg["extra_body"],
        "providers": PROVIDERS,
        "last_error": err,
        "forced_off": config.LLM_OFF,
        "cache_enabled": bool(cfg.get("cache_enabled", True)),
        "stream_upstream": bool(cfg.get("stream_upstream", True)),
        "cache": cache_stats(),
        "cache_entries": cache_size(),
        "rpm": cfg["rpm"],
        "tpm": cfg["tpm"],
        "context_window": cfg["context_window"],
        "rate_limit": rate_limit_stats(),
        "usage": usage_stats(),
        "config_warnings": config_warnings(cfg),
    }


def _request_headers(cfg: dict[str, Any], accept: str) -> dict[str, str]:
    header = cfg.get("auth_header") or DEFAULT_AUTH_HEADER
    prefix = _normalize_auth_prefix(header, cfg.get("auth_prefix"))
    return {
        "Content-Type": "application/json",
        "Accept": accept,
        header: f"{prefix}{cfg['api_key']}",
        "User-Agent": "Boil/1.0 (local-first learning os)",
    }


def _ensure_dots_thinking_off(payload: dict[str, Any], cfg: dict[str, Any]) -> None:
    """Dots 深度思考默认开启且只有 max 档：不显式关，编译每批都要多跑一轮最重推理。

    只填空、不覆盖：用户在 extra_body 里显式写了 enable_thinking 时以他的选择为准
    （config_warnings 会说明开启的代价）。预设之外用环境变量 / 自定义端点接 Dots
    时 extra_body 是空的，靠这里兜住，而不是靠设置页的一句建议。
    """
    url = str(cfg.get("base_url") or "").lower()
    model = str(payload.get("model") or cfg.get("model") or "")
    if DOTS_HOST_HINT not in url and model != DOTS_MODEL:
        return
    kwargs = payload.get("chat_template_kwargs")
    if not isinstance(kwargs, dict):
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    elif "enable_thinking" not in kwargs:
        payload["chat_template_kwargs"] = {**kwargs, "enable_thinking": False}


def _apply_extra_body(payload: dict[str, Any], cfg: dict[str, Any]) -> None:
    for key, value in (cfg.get("extra_body") or {}).items():
        if key not in RESERVED_PAYLOAD_KEYS and key not in payload:
            payload[key] = value
    _ensure_dots_thinking_off(payload, cfg)


def _extract_message(data: dict[str, Any]) -> tuple[str, str]:
    try:
        choice = data["choices"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError("模型返回结构异常", json.dumps(data, ensure_ascii=False)[:800]) from exc
    msg = choice.get("message") or {}
    content = msg.get("content")
    if isinstance(content, list):
        content = "".join(str(x.get("text", "")) for x in content if isinstance(x, dict))
    if not isinstance(content, str) or not content.strip():
        reasoning = msg.get("reasoning_content") or ""
        detail = "模型返回空正文"
        if reasoning:
            detail += "（只有推理内容；请换模型或关闭 reasoning）"
        raise LLMError(detail, json.dumps(data, ensure_ascii=False)[:800], "empty_content")
    return content.strip(), str(choice.get("finish_reason") or "")


def _extract_content(data: dict[str, Any]) -> str:
    return _extract_message(data)[0]


def _should_retry(status: int) -> bool:
    return status in (408, 409, 429, 500, 502, 503, 504)


def _gateway_server_error(status: int, detail: str) -> bool:
    """有些网关把上游 5xx 包装成 HTTP 400（Dots 实测：Unknown provider error）。

    按文档 400 是参数错误，不该重试；但响应体里明写 status=500 /
    InternalServerError 时，重试比让用户改 Key 或改参数有用得多。
    """
    if status != 400:
        return False
    text = str(detail or "").lower()
    return any(marker in text for marker in (
        "internalservererror", "unknown provider error", "provider error",
        "upstream", "bad gateway", "service unavailable",
        '"status":5', '"status": 5'))


def _http_post_json(url: str, headers: dict[str, str], payload: dict[str, Any],
                    timeout: int) -> tuple[int, bytes, float]:
    import urllib.error
    import urllib.request
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(), _retry_after_seconds(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), _retry_after_seconds(exc.headers)
    except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
        kind, hint = _classify_exception(exc)
        raise LLMError("无法连接模型服务", f"{type(exc).__name__}: {exc}", kind, hint) from exc


def _http_post_stream(url: str, headers: dict[str, str], payload: dict[str, Any],
                      timeout: int) -> tuple[int, bytes, float]:
    """向上游要 stream:true，再把分片拼回非流式的响应形状。

    很多网关按「空闲」掐断长请求：思考型模型几十秒不吐字节就被 504 掉。
    流式期间一直有字节到达，链路不会被掐；对调用方仍然是一次普通返回。
    """
    import urllib.error
    import urllib.request
    streamed = {**payload, "stream": True}
    body = json.dumps(streamed, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ctype = (resp.headers.get("Content-Type") or "").lower()
            raw = resp.read()
            if "event-stream" not in ctype:
                # 网关不认 stream，照旧返回 JSON（usage 原样保留）
                return resp.status, raw, _retry_after_seconds(resp.headers)
            pieces: list[str] = []
            finish = ""
            usage: dict[str, Any] = {}
            for line in raw.decode("utf-8", "replace").splitlines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                data_text = line[5:].strip()
                if data_text == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_text)
                except ValueError:
                    continue
                if isinstance(chunk.get("usage"), dict):
                    usage = chunk["usage"]
                for choice in (chunk.get("choices") or []):
                    if choice.get("finish_reason"):
                        finish = str(choice["finish_reason"])
                    piece = (choice.get("delta") or {}).get("content")
                    if piece is None:
                        piece = (choice.get("message") or {}).get("content")
                    if isinstance(piece, list):
                        piece = "".join(str(x.get("text", "")) for x in piece if isinstance(x, dict))
                    if isinstance(piece, str):
                        pieces.append(piece)
            rebuilt = {"choices": [{"message": {"content": "".join(pieces)},
                                    "finish_reason": finish}],
                       "usage": usage}
            return (resp.status, json.dumps(rebuilt, ensure_ascii=False).encode("utf-8"),
                    _retry_after_seconds(resp.headers))
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), _retry_after_seconds(exc.headers)
    except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
        kind, hint = _classify_exception(exc)
        raise LLMError("无法连接模型服务", f"{type(exc).__name__}: {exc}", kind, hint) from exc


def chat(messages: list[dict[str, Any]], model: str | None = None,
         json_mode: bool = False, temperature: float | None = None,
         max_tokens: int | None = None, timeout: int | None = None,
         stop: list[str] | None = None, tools: list[dict[str, Any]] | None = None,
         tool_choice: Any = None) -> str:
    if not enabled():
        raise LLMError("未配置模型，当前为演示模式", "请在设置中填写 Base URL 与 API Key", "not_configured")
    cfg = get_config()
    payload = {
        "model": model or cfg["model"],
        "messages": messages,
        "temperature": cfg["temperature"] if temperature is None else float(temperature),
        "stream": False,
    }
    # 输入 tokens + max_tokens 必须留在 512K 窗口内，超出就收缩输出额度
    tokens = _fit_context_budget(
        messages, max_tokens if max_tokens is not None else cfg.get("max_tokens"), cfg)
    if tokens:
        payload["max_tokens"] = int(tokens)
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if stop:
        payload["stop"] = [str(s) for s in stop][:8]
    if tools:
        payload["tools"] = tools
    if tool_choice:
        payload["tool_choice"] = tool_choice
    _apply_extra_body(payload, cfg)
    headers = _request_headers(cfg, "application/json")
    url = _base_url()
    timeout_s = int(timeout or cfg["timeout"])
    # 限流按单个 API Key 计；只存指纹，不把明文 Key 放进任何统计或日志
    scope = hashlib.sha256(str(cfg.get("api_key") or "").encode("utf-8")).hexdigest()[:16]
    prompt_guess = estimate_prompt_tokens(messages)
    last_exc: LLMError | None = None
    length_retried = False
    poster = _http_post_stream if cfg.get("stream_upstream", True) else _http_post_json
    for attempt in range(MAX_RETRIES + 1):
        _limiter.acquire(scope, cfg["rpm"], cfg["tpm"],
                         estimate_request_tokens(payload["messages"], payload.get("max_tokens")))
        try:
            status, raw, retry_after = poster(url, headers, payload, timeout_s)
        except LLMError as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                time.sleep(0.7 * (attempt + 1))
                continue
            _note_error(exc.message, exc.detail, exc.code, exc.hint)
            raise
        if status == 400 and json_mode:
            detail = raw[:800].decode("utf-8", "replace")
            if "response_format" in detail or "json" in detail.lower():
                payload.pop("response_format", None)
                json_mode = False
                continue
        if status != 200:
            detail = _safe_detail(raw[:1200].decode("utf-8", "replace"))
            wrapped_5xx = _gateway_server_error(status, detail)
            hint = ("网关把上游 5xx 包成了 HTTP 400（服务商内部错误）；Boil 已重试，"
                    "稍后再试即可，不是 Key 或参数问题。" if wrapped_5xx
                    else _http_hint(status))
            exc = LLMError(f"模型服务返回 HTTP {status}", detail, "http_error", hint)
            last_exc = exc
            if (_should_retry(status) or wrapped_5xx) and attempt < MAX_RETRIES:
                # 429 优先听 Retry-After（服务端知道下一分钟窗口何时放开）
                wait = (max(retry_after, 1.0 * (2 ** attempt)) if status == 429
                        else max(1.0, 0.8 * (2 ** attempt)))
                time.sleep(min(wait, 30.0))
                continue
            _note_error(exc.message, exc.detail, exc.code, exc.hint)
            raise exc
        try:
            data = json.loads(raw.decode("utf-8"))
            text, finish = _extract_message(data)
            usage = data.get("usage") if isinstance(data, dict) else None
            if finish == "length" and payload.get("max_tokens"):
                _record_usage(usage if isinstance(usage, dict) and usage else None,
                              str(payload["model"]), prompt_guess, int(len(text) / 2))
                # 被 max_tokens 截断：只缩量续问一次，不再按 4 倍放大额度。
                # 旧写法把编译批的 9000 直接抬到 32000，模型可能真的生成三万 token，
                # 一批的钱和尾延迟都被放大；批次并发时多批同时放大更明显。
                if length_retried:
                    raise LLMError("模型输出连续被截断", text[:300], "length_retry_exhausted")
                length_retried = True
                current = int(payload["max_tokens"])
                ceiling = max(256, int(cfg["context_window"]) - prompt_guess - 1024)
                payload["max_tokens"] = min(max(current * 2, current + 800), 32000, ceiling)
                payload["messages"] = list(payload["messages"]) + [{
                    "role": "user",
                    "content": "上一次回复被输出长度截断。请压缩每个字段的篇幅，"
                               "只输出一个完整合法的 JSON，不要解释、不要代码围栏。",
                }]
                continue
            _record_usage(usage if isinstance(usage, dict) and usage else None,
                          str(payload["model"]), prompt_guess, int(len(text) / 2))
            _clear_error()
            return text
        except (ValueError, LLMError) as exc:
            err = exc if isinstance(exc, LLMError) else LLMError("模型返回不是合法 JSON", str(exc)[:500])
            last_exc = err
            # 连续截断说明「这一批不适合一次生成」，再重试只是重复烧同一份材料；
            # 直接报错交给编译层记 warning，不走普通重试分支。
            if getattr(err, "code", None) == "length_retry_exhausted":
                _note_error(err.message, err.detail, err.code, err.hint)
                raise err
            if attempt < MAX_RETRIES:
                time.sleep(0.5)
                continue
            _note_error(err.message, err.detail, err.code, err.hint)
            raise err
    if last_exc:
        raise last_exc
    raise LLMError("模型调用失败", "未知错误")


def stream_chat(messages: list[dict[str, Any]], model: str | None = None,
                temperature: float | None = None, max_tokens: int | None = None,
                timeout: int | None = None,
                stop: list[str] | None = None) -> Iterator[str]:
    if not enabled():
        raise LLMError("未配置模型，当前为演示模式", "请在设置中填写 Base URL 与 API Key", "not_configured")
    cfg = get_config()
    payload = {
        "model": model or cfg["model"],
        "messages": messages,
        "temperature": cfg["temperature"] if temperature is None else float(temperature),
        "stream": True,
    }
    tokens = _fit_context_budget(
        messages, max_tokens if max_tokens is not None else cfg.get("max_tokens"), cfg)
    if tokens:
        payload["max_tokens"] = int(tokens)
    if stop:
        payload["stop"] = [str(s) for s in stop][:8]
    parts = urlsplit(_base_url())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise LLMError("Base URL 不合法", _base_url(), "bad_config")
    _apply_extra_body(payload, cfg)
    headers = _request_headers(cfg, "text/event-stream")
    headers["Connection"] = "close"
    timeout_s = int(timeout or cfg["timeout"])
    cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    kwargs: dict[str, Any] = {"timeout": timeout_s}
    if parts.scheme == "https":
        kwargs["context"] = ssl.create_default_context()
    conn = cls(parts.hostname, parts.port, **kwargs)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    scope = hashlib.sha256(str(cfg.get("api_key") or "").encode("utf-8")).hexdigest()[:16]
    _limiter.acquire(scope, cfg["rpm"], cfg["tpm"],
                     estimate_request_tokens(messages, payload.get("max_tokens")))
    produced = False
    emitted = 0
    usage: dict[str, Any] = {}
    try:
        conn.request("POST", path, body=body, headers=headers)
        resp = conn.getresponse()
        if resp.status != 200:
            detail = _safe_detail(resp.read(1600).decode("utf-8", "replace"))
            exc = LLMError(f"模型服务返回 HTTP {resp.status}", detail, "http_error",
                           _http_hint(resp.status))
            _note_error(exc.message, exc.detail, exc.code, exc.hint)
            raise exc
        while True:
            raw_line = resp.readline()
            if not raw_line:
                break
            line = raw_line.decode("utf-8", "replace").strip()
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
                continue
            data_text = line[5:].strip()
            if data_text == "[DONE]":
                break
            try:
                data = json.loads(data_text)
            except ValueError:
                continue
            if isinstance(data.get("usage"), dict):
                usage = data["usage"]
            choices = data.get("choices") or []
            if not choices:
                continue
            choice = choices[0] or {}
            delta = choice.get("delta") or {}
            piece = delta.get("content")
            if piece is None:
                msg = choice.get("message") or {}
                piece = msg.get("content")
            if isinstance(piece, list):
                piece = "".join(str(x.get("text", "")) for x in piece if isinstance(x, dict))
            if isinstance(piece, str) and piece:
                produced = True
                emitted += len(piece)
                yield piece
        if not produced:
            exc = LLMError("模型流式返回空正文", "服务端没有返回任何 content 分片", "empty_content")
            _note_error(exc.message, exc.detail, exc.code, exc.hint)
            raise exc
        _record_usage(usage or None, str(payload["model"]),
                      estimate_prompt_tokens(messages), int(emitted / 2))
        _clear_error()
    except LLMError:
        raise
    except (http.client.HTTPException, socket.timeout, TimeoutError, OSError) as exc:
        err = LLMError("流式连接中断", f"{type(exc).__name__}: {exc}", "stream")
        _note_error(err.message, err.detail, err.code, err.hint)
        raise err from exc
    finally:
        try:
            conn.close()
        except Exception:
            pass


def test_connection() -> dict[str, Any]:
    if not enabled():
        raise LLMError("演示模式无需测试", "填写 API Key 后可测试真实模型", "not_configured")
    started = time.time()
    text = chat([
        {"role": "system", "content": "你是连接测试助手。只回复 OK。"},
        {"role": "user", "content": "ping"},
    ], max_tokens=16, temperature=0.0)
    return {"ok": True, "latency_ms": int((time.time() - started) * 1000),
            "model": model_name(True), "reply": text[:80]}


def _scan_json_value(text: str, start: int) -> tuple[int | None, list[str], bool]:
    """从 start 起做括号配平，跳过字符串内的括号。
    返回 (完整值的结束位置或 None, 结束时仍未闭合的括号栈, 是否停在字符串里)。"""
    pairs = {"{": "}", "[": "]"}
    stack: list[str] = []
    in_str = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in pairs:
            stack.append(pairs[ch])
        elif ch in "}]":
            if stack and ch == stack[-1]:
                stack.pop()
                if not stack:
                    return i + 1, stack, in_str
                continue
            # 混进来的闭合符（模型写坏了结构）：就地截断，让后面的修复逻辑处理
            return i + 1, stack, in_str
    return None, stack, in_str


def _complete_truncated(text: str, start: int) -> str:
    """模型被 max_tokens 截断时，补齐引号与括号再试一次。"""
    end, stack, in_str = _scan_json_value(text, start)
    if end is not None:
        return text[start:end]
    fragment = text[start:]
    if in_str:
        fragment = fragment.rstrip()
        if fragment.endswith("\\"):
            fragment = fragment[:-1]
        fragment += '"'
    closing = "".join(reversed(stack))
    if not closing and not in_str:
        return fragment
    return fragment + closing


def parse_json_loose(text: str) -> Any:
    text = (text or "").strip()
    if not text:
        raise LLMError("模型返回空内容", "无法解析 JSON", "empty_content")
    fenced = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", text, re.I)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        raise LLMError("模型没有返回 JSON", text[:800], "json")
    start = min(starts)
    attempts: list[str] = []
    end, _stack, _in_str = _scan_json_value(text, start)
    if end is not None:
        attempts.append(text[start:end])
    attempts.append(_complete_truncated(text, start))
    last = max(text.rfind("}"), text.rfind("]"))
    if last > start:
        attempts.append(text[start:last + 1])
    seen: set[str] = set()
    errors: list[str] = []
    for candidate in attempts:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        for variant in (candidate, re.sub(r",\s*([}\]])", r"\1", candidate)):
            try:
                return json.loads(variant)
            except ValueError as exc:
                errors.append(str(exc)[:120])
    raise LLMError("模型返回的 JSON 无法解析", f"{'; '.join(errors[:3])}；{text[:800]}", "json")


_JUNK_LITERALS = {"", "undefined", "null", "none", "nan", "nil", "n/a", "无", "未知",
                  "没有", "...", "…", "未知内容"}


def _reject_junk(value: Any) -> Any:
    """模型偶尔会回一个占位字符串；把它当失败，而不是写进卡片/笔记。"""
    if isinstance(value, str):
        cleaned = value.strip().strip("\"'“”").lower()
        if cleaned in _JUNK_LITERALS:
            raise LLMError("模型只返回了占位内容", value[:200], "junk_output")
    return value


_cache_stats = {"calls": 0, "hits": 0, "writes": 0}
CACHE_MAX_ENTRIES = 800


def cache_dir():
    return config.DATA_DIR / "llm_cache"


def cache_key(model: str, messages: list[dict[str, Any]], temperature: float,
              max_tokens: int | None, endpoint: str = "") -> str:
    # endpoint 也在键里：同名模型接到另一家网关时，不该复用上一家的输出
    blob = json.dumps({"v": 2, "endpoint": endpoint, "model": model, "messages": messages,
                       "temperature": temperature, "max_tokens": max_tokens},
                      ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def cache_stats() -> dict[str, int]:
    return dict(_cache_stats)


def cache_size() -> int:
    directory = cache_dir()
    if not directory.exists():
        return 0
    return sum(1 for p in directory.glob("*.json"))


def clear_cache() -> int:
    directory = cache_dir()
    removed = 0
    if directory.exists():
        for path in directory.glob("*.json"):
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    _cache_stats["hits"] = 0
    _cache_stats["writes"] = 0
    return removed


def _cache_read(key: str) -> str | None:
    data = read_json(cache_dir() / f"{key}.json", None)
    if isinstance(data, dict) and isinstance(data.get("text"), str):
        return data["text"]
    return None


def _cache_write(key: str, text: str, model: str) -> None:
    directory = cache_dir()
    directory.mkdir(parents=True, exist_ok=True)
    write_json_atomic(directory / f"{key}.json",
                      {"key": key, "model": model, "created_at": now_iso(), "text": text})
    _cache_stats["writes"] += 1
    prune_cache()


def prune_cache() -> int:
    """缓存只增不减会一直吃盘：超过上限就按 mtime 淘汰到九成，避免每次写都搬家。"""
    directory = cache_dir()
    if not directory.exists():
        return 0
    files = list(directory.glob("*.json"))
    if len(files) <= CACHE_MAX_ENTRIES:
        return 0
    files.sort(key=lambda p: (p.stat().st_mtime, p.name))
    keep = int(CACHE_MAX_ENTRIES * 0.9)
    removed = 0
    for path in files[:len(files) - keep]:
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def _cache_drop(key: str) -> None:
    """坏缓存要能自愈：解析不了的条目留在盘上，同一个请求就永久失败。"""
    try:
        (cache_dir() / f"{key}.json").unlink(missing_ok=True)
    except OSError:
        pass


def chat_json(messages: list[dict[str, Any]], model: str | None = None,
              temperature: float = 0.2, max_tokens: int | None = None) -> Any:
    """结构化调用：内容寻址缓存，同样的输入第二次零成本、零漂移。

    只缓存 chat_json：它的提示词是稳定材料（编译/元信息/速记/解读），
    流式对话带会话状态，缓存它等于伪造回答。
    解析成功才写缓存，命中却解析不了就删条目重问——缓存不能把一次失败固化成永久失败。
    """
    cfg = get_config()
    use_cache = bool(cfg.get("cache_enabled", True)) and enabled()
    chosen_model = model or cfg["model"]
    key = (cache_key(chosen_model, messages, temperature, max_tokens,
                     str(cfg.get("base_url") or "")) if use_cache else "")
    if use_cache:
        cached = _cache_read(key)
        if cached is not None:
            _cache_stats["hits"] += 1
            try:
                return _reject_junk(parse_json_loose(cached))
            except LLMError:
                _cache_drop(key)          # 坏条目：删掉后当没命中，继续真实调用
    _cache_stats["calls"] += 1
    text = chat(messages, model=model, json_mode=True, temperature=temperature,
               max_tokens=max_tokens)
    parsed = _reject_junk(parse_json_loose(text))
    if use_cache:
        try:
            _cache_write(key, text, chosen_model)
        except OSError:
            pass  # 缓存写不下去不影响本次结果
    return parsed


def load_prompt(name: str) -> str:
    path = config.PROMPTS_DIR / name
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""
