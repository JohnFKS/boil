/* 长材料编译：提交后台任务 → 轮询进度 → 取回结果，并把详细进度/报错渲染出来。
 *
 * 一次编译会串行调用模型十几次，单次就可能两分钟。让一个 fetch 一直挂着等结果，
 * 浏览器必定先超时；服务端线程却还在算，用户再点一次又多出一个内容包。
 * 现在提交后只拿 job_id，靠短请求轮询进度；网络断了、页面刷新了也能接回同一次编译。
 */
const STORAGE_KEY = "boil.compile.job";
const POLL_MS = 1500;

const LEVEL_LABELS = { info: "信息", warn: "告警", error: "错误" };

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

function clockText(seconds) {
  const s = Math.max(0, Math.round(seconds || 0));
  return s >= 60 ? `${Math.floor(s / 60)} 分 ${s % 60} 秒` : `${s} 秒`;
}

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
  const phase = st.phase_label ? `${st.phase_label} · ` : "";
  const step = st.total > 0
    ? ` · 已完成 ${Math.min(st.current, st.total)}/${st.total} 批` : "";
  return `${phase}${st.message || "正在编译…"}${step} · 已用 ${clockText(st.elapsed)}`;
}

/** 开始页与内容库共用的进度条结构；用 data-* 而不是 id，两个视图可以各放一份。 */
export function compileProgressMarkup() {
  return `<div class="preview-box compile-progress" data-compile-progress style="display:none">
    <div class="between">
      <strong data-compile-title>正在编译材料</strong>
      <span class="badge badge-muted" data-compile-clock>0%</span>
    </div>
    <div class="progress-line"><i data-compile-bar style="width:0%"></i></div>
    <div class="between mt-2">
      <span class="hint" data-compile-msg>正在提交任务…</span>
      <button class="btn btn-ghost btn-sm" type="button" data-compile-cancel>取消编译</button>
    </div>
    <div class="compile-stats mt-2" data-compile-stats></div>
    <div class="compile-warn mt-2" data-compile-warn style="display:none"></div>
    <div class="error-box mt-2" data-compile-error style="display:none"></div>
    <details class="mt-2" open>
      <summary>详细进度 <span class="faint tiny" data-compile-count>0 条</span></summary>
      <div class="compile-events" data-compile-events></div>
    </details>
  </div>`;
}

function eventHTML(event) {
  const level = LEVEL_LABELS[event.level] ? event.level : "info";
  return `<div class="compile-event level-${level}">
    <span class="compile-event-time">+${clockText(event.elapsed)}</span>
    <span class="compile-event-level">${LEVEL_LABELS[level]}</span>
    <span class="compile-event-body">
      <span class="compile-event-phase">${esc(event.phase_label || event.phase || "")}</span>
      ${esc(event.message || "")}
      ${event.detail ? `<span class="compile-event-detail">${esc(event.detail)}</span>` : ""}
    </span>
  </div>`;
}

function errorHTML(err) {
  if (!err) return "";
  return `<strong>${esc(err.message || "编译失败")}</strong>
    ${err.phase ? `<div class="tiny">失败阶段：${esc(err.phase_label || err.phase)}</div>` : ""}
    ${err.detail ? `<div class="tiny">原因：${esc(err.detail)}</div>` : ""}
    ${err.hint ? `<div class="tiny">建议：${esc(err.hint)}</div>` : ""}
    ${err.traceback ? `<details class="mt-1"><summary>技术细节</summary><pre class="compile-trace">${esc(err.traceback)}</pre></details>` : ""}`;
}

/** 把后台返回的完整状态画进进度框：百分比、统计、告警、逐条事件、常驻错误。 */
export function renderCompileProgress(root, st) {
  if (!root || !st) return;
  const q = (name) => root.querySelector(`[data-compile-${name}]`);
  root.style.display = "";
  const percent = Number.isFinite(st.percent) ? st.percent
    : (st.total > 0 ? Math.min(100, Math.round(st.current / st.total * 100)) : 0);
  const bar = q("bar");
  if (bar) bar.style.width = `${Math.max(0, Math.min(100, percent))}%`;
  const title = q("title");
  if (title) title.textContent = st.state === "error" ? "编译失败"
    : st.state === "cancelled" ? "编译已取消"
      : st.state === "done" ? "编译完成" : "正在编译材料";
  const clock = q("clock");
  if (clock) clock.textContent = `${st.phase_label || "准备中"} · ${percent}%`;
  const msg = q("msg");
  if (msg) msg.textContent = compileProgressText(st);

  const m = st.material || {};
  const stats = st.stats || {};
  const chips = [
    `${m.documents || 0} 份文件 · ${(m.chars || 0).toLocaleString()} 字符`,
    m.use_llm ? "真实模型" : "本地编译器",
  ];
  if (st.total > 0) chips.push(`已完成 ${st.current}/${st.total} 批`);
  if (stats.errors) chips.push(`${stats.errors} 个错误`);
  if (stats.units) chips.push(`${stats.units} 个单元`);
  const statBox = q("stats");
  if (statBox) statBox.innerHTML = chips.map(c => `<span class="badge badge-muted">${esc(c)}</span>`).join(" ");

  const warnings = st.warnings || [];
  const warnBox = q("warn");
  if (warnBox) {
    warnBox.style.display = warnings.length ? "" : "none";
    warnBox.innerHTML = warnings.length
      ? `<strong>${warnings.length} 条告警</strong><ul>${warnings.slice(-8).map(
          w => `<li>${esc(w)}</li>`).join("")}</ul>`
      : "";
  }

  const errBox = q("error");
  if (errBox) {
    const err = st.state === "error" ? (st.error || { message: "编译失败" }) : null;
    errBox.style.display = err ? "" : "none";
    errBox.innerHTML = errorHTML(err);
  }

  const events = st.events || [];
  const count = q("count");
  if (count) count.textContent = `${events.length} 条`;
  const list = q("events");
  if (list) {
    list.innerHTML = events.length
      ? events.map(eventHTML).join("")
      : `<div class="compile-event level-info"><span class="compile-event-body">等待后台返回第一条进度…</span></div>`;
    list.scrollTop = list.scrollHeight;
  }
  const cancel = q("cancel");
  if (cancel) cancel.disabled = st.cancellable === false ||
    ["done", "error", "cancelled"].includes(st.state);
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
      const err = st.error || {};
      const message = err.message || "编译失败";
      const failure = new Error(err.detail ? `${message}：${err.detail}` : message);
      failure.jobStatus = st;    // 让视图把错误详情常驻在进度框里，而不是 5 秒 toast
      throw failure;
    }
    if (st.state === "cancelled") { remember(""); throw cancelled("编译已取消"); }
    await wait(POLL_MS, signal);
  }
}
