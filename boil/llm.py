"""零依赖 OpenAI 兼容 LLM 网关。

- 配置存 data/llm.json（权限 0600），API Key 永不返回给前端；
- 支持普通与流式（SSE）调用、JSON 模式、宽松 JSON 修复、失败重试；
- 失败必须可见：last_error 会出现在设置页和 /api/health；
- 未配置 Key 时由各业务层走本地确定性引擎，不在这里偷偷伪造模型回答。
"""
from __future__ import annotations

import http.client
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
READ_IDLE = 120
CONNECT_TIMEOUT = 20
MAX_RETRIES = 2

PROVIDERS = [
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
    def __init__(self, message: str, detail: str = "", code: str = "llm_error"):
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.code = code

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "detail": self.detail}


def _env_config() -> dict[str, Any]:
    return {
        "base_url": os.environ.get("BOIL_LLM_BASE_URL", DEFAULT_BASE),
        "api_key": os.environ.get("BOIL_LLM_KEY", ""),
        "model": os.environ.get("BOIL_LLM_MODEL", DEFAULT_MODEL),
        "deep_model": os.environ.get("BOIL_LLM_DEEP_MODEL", ""),
        "temperature": 0.5,
        "max_tokens": None,
        "timeout": READ_IDLE,
    }


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


def _note_error(message: str, detail: str = "", code: str = "llm_error") -> None:
    write_json_atomic(config.LLM_STATE_PATH, {
        "message": message[:300],
        "detail": detail[:1200],
        "code": code,
        "at": now_iso(),
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
        "providers": PROVIDERS,
        "last_error": err,
        "forced_off": config.LLM_OFF,
    }


def _extract_content(data: dict[str, Any]) -> str:
    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError("模型返回结构异常", json.dumps(data, ensure_ascii=False)[:800]) from exc
    content = msg.get("content")
    if isinstance(content, list):
        content = "".join(str(x.get("text", "")) for x in content if isinstance(x, dict))
    if not isinstance(content, str) or not content.strip():
        reasoning = msg.get("reasoning_content") or ""
        detail = "模型返回空正文"
        if reasoning:
            detail += "（只有推理内容；请换模型或关闭 reasoning）"
        raise LLMError(detail, json.dumps(data, ensure_ascii=False)[:800], "empty_content")
    return content.strip()


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
        raise LLMError("无法连接模型服务", f"{type(exc).__name__}: {exc}", "network") from exc


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
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": "Bearer " + cfg["api_key"],
        "User-Agent": "Boil/1.0 (local-first learning os)",
    }
    url = _base_url()
    timeout_s = int(timeout or cfg["timeout"])
    last_exc: LLMError | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            status, raw = _http_post_json(url, headers, payload, timeout_s)
        except LLMError as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                time.sleep(0.7 * (attempt + 1))
                continue
            _note_error(exc.message, exc.detail, exc.code)
            raise
        if status == 400 and json_mode:
            detail = raw[:800].decode("utf-8", "replace")
            if "response_format" in detail or "json" in detail.lower():
                payload.pop("response_format", None)
                json_mode = False
                continue
        if status != 200:
            detail = raw[:1200].decode("utf-8", "replace")
            exc = LLMError(f"模型服务返回 HTTP {status}", detail, "http_error")
            last_exc = exc
            if _should_retry(status) and attempt < MAX_RETRIES:
                time.sleep(0.8 * (2 ** attempt))
                continue
            _note_error(exc.message, detail, exc.code)
            raise exc
        try:
            data = json.loads(raw.decode("utf-8"))
            text = _extract_content(data)
            _clear_error()
            return text
        except (ValueError, LLMError) as exc:
            err = exc if isinstance(exc, LLMError) else LLMError("模型返回不是合法 JSON", str(exc)[:500])
            last_exc = err
            if attempt < MAX_RETRIES:
                time.sleep(0.5)
                continue
            _note_error(err.message, err.detail, err.code)
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
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "Authorization": "Bearer " + cfg["api_key"],
        "User-Agent": "Boil/1.0 (local-first learning os)",
        "Connection": "close",
    }
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
            detail = resp.read(1600).decode("utf-8", "replace")
            exc = LLMError(f"模型服务返回 HTTP {resp.status}", detail, "http_error")
            _note_error(exc.message, detail, exc.code)
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
            _note_error(exc.message, exc.detail, exc.code)
            raise exc
        _clear_error()
    except LLMError:
        raise
    except (http.client.HTTPException, socket.timeout, TimeoutError, OSError) as exc:
        err = LLMError("流式连接中断", f"{type(exc).__name__}: {exc}", "stream")
        _note_error(err.message, err.detail, err.code)
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
    end = max(text.rfind("}"), text.rfind("]"))
    if end <= start:
        raise LLMError("模型返回的 JSON 不完整", text[:800], "json")
    candidate = text[start:end + 1]
    try:
        return json.loads(candidate)
    except ValueError:
        repaired = re.sub(r",\s*([}\]])", r"\1", candidate)
        try:
            return json.loads(repaired)
        except ValueError as exc:
            raise LLMError("模型返回的 JSON 无法解析", f"{exc}; {candidate[:800]}", "json") from exc


def chat_json(messages: list[dict[str, Any]], model: str | None = None,
              temperature: float = 0.2, max_tokens: int | None = None) -> Any:
    text = chat(messages, model=model, json_mode=True, temperature=temperature,
                max_tokens=max_tokens)
    return parse_json_loose(text)


def load_prompt(name: str) -> str:
    path = config.PROMPTS_DIR / name
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""
