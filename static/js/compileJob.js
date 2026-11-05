/* 长材料编译：提交后台任务 → 轮询进度 → 取回结果。
 *
 * 一次编译会串行调用模型十几次，单次就可能两分钟。让一个 fetch 一直挂着等结果，
 * 浏览器必定先超时；服务端线程却还在算，用户再点一次又多出一个内容包。
 * 现在提交后只拿 job_id，靠短请求轮询进度；网络断了、页面刷新了也能接回同一次编译。
 */
const STORAGE_KEY = "boil.compile.job";
const POLL_MS = 1500;

export function savedJobId() {
  try { return sessionStorage.getItem(STORAGE_KEY) || ""; } catch { return ""; }
}

function remember(id) {
  try {
    if (id) sessionStorage.setItem(STORAGE_KEY, id);
    else sessionStorage.removeItem(STORAGE_KEY);
  } catch { /* 隐私模式下不可用：退化成只能在当前页面等待 */ }
}

function cancelled(message) {
  const err = new Error(message);
  err.cancelled = true;
  return err;
}

function wait(ms, signal) {
  return new Promise((resolve) => {
    if (signal?.aborted) return resolve();
    const onAbort = () => { clearTimeout(timer); resolve(); };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

export function compileProgressText(st) {
  const elapsed = Math.max(0, Math.round(st.elapsed || 0));
  const clock = elapsed >= 60
    ? `${Math.floor(elapsed / 60)} 分 ${elapsed % 60} 秒` : `${elapsed} 秒`;
  const step = st.total > 0 ? ` · 第 ${Math.min(st.current, st.total)}/${st.total} 批` : "";
  return `${st.message || "正在编译…"}${step} · 已用 ${clock}`;
}

export async function runCompile(api, { payload = null, resumeId = "", onProgress, signal } = {}) {
  let jobId = resumeId;
  if (!jobId) {
    const started = await api.post("/api/packs/compile",
      { ...(payload || {}), async: true }, { timeout: 30000 });
    if (started?.pack) return started;      // 旧版服务端：同步返回了结果
    jobId = started?.job_id || "";
    if (!jobId) throw new Error("服务没有返回编译任务，请重启 Boil 服务后再试");
  }
  remember(jobId);
  let failures = 0;
  for (;;) {
    if (signal?.aborted) {
      try {
        await api.post("/api/packs/compile/cancel", { job_id: jobId }, { timeout: 10000 });
      } catch { /* 任务可能刚好结束 */ }
      remember("");
      throw cancelled("编译已取消");
    }
    let st;
    try {
      st = await api.get(`/api/packs/compile/status?id=${encodeURIComponent(jobId)}`,
        { timeout: 20000 });
      failures = 0;
    } catch (err) {
      if (err?.status === 404) {
        remember("");
        throw new Error("上次的编译任务已失效（服务可能重启过），请重新点一次编译");
      }
      failures += 1;
      if (failures >= 8) {
        // 保留 jobId：服务恢复后刷新页面还能接回来
        throw new Error("暂时连不上 Boil 服务，编译仍在后台进行；稍后刷新页面可以继续");
      }
      try { onProgress?.({ state: "running", current: 0, total: 0,
                           message: `连接中断，正在重试（${failures}/8）…` }); } catch { /* noop */ }
      await wait(POLL_MS * 2, signal);
      continue;
    }
    try { onProgress?.(st); } catch { /* UI 回调出错不该打断编译 */ }
    if (st.state === "done") { remember(""); return st.result; }
    if (st.state === "error") {
      remember("");
      const detail = st.error?.detail ? `：${st.error.detail}` : "";
      throw new Error(`${st.error?.message || "编译失败"}${detail}`);
    }
    if (st.state === "cancelled") { remember(""); throw cancelled("编译已取消"); }
    await wait(POLL_MS, signal);
  }
}
