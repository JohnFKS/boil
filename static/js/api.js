/* Boil API client: REST + SSE + download. */
const TOKEN_KEY = "boil_token";
let unauthorizedHandler = null;
let activeStream = null;

export class ApiError extends Error {
  constructor(message, { status = 0, code = "error", detail = "" } = {}) {
    super(message);
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

export function setToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || "";
}

export function onUnauthorized(fn) {
  unauthorizedHandler = fn;
}

function headers(extra = {}) {
  const h = { ...extra };
  const token = getToken();
  if (token) h.Authorization = `Bearer ${token}`;
  return h;
}

async function parseError(resp) {
  let payload = null;
  try {
    payload = await resp.json();
  } catch {
    try {
      const text = await resp.text();
      return new ApiError(text.slice(0, 200) || `HTTP ${resp.status}`, { status: resp.status });
    } catch {
      return new ApiError(`HTTP ${resp.status}`, { status: resp.status });
    }
  }
  const err = payload?.error || {};
  return new ApiError(err.message || `HTTP ${resp.status}`, {
    status: resp.status,
    code: err.code || "http_error",
    detail: typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail || ""),
  });
}

export async function request(path, { method = "GET", body = null, timeout = 90000 } = {}) {
  const opts = {
    method,
    headers: headers(body != null ? { "Content-Type": "application/json" } : {}),
  };
  if (body != null) opts.body = JSON.stringify(body);
  const controller = new AbortController();
  opts.signal = controller.signal;
  const timer = setTimeout(() => controller.abort(), Math.max(1000, timeout));
  let resp;
  try {
    resp = await fetch(path, opts);
  } catch (e) {
    if (e.name === "AbortError") {
      throw new ApiError("请求超时，请检查 Boil 服务是否仍在运行", { code: "timeout" });
    }
    throw new ApiError("无法连接 Boil 服务", { code: "network", detail: String(e) });
  } finally {
    clearTimeout(timer);
  }
  if (resp.status === 401) {
    const err = await parseError(resp);
    if (unauthorizedHandler) unauthorizedHandler(err);
    throw err;
  }
  if (!resp.ok) throw await parseError(resp);
  const payload = await resp.json();
  if (payload && payload.ok === false) {
    const err = payload.error || {};
    throw new ApiError(err.message || "请求失败", {
      code: err.code,
      detail: typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail || ""),
    });
  }
  return payload?.data ?? payload;
}

export const get = (path, opts = {}) => request(path, opts);
export const post = (path, body = {}, opts = {}) => request(path, { method: "POST", body, ...opts });

export async function stream(path, body, onEvent, { signal } = {}) {
  if (activeStream) activeStream.abort();
  const controller = new AbortController();
  activeStream = controller;
  if (signal) signal.addEventListener("abort", () => controller.abort(), { once: true });
  let resp;
  try {
    resp = await fetch(path, {
      method: "POST",
      headers: headers({ "Content-Type": "application/json", Accept: "text/event-stream" }),
      body: JSON.stringify(body),
      signal: controller.signal,
    });
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw new ApiError("无法连接 Boil 服务", { code: "network", detail: String(e) });
  }
  if (resp.status === 401) {
    const err = await parseError(resp);
    if (unauthorizedHandler) unauthorizedHandler(err);
    throw err;
  }
  if (!resp.ok) throw await parseError(resp);
  if (!resp.body) throw new ApiError("浏览器不支持流式响应");
  const reader = resp.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf("\n\n")) >= 0) {
        const chunk = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        let event = "message";
        const dataLines = [];
        for (const line of chunk.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
        }
        if (!dataLines.length) continue;
        const text = dataLines.join("\n");
        let data = null;
        try {
          data = JSON.parse(text);
        } catch {
          data = { text };
        }
        try {
          onEvent(event, data);
        } catch (e) {
          console.error("SSE handler error", e);
        }
        if (event === "close") return;
      }
    }
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw new ApiError("流式连接中断", { code: "stream", detail: String(e) });
  } finally {
    if (activeStream === controller) activeStream = null;
    try { reader.releaseLock(); } catch { /* noop */ }
  }
}

export function abortStream() {
  if (activeStream) activeStream.abort();
  activeStream = null;
}

export async function download(path, filename = "boil-export.json") {
  const resp = await fetch(path, { headers: headers() });
  if (!resp.ok) throw await parseError(resp);
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
