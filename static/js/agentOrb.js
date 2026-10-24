/* 全局 agent 悬浮球：能问系统、能代为操作界面，但每一步都要人点「执行」。
 *
 * 权限模型写在这里而不是提示词里：
 *   - 服务端只返回「提议」，这里才是唯一会真的发请求的地方；
 *   - risk=read/write 需要点一次「执行」；risk=danger 必须亲手输入确认词，按钮在那之前是禁用的；
 *   - 动作卡片上写清 method + path + 为什么，用户看得见自己被带着去哪儿；
 *   - 执行结果会回传给服务端继续下一轮（最多 max_turns 轮），但"继续"也只是再提议，不是自动执行。
 */
import { escapeHTML, escapeAttr, icon, overlayOpen } from "./utils.js";

const RISK_LABEL = { read: "只读", write: "会改数据", danger: "危险" };
const COUNT_LABEL = {
  cards: "张卡", units: "个单元", nodes: "个单元", edges: "条关系", notes: "条灵感", events: "条事件",
  releases: "个版本", messages: "条消息", replies: "条回答", mnemonics: "条口诀", records: "条记录",
  capabilities: "项能力", duplicates: "对重复", recalls: "条回忆", absorbed: "个被吸收单元",
  related: "个相关单元", phases: "个阶段", warnings: "条提醒", questions: "道题", gaps: "个缺口",
  recent_sessions: "个会话", forecast: "天到期", retention: "天记录",
};
const QUICK = [
  "今天该学什么",
  "我还有几张卡到期",
  "记忆健康度是怎么算的",
  "合并单元会不会丢内容",
  "这个版本更新了什么",
];

let mounted = null;
let caps = null;
let midSeq = 0;
const nextMid = () => `m${++midSeq}`;

function el(html) {
  const wrap = document.createElement("div");
  wrap.innerHTML = html.trim();
  return wrap.firstElementChild;
}

function argSummary(action) {
  return Object.entries(action.args || {})
    .map(([k, v]) => `${k}=${typeof v === "string" && v.length > 22 ? `${v.slice(0, 22)}…` : String(v)}`)
    .join(" ");
}

function fillPath(action) {
  let path = action.path || "";
  for (const name of action.path_args || []) {
    path = path.replace(`{${name}}`, encodeURIComponent(String((action.args || {})[name] ?? "")));
  }
  return path;
}

function messageHTML(msg) {
  if (msg.role === "user") {
    return `<div class="agent-msg user"><div class="agent-bubble">${escapeHTML(msg.text)}</div></div>`;
  }
  const mid = escapeAttr(msg.mid);
  // 每条回答自己带引擎标签：面板顶部那个会在滚动上去之后看不见，而"这话是谁说的"必须一直看得见
  const badges = [];
  if (msg.engine === "model") badges.push('<span class="badge badge-teal" data-agent-engine="model">真实模型</span>');
  else if (msg.engine === "rules") badges.push('<span class="badge badge-gold" data-agent-engine="rules">本地规则</span>');
  else if (msg.engine === "error") badges.push('<span class="badge badge-ember" data-agent-engine="error">出错了</span>');
  const actions = (msg.actions || []).map((a, i) => `
    <div class="agent-action ${a.state || ""}" data-idx="${i}">
      <div class="agent-action-head">
        <span class="agent-risk ${escapeAttr(a.risk)}">${escapeHTML(RISK_LABEL[a.risk] || a.risk)}</span>
        <strong>${escapeHTML(a.desc || a.id)}</strong>
      </div>
      <code class="agent-path">${escapeHTML(a.kind === "api" ? `${a.method} ${fillPath(a)}` : `界面：${a.action}`)}${argSummary(a) ? ` ${escapeHTML(argSummary(a))}` : ""}</code>
      ${a.why ? `<p class="agent-why">为什么：${escapeHTML(a.why)}</p>` : `<p class="agent-why faint">它没说为什么</p>`}
      ${a.risk === "danger" ? `
        <div class="agent-confirm">
          <input class="input" type="text" inputmode="latin" autocomplete="off"
                 placeholder="这一步会毁掉数据，亲手输入 RESET 才能执行" data-confirm-for="${i}">
        </div>` : ""}
      <div class="agent-action-foot">
        <button class="btn ${a.risk === "danger" ? "btn-danger" : "btn-primary"} btn-sm" data-run="${i}"
                ${a.risk === "danger" ? "disabled" : ""}>${icon("play", 13)} 执行</button>
        <button class="btn btn-ghost btn-sm" data-skip="${i}">不要</button>
        <span class="agent-result ${a.state === "ok" ? "ok" : a.state === "fail" ? "fail" : ""}">${escapeHTML(a.result || "")}</span>
      </div>
    </div>`).join("");
  const rejected = (msg.rejected || []).map(r => `
    <div class="agent-rejected">${icon("shield", 12)} <span>没排进去的动作 <code>${escapeHTML(r.id || "?")}</code>：${escapeHTML(r.reason || "")}</span></div>`).join("");
  const cites = (msg.citations || []).length ? `
    <details class="agent-cites"${(msg.citations || []).length === 1 ? " open" : ""}>
      <summary>依据 ${(msg.citations || []).length} 处文档</summary>
      ${msg.citations.map(c => `<div class="agent-cite">${escapeHTML(c)}</div>`).join("")}
    </details>` : "";
  return `
    <div class="agent-msg assistant" data-mid="${mid}">
      <div class="agent-bubble">${escapeHTML(msg.text || "")}${cites}</div>
      ${badges.length ? `<div class="agent-marks">${badges.join("")}</div>` : ""}
      ${actions ? `<div class="agent-actions">${actions}</div>` : ""}
      ${rejected}
      ${msg.canContinue ? '<p class="agent-continues">执行完这一步我会根据结果再提议下一步（最多问几轮就有上限）。</p>' : ""}
    </div>`;
}

export function initAgentOrb(ctx) {
  if (mounted) return mounted;

  const orb = el(`
    <div class="agent-layer">
      <button class="agent-orb" id="agent-orb" type="button" aria-label="打开系统助手" aria-expanded="false">
        <span class="agent-orb-core">${icon("spark", 20)}</span>
        <span class="agent-orb-pulse"></span>
      </button>
      <section class="agent-panel" id="agent-panel" role="dialog" aria-label="系统助手" hidden>
        <header class="agent-head">
          <span class="agent-title">${icon("spark", 15)} 系统助手</span>
          <span class="agent-engine badge badge-muted" id="agent-engine">连接中…</span>
          <button class="icon-btn small" id="agent-clear" type="button" aria-label="清空对话">${icon("refresh", 14)}</button>
          <button class="icon-btn small" id="agent-close" type="button" aria-label="关闭">${icon("close", 15)}</button>
        </header>
        <div class="agent-log" id="agent-log"></div>
        <div class="agent-quick" id="agent-quick">
          ${QUICK.map(q => `<button type="button" class="agent-chip" data-q="${escapeAttr(q)}">${escapeHTML(q)}</button>`).join("")}
        </div>
        <footer class="agent-foot">
          <textarea id="agent-input" rows="1" placeholder="问它任何关于这个系统的事，或者让它带你去做…" aria-label="给助手"></textarea>
          <button class="send-btn" id="agent-send" type="button" aria-label="发送">${icon("send", 16)}</button>
        </footer>
        <p class="agent-note">它只提议，不替你决定：每个动作都要你点「执行」；清空数据还要你亲手输确认词。</p>
      </section>
    </div>`);
  document.body.appendChild(orb);
  mounted = orb;

  const panel = orb.querySelector("#agent-panel");
  const ball = orb.querySelector("#agent-orb");
  const log = orb.querySelector("#agent-log");
  const quick = orb.querySelector("#agent-quick");
  const input = orb.querySelector("#agent-input");
  const send = orb.querySelector("#agent-send");
  const engineBadge = orb.querySelector("#agent-engine");
  const messages = [];
  let busy = false;
  let turn = 0;

  function paint() {
    log.innerHTML = messages.map(messageHTML).join("")
      || `<div class="agent-hello">
            <div class="agent-hello-mark">${icon("flame", 26)}</div>
            <p>我是这个系统自己的助手。可以问我怎么用、某个指标是什么口径，也可以让我带你去某个页面、把今天的复习列出来。</p>
            <p class="faint tiny">回答里带「本地规则」时说明没配模型，那时它只按关键词理解你。</p>
          </div>`;
    log.scrollTop = log.scrollHeight;
    quick.style.display = messages.length ? "none" : "flex";
  }

  function open(wantFocus = true) {
    panel.hidden = false;
    ball.classList.add("active");
    ball.setAttribute("aria-expanded", "true");
    if (wantFocus) setTimeout(() => input.focus(), 40);
  }

  function close() {
    panel.hidden = true;
    ball.classList.remove("active");
    ball.setAttribute("aria-expanded", "false");
  }

  ball.addEventListener("click", () => (panel.hidden ? open() : close()));
  orb.querySelector("#agent-close").addEventListener("click", close);
  orb.querySelector("#agent-clear").addEventListener("click", () => {
    messages.length = 0;
    turn = 0;
    paint();
  });
  quick.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-q]");
    if (btn) { input.value = btn.dataset.q; ask(); }
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); ask(); }
    e.stopPropagation();                       // 别让全局的「/」和空格快捷键抢走输入
  });
  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = `${Math.min(120, input.scrollHeight)}px`;
  });
  send.addEventListener("click", ask);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !panel.hidden) { close(); }
  });

  async function loadCaps() {
    try {
      caps = await ctx.api.get("/api/agent/capabilities", { timeout: 10000 });
      engineBadge.textContent = caps.engine === "live" ? "真实模型" : "本地规则";
      engineBadge.className = `agent-engine badge ${caps.engine === "live" ? "badge-teal" : "badge-gold"}`;
    } catch {
      engineBadge.textContent = "离线";
    }
  }
  loadCaps();

  function historyForModel() {
    return messages.slice(-6).map(m => ({
      role: m.role === "user" ? "user" : "assistant",
      content: m.role === "user" ? m.text : `${m.text || ""}\n已提议动作：${(m.actions || []).map(a => a.id).join("、") || "无"}`,
    }));
  }

  async function ask() {
    const text = input.value.trim();
    if (!text || busy) return;
    input.value = "";
    input.style.height = "auto";
    messages.push({ role: "user", text, mid: nextMid() });
    turn = 1;
    paint();
    await runTurn(text, []);
  }

  async function runTurn(question, observations) {
    busy = true;
    send.disabled = true;
    const thinking = { role: "assistant", text: "正在想…", engine: null, actions: [], mid: nextMid() };
    messages.push(thinking);
    paint();
    try {
      const res = await ctx.api.post("/api/agent/plan", {
        question, history: historyForModel(), observations, turn,
      }, { timeout: 180000 });
      thinking.text = res.reply || "";
      thinking.engine = res.engine;
      thinking.citations = res.citations || [];
      thinking.rejected = res.rejected || [];
      thinking.canContinue = !!res.can_continue && !observations.length;
      thinking.actions = (res.actions || []).map(a => ({ ...a, state: "idle", result: "" }));
      if (observations.length) {
        // 第二轮之后模型常会没话找话，保留它说的但别覆盖我们已经展示的结果
        thinking.text = thinking.text || "结果就在上面，需要我继续吗？";
      }
    } catch (err) {
      thinking.text = err.message || "助手没答上来";
      thinking.engine = "error";
      thinking.actions = [];
    } finally {
      busy = false;
      send.disabled = false;
      paint();
    }
  }

  function findMessageIndex(action) {
    return messages.findIndex(m => (m.actions || []).includes(action));
  }

  async function execute(msgIdx, idx) {
    const msg = messages[msgIdx];
    const action = msg.actions[idx];
    if (!action || action.state === "running") return;
    if (action.risk === "danger") {
      // 按消息定位：不同回答里都可能有第 0 号动作，全局查会拿错输入框
      const field = log.querySelector(`[data-mid="${msg.mid}"] [data-confirm-for="${idx}"]`);
      if (!field || field.value.trim() !== "RESET") {
        ctx.toast("危险动作要亲手输入确认词才会执行。", "error", 3600);
        return;
      }
      action.args = { ...action.args, confirm: "RESET" };
    }
    action.state = "running";
    action.result = "执行中…";
    paint();
    let observation = { id: action.id, ok: false, summary: "" };
    try {
      const payload = await perform(action);
      action.state = "ok";
      action.result = payload.summary;
      observation = { id: action.id, ok: true, summary: payload.summary, data: payload.brief };
      ctx.toast(`${action.desc || action.id}：${payload.summary}`, "success", 4200);
    } catch (err) {
      action.state = "fail";
      action.result = err.message || "执行失败";
      observation = { id: action.id, ok: false, summary: action.result };
      ctx.toast(`${action.desc || action.id} 失败：${action.result}`, "error", 5200);
    }
    action.observation = observation;
    paint();
    maybeContinue(msg);
  }

  function brief(value) {
    if (value == null) return null;
    if (Array.isArray(value)) return { count: value.length, sample: value.slice(0, 3) };
    if (typeof value === "object") {
      const out = {};
      for (const [k, v] of Object.entries(value)) {
        if (["api_key", "key", "token", "authorization"].includes(k)) continue;
        out[k] = Array.isArray(v) ? { count: v.length } : (typeof v === "object" ? "{…}" : v);
      }
      return out;
    }
    return value;
  }

  async function perform(action) {
    if (action.kind === "ui") {
      if (action.action === "navigate") {
        const routes = { today: "#/dashboard", dashboard: "#/dashboard", map: "#/map", review: "#/review",
                         library: "#/library", insights: "#/insights", notes: "#/notes",
                         settings: "#/settings", onboarding: "#/onboarding" };
        const href = routes[action.args.route] || "#/dashboard";
        ctx.navigate(href);
        close();
        return { summary: `已经带你到 ${href.replace("#/", "")}`, brief: { route: href } };
      }
      if (action.action === "open_unit") {
        ctx.navigate(`#/unit/${encodeURIComponent(action.args.unit_id || "")}`);
        close();
        return { summary: "打开了这个单元", brief: { unit_id: action.args.unit_id } };
      }
      if (action.action === "open_search") {
        ctx.openSearch?.(action.args.q || "");
        return { summary: "搜索框已打开", brief: { q: action.args.q || "" } };
      }
      return { summary: "这个界面动作我不认识", brief: null };
    }
    const path = fillPath(action);
    if (action.method === "DOWNLOAD") {
      await ctx.api.download(path, (action.args && action.args.filename) || "boil-export.json");
      return { summary: "文件已开始下载", brief: { path } };
    }
    if (action.method === "GET") {
      const data = await ctx.api.get(path, { timeout: 60000 });
      return { summary: describe(data), brief: brief(data) };
    }
    const body = {};
    for (const [k, v] of Object.entries(action.args || {})) {
      if (!path.includes(`{${k}}`)) body[k] = v;
    }
    const data = await ctx.api.post(path, body, { timeout: 180000 });
    return { summary: describe(data), brief: brief(data) };
  }

function describe(data) {
    if (data == null) return "完成";
    if (typeof data !== "object") return String(data).slice(0, 40);
    const bits = [];
    const counts = data.counts;
    if (counts && typeof counts === "object") {
      bits.push(`${counts.boiled ?? 0}/${counts.units ?? 0} 已烧开`, `${counts.due ?? 0} 张到期`);
    }
    if (data.session && data.session.minutes) bits.push(`${data.session.minutes} 分钟番茄钟`);
    if (data.result && data.result.interval_label) bits.push(`下次 ${data.result.interval_label}`);
    if (data.version) bits.push(`v${data.version}`);
    if (data.reset) bits.push("学习数据已清空");
    for (const [k, v] of Object.entries(data)) {
      if (Array.isArray(v) && COUNT_LABEL[k]) bits.push(`${v.length} ${COUNT_LABEL[k]}`);
    }
    return bits.length ? bits.join(" · ") : "已完成，结果就在界面上";
  }

  function maybeContinue(msg) {
    if (!msg.canContinue) return;
    const pending = msg.actions.filter(a => a.state === "idle");
    if (pending.length) return;                       // 还有没表态的动作，先不追问
    const done = msg.actions.filter(a => a.state === "ok" || a.state === "fail");
    if (!done.length || turn >= (caps?.max_turns || 4)) return;
    turn += 1;
    msg.canContinue = false;
    runTurn("", done.map(a => a.observation).filter(Boolean));
  }

  log.addEventListener("click", (e) => {
    const runBtn = e.target.closest("[data-run]");
    const skipBtn = e.target.closest("[data-skip]");
    const msgNode = e.target.closest(".agent-msg.assistant");
    if (!msgNode) return;
    const msg = messages.find(m => m.mid === msgNode.dataset.mid);
    if (!msg) return;
    if (runBtn) { execute(messages.indexOf(msg), Number(runBtn.dataset.run)); return; }
    if (skipBtn) {
      const action = msg.actions[Number(skipBtn.dataset.skip)];
      if (action) { action.state = "skipped"; action.result = "你选了不做"; paint(); maybeContinue(msg); }
    }
  });
  log.addEventListener("keydown", (e) => {
    if (e.target.matches("[data-confirm-for]")) e.stopPropagation();
  });
  // 危险动作的按钮默认锁死，亲手输对确认词才解锁 —— 少这一步，它永远点不动
  log.addEventListener("input", (e) => {
    const field = e.target.closest("[data-confirm-for]");
    if (!field) return;
    const idx = field.dataset.confirmFor;
    const btn = field.closest(".agent-action")?.querySelector(`[data-run="${idx}"]`);
    if (btn) btn.disabled = field.value.trim() !== "RESET";
  });

  const api = {
    open,
    close,
    async ask(text) { input.value = text; open(false); await ask(); return messages[messages.length - 1]; },
    info() {
      return {
        messages: messages.length,
        busy,
        turn,
        pending: messages.flatMap(m => m.actions || []).filter(a => a.state === "idle").length,
      };
    },
    // 浏览器测试专用：直接投喂一份「服务端计划结果」，用来验证危险动作的闸门
    // （真实模型对「清空数据」的回答每次都不一样，不能拿来断言）。
    propose(plan) {
      const msg = {
        role: "assistant", text: plan.reply || "", engine: plan.engine || "model",
        citations: plan.citations || [], rejected: plan.rejected || [],
        canContinue: false, mid: nextMid(),
        actions: (plan.actions || []).map(a => ({ ...validateLocal(a), state: "idle", result: "" })),
      };
      messages.push(msg);
      open(false);
      paint();
      return msg.actions.map(a => ({ id: a.id, ok: !!a.ok, risk: a.risk }));
    },
  };
  window.__boilAgent = api;
  return api;
}

/** 前端再校一遍：服务端已经过滤过，这里挡的是"有人直接改了响应"。 */
function validateLocal(raw) {
  const known = raw && raw.ok !== false && raw.id && raw.risk && raw.desc;
  if (known) return raw;
  return { id: (raw && raw.id) || "?", ok: false, risk: "read", desc: "（不在能力清单里）", args: {} };
}

export function agentOverlayOpen() {
  return !!document.querySelector(".agent-panel:not([hidden])");
}
