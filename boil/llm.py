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
import time
from dataclasses import dataclass
from typing import Any, Iterable, Iterator
from urllib.parse import urlsplit

from . import config
from .utils import jwt_like_mask, now_iso, read_json, write_json_atomic

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
    400: "请求被网关拒绝：常见原因是 response_format/temperature 等参数不被支持，或模型名不对。",
    401: "认证失败：检查 API Key 是否正确，以及这个服务用的是 Authorization: Bearer 还是自定义头（Dots 用 api-key 且无前缀）。",
    403: "被拒绝访问：Key 有效但无权访问该模型，或触发了服务商策略。",
    404: "地址或模型不存在：Base URL 通常要以 /v1 结尾；也可能是模型名拼错。",
    408: "网关超时：把超时调大，或换更快的模型；长思考类模型建议提高超时。",
    409: "并发冲突：稍后重试；Boil 已按指数退避重试两次。",
    413: "请求体过大：缩短材料或降低单次编译的字符数。",
    422: "参数校验失败：多为额外参数（extra_body）与网关不兼容，可清空高级参数再试。",
    429: "限流：降低频率或提高配额；Boil 已按指数退避重试。",
    500: "网关内部错误：通常是服务商抖动，稍后重试。",
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
        "extra_body": {},
    }


def _normalize_auth_prefix(header: str, prefix: Any) -> str:
    if prefix is None:
        return DEFAULT_AUTH_PREFIX
    value = str(prefix).replace("\r", "").replace("\n", "")
    if header.lower() == "authorization" and value.strip().lower() == "bearer":
        return "Bearer "
    return value


def get_config() -> dict[str, Any]:
    base = _env_config()
    stored = read_json(config.LLM_CONFIG_PATH, {}) or {}
    cfg = {**base, **{k: v for k, v in stored.items() if v is not None}}
    cfg["base_url"] = (cfg.get("base_url") or DEFAULT_BASE).rstrip("/")
    cfg["model"] = cfg.get("model") or DEFAULT_MODEL
    cfg["deep_model"] = cfg.get("deep_model") or cfg["model"]
    cfg["api_key"] = cfg.get("api_key") or ""
    cfg["temperature"] = float(cfg.get("temperature") or 0.5)
    cfg["timeout"] = int(cfg.get("timeout") or READ_IDLE)
    cfg["max_tokens"] = cfg.get("max_tokens") or None
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
    return cfg


def set_config(payload: dict[str, Any]) -> dict[str, Any]:
    cur = get_config()
    out = {
        "base_url": (payload.get("base_url") or cur["base_url"]).rstrip("/"),
        "model": (payload.get("model") or cur["model"]).strip(),
        "deep_model": (payload.get("deep_model") or "").strip(),
        "temperature": payload.get("temperature", cur["temperature"]),
        "max_tokens": payload.get("max_tokens", cur["max_tokens"]),
        "timeout": payload.get("timeout", cur["timeout"]),
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
    return bool(get_config().get("api_key"))


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


def _apply_extra_body(payload: dict[str, Any], cfg: dict[str, Any]) -> None:
    for key, value in (cfg.get("extra_body") or {}).items():
        if key not in RESERVED_PAYLOAD_KEYS and key not in payload:
            payload[key] = value


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


def _http_post_json(url: str, headers: dict[str, str], payload: dict[str, Any],
                    timeout: int) -> tuple[int, bytes]:
    import urllib.error
    import urllib.request
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
        kind, hint = _classify_exception(exc)
        raise LLMError("无法连接模型服务", f"{type(exc).__name__}: {exc}", kind, hint) from exc


def _http_post_stream(url: str, headers: dict[str, str], payload: dict[str, Any],
                      timeout: int) -> tuple[int, bytes]:
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
                return resp.status, raw      # 网关不认 stream，照旧返回 JSON
            pieces: list[str] = []
            finish = ""
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
                                    "finish_reason": finish}]}
            return resp.status, json.dumps(rebuilt, ensure_ascii=False).encode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
        kind, hint = _classify_exception(exc)
        raise LLMError("无法连接模型服务", f"{type(exc).__name__}: {exc}", kind, hint) from exc


def chat(messages: list[dict[str, Any]], model: str | None = None,
         json_mode: bool = False, temperature: float | None = None,
         max_tokens: int | None = None, timeout: int | None = None) -> str:
    if not enabled():
        raise LLMError("未配置模型，当前为演示模式", "请在设置中填写 Base URL 与 API Key", "not_configured")
    cfg = get_config()
    payload = {
        "model": model or cfg["model"],
        "messages": messages,
        "temperature": cfg["temperature"] if temperature is None else float(temperature),
        "stream": False,
    }
    tokens = max_tokens if max_tokens is not None else cfg.get("max_tokens")
    if tokens:
        payload["max_tokens"] = int(tokens)
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    _apply_extra_body(payload, cfg)
    headers = _request_headers(cfg, "application/json")
    url = _base_url()
    timeout_s = int(timeout or cfg["timeout"])
    last_exc: LLMError | None = None
    poster = _http_post_stream if cfg.get("stream_upstream", True) else _http_post_json
    for attempt in range(MAX_RETRIES + 1):
        try:
            status, raw = poster(url, headers, payload, timeout_s)
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
            hint = _http_hint(status)
            exc = LLMError(f"模型服务返回 HTTP {status}", detail, "http_error", hint)
            last_exc = exc
            if _should_retry(status) and attempt < MAX_RETRIES:
                time.sleep(0.8 * (2 ** attempt))
                continue
            _note_error(exc.message, exc.detail, exc.code, exc.hint)
            raise exc
        try:
            data = json.loads(raw.decode("utf-8"))
            text, finish = _extract_message(data)
            if finish == "length" and attempt < MAX_RETRIES and payload.get("max_tokens"):
                # 被 max_tokens 截断：加大额度重来，而不是把半截 JSON 交下去
                current = int(payload["max_tokens"])
                payload["max_tokens"] = min(max(current * 4, current + 800), 32000)
                continue
            _clear_error()
            return text
        except (ValueError, LLMError) as exc:
            err = exc if isinstance(exc, LLMError) else LLMError("模型返回不是合法 JSON", str(exc)[:500])
            last_exc = err
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
                timeout: int | None = None) -> Iterator[str]:
    if not enabled():
        raise LLMError("未配置模型，当前为演示模式", "请在设置中填写 Base URL 与 API Key", "not_configured")
    cfg = get_config()
    payload = {
        "model": model or cfg["model"],
        "messages": messages,
        "temperature": cfg["temperature"] if temperature is None else float(temperature),
        "stream": True,
    }
    tokens = max_tokens if max_tokens is not None else cfg.get("max_tokens")
    if tokens:
        payload["max_tokens"] = int(tokens)
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
    produced = False
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
                yield piece
        if not produced:
            exc = LLMError("模型流式返回空正文", "服务端没有返回任何 content 分片", "empty_content")
            _note_error(exc.message, exc.detail, exc.code, exc.hint)
            raise exc
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
