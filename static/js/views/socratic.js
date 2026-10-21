/* Socratic dialogue: streamed, one question at a time, ends with evidence. */
import {
  escapeHTML, escapeAttr, icon, pct, radarSVG, ringSVG, renderMarkdown,
  streamingText, formatDateTime, relativeTime, toast,
} from "../utils.js";

const FACET_LABELS = { recall: "回忆", explain: "解释", apply: "应用", transfer: "迁移" };
const STAGE_LABELS = {
  diagnose: "诊断起点", deepen: "追问理由", boundary: "边界与反例",
  apply: "应用迁移", integrate: "综合收束", done: "已收束",
};

function messageHTML(m, index) {
  const isUser = m.role === "user";
  return `<div class="msg ${isUser ? "user" : "assistant"}" data-msg="${index}">
    <span class="msg-avatar">${icon(isUser ? "brain" : "spark", 14)}</span>
    <div>
      <div class="msg-bubble">${isUser ? streamingText(m.content) : renderMarkdown(m.content)}</div>
      <div class="msg-meta">${isUser ? "你" : "教练"} · ${formatDateTime(m.created_at)}${m.meta?.engine === "demo" ? " · 演示引擎" : ""}</div>
    </div>
  </div>`;
}

function chatHTML(data) {
  const s = data.session, unit = data.unit;
  const messages = s.messages || [];
  const engine = s.meta?.engine || "demo";
  const report = data.report;
  return `
    <div class="page-head">
      <div>
        <a class="row small muted" href="#/unit/${escapeAttr(unit?.id || "")}" style="gap:5px">${icon("chevronLeft", 14)} 返回单元</a>
        <h2>${escapeHTML(unit?.title || "苏格拉底对话")}</h2>
        <p>一次只问一个问题。先自己回答，再要提示；我不替你完成思考。</p>
      </div>
      <div class="row wrap">
        <span class="badge badge-ember" id="stage-badge">${STAGE_LABELS[s.stage] || s.stage}</span>
        <span class="badge badge-muted" id="turn-badge">第 ${s.turn || 0} 轮</span>
        <span class="badge ${engine === "demo" ? "badge-gold" : "badge-teal"}">${engine === "demo" ? "演示引擎" : "真实模型"}</span>
        <button class="btn btn-ghost btn-sm" data-finish>${icon("check", 14)} 结束并生成报告</button>
      </div>
    </div>
    <div class="socratic-shell">
      <div class="card chat-panel">
        <div class="chat-head">
          <div class="chat-head-left">
            <div class="chat-unit">${escapeHTML(unit?.title || "")}</div>
            <div class="chat-meta">
              <span class="badge badge-muted">${icon("target", 11)} 目标：把本质、条件、边界说清</span>
              <span class="badge badge-muted">${(s.meta?.hints || 0)} 次提示</span>
            </div>
          </div>
          <div class="row">
            <button class="btn btn-ghost btn-sm" data-hint>${icon("hint", 14)} 给点提示</button>
          </div>
        </div>
        <div class="chat-body" id="chat-body">
          ${messages.map(messageHTML).join("")}
          <div id="stream-anchor"></div>
        </div>
        <div class="chat-composer">
          <div class="composer">
            <textarea id="chat-input" rows="1" placeholder="先回答，再解释为什么…" aria-label="你的回答"></textarea>
            <button class="send-btn" id="send-btn" aria-label="发送">${icon("send", 16)}</button>
          </div>
          <div class="composer-tools">
            <div class="hint-chips">
              <button class="hint-chip" data-quick="我有点卡住了，给我一个最小提示，不要直接给答案。">我卡住了</button>
              <button class="hint-chip" data-quick="我用一个自己的例子来说明，你看这个理解对不对：">举个例子</button>
              <button class="hint-chip" data-quick="我想先试着自己总结一遍，再请你指出漏洞。">我先总结</button>
            </div>
            <span class="faint tiny">Enter 发送 · Shift+Enter 换行</span>
          </div>
        </div>
      </div>
      <aside class="session-side">
        <div class="card">
          <div class="side-label">这一轮怎么走</div>
          <div class="rule-list">
            <div class="rule"><span class="n">1</span><span>先回答，不急着要答案；卡住再要最小提示。</span></div>
            <div class="rule"><span class="n">2</span><span>一次只推进一个点；说错也比不说有价值。</span></div>
            <div class="rule"><span class="n">3</span><span>结束时会按回忆、解释、应用、迁移四维生成证据。</span></div>
          </div>
        </div>
        <div class="card">
          <div class="side-label">这个单元的本质</div>
          <div class="side-value">${escapeHTML(unit?.essence || "")}</div>
          ${unit?.key_points?.length ? `<div class="divider"></div><div class="side-label">评分关键点</div><div class="keypoint-list">${unit.key_points.slice(0, 6).map(k => `<div class="keypoint">${escapeHTML(k)}</div>`).join("")}</div>` : ""}
        </div>
        <div class="card">
          <div class="side-label">边界提醒</div>
          ${unit?.boundaries?.length ? unit.boundaries.slice(0, 4).map(b => `<div class="small muted" style="margin-top:7px">· ${escapeHTML(b)}</div>`).join("") : `<p class="small muted mt-2">材料没有给出边界。可以主动问：它在什么条件下失效？</p>`}
        </div>
        <div class="card">
          <div class="side-label">会话信息</div>
          <div class="facet-row"><span class="muted">阶段</span><span>${STAGE_LABELS[s.stage] || s.stage}</span><span></span></div>
          <div class="facet-row"><span class="muted">轮数</span><span>${s.turn || 0}</span><span></span></div>
          <div class="facet-row"><span class="muted">引擎</span><span>${engine === "demo" ? "本地演示" : "真实模型"}</span><span></span></div>
          <div class="faint tiny mt-3">${engine === "demo" ? "演示模式使用确定性追问与关键词评估；配置模型后会进行语义判断。" : "真实模型只负责生成语言；一次一问、先答后示由策略层约束。"}</div>
        </div>
      </aside>
    </div>`;
}

function reportHTML(data) {
  const s = data.session, unit = data.unit, report = data.report || {};
  const facets = report.facets || {};
  const values = ["recall", "explain", "apply", "transfer"].map(k => Number(facets[k] || 0));
  const avg = values.reduce((a, b) => a + b, 0) / (values.length || 1);
  const messages = s.messages || [];
  return `
    <div class="page-head">
      <div>
        <a class="row small muted" href="#/unit/${escapeAttr(unit?.id || "")}" style="gap:5px">${icon("chevronLeft", 14)} 返回单元</a>
        <h2>这次对话留下了什么</h2>
        <p>${escapeHTML(unit?.title || "")} · 第 ${s.turn || 0} 轮 · ${formatDateTime(s.ended_at || s.started_at)}</p>
      </div>
      <div class="row wrap">
        <span class="badge ${report.engine === "demo" ? "badge-gold" : "badge-teal"}">${report.engine === "demo" ? "演示评估" : "语义评估"}</span>
        <span class="badge badge-muted">置信度 ${pct(report.confidence || 0)}</span>
      </div>
    </div>
    <div class="report">
      <div class="card report-hero">
        <div style="min-width:0">
          <div class="report-title">${avg >= 0.8 ? "这壶水接近烧开" : avg >= 0.55 ? "有理解，但还有缺口" : "先别急着推进"}</div>
          <div class="report-sub">${escapeHTML(report.summary || "这次对话已经结束。")}</div>
          <div class="report-actions">
            <a class="btn btn-teal" href="#/feynman/${escapeAttr(unit?.id || "")}">${icon("teach", 15)} 用费曼法补漏</a>
            <a class="btn btn-ghost" href="#/unit/${escapeAttr(unit?.id || "")}">回到单元</a>
            <button class="btn btn-ghost" data-new-soc>${icon("refresh", 15)} 再聊一次</button>
          </div>
        </div>
        <div class="score-ring">
          ${ringSVG(avg * 100, { size: 130, stroke: 8, color: "var(--teal)" })}
          <div style="position:relative;z-index:1;text-align:center"><strong>${Math.round(avg * 100)}</strong><span>理解度</span></div>
        </div>
      </div>
      <div class="report-grid">
        <div class="card card-pad">
          <div class="section-head" style="margin-bottom:10px"><div><div class="section-title" style="font-size:16px">四维证据</div><div class="section-sub">不是自评，是对话里实际说出的内容</div></div></div>
          ${["recall", "explain", "apply", "transfer"].map(k => `<div class="facet-row"><span class="muted">${FACET_LABELS[k]}</span><span class="facet-bar"><i style="width:${Math.round((facets[k] || 0) * 100)}%"></i></span><span class="faint" style="text-align:right">${pct(facets[k] || 0)}</span></div>`).join("")}
          <div class="divider"></div>
          <div class="side-label">下一步</div>
          <div class="callout teal mt-2">${escapeHTML(report.next_step || "用费曼法复述一次。")}</div>
        </div>
        <div class="card card-pad">
          <div class="section-head" style="margin-bottom:10px"><div><div class="section-title" style="font-size:16px">真正的收获</div><div class="section-sub">绿色的部分</div></div></div>
          <div class="report-list">${(report.understood || []).map(x => `<div class="report-item">${escapeHTML(x)}</div>`).join("") || `<p class="muted small">还没有足够证据。</p>`}</div>
          <div class="divider"></div>
          <div class="section-title" style="font-size:16px">还模糊的地方</div>
          <div class="report-list mt-2">${(report.gaps || []).map(x => `<div class="report-item gap">${escapeHTML(x)}</div>`).join("") || `<p class="muted small">没有明显缺口，继续用变式题验证。</p>`}</div>
          ${(report.misconceptions || []).length ? `<div class="divider"></div><div class="section-title" style="font-size:16px">误解</div><div class="report-list mt-2">${report.misconceptions.map(x => `<div class="report-item mis">${escapeHTML(x)}</div>`).join("")}</div>` : ""}
        </div>
      </div>
      <details class="card unit-section mt-4" style="padding:0">
        <summary style="padding:18px 22px;cursor:pointer;list-style:none;display:flex;justify-content:space-between;align-items:center"><span class="section-title" style="font-size:16px">对话记录</span><span class="badge badge-muted">${messages.length} 条</span></summary>
        <div style="padding:0 22px 22px">${messages.map(messageHTML).join("")}</div>
      </details>
      <div class="demo-note">${report.engine === "demo" ? "演示模式采用关键词覆盖与结构启发式评分，不等同于语义理解。配置真实模型后可获得更细的评分与缺口分析。" : "报告由真实模型按评分 rubric 生成；关键事实仍建议回到材料核对。"}</div>
    </div>`;
}

export async function render(el, ctx, params) {
  const sid = params.sessionId;
  if (!sid) { ctx.navigate("#/dashboard", { replace: true }); return; }
  let data;
  try {
    data = await ctx.api.get(`/api/socratic/${encodeURIComponent(sid)}`);
  } catch (e) {
    el.innerHTML = `<div class="empty"><div class="empty-icon">${icon("alert", 22)}</div><h3>会话不存在</h3><p>${escapeHTML(e.message)}</p><a class="btn btn-primary mt-3" href="#/dashboard">回到今日</a></div>`;
    return;
  }
  const unit = data.unit;
  ctx.setPageTitle("SOCRATIC DIALOGUE", "苏格拉底");
  if (data.report && data.session.status === "completed") {
    ctx.setPageTitle("SOCRATIC REPORT", "对话报告");
    el.innerHTML = reportHTML(data);
    bindReport(el, ctx, data);
    return;
  }
  el.innerHTML = chatHTML(data);
  bindChat(el, ctx, data, sid);
}

function bindReport(el, ctx, data) {
  el.querySelector("[data-new-soc]")?.addEventListener("click", () => ctx.startSocratic(data.unit.id));
  if (data.session?.messages) {
    // 让 details 里的公式也能渲染
    setTimeout(() => ctx.renderMath(el), 0);
  }
}

function bindChat(el, ctx, data, sid) {
  let session = data.session;
  let messages = [...(session.messages || [])];
  let streaming = false;
  let finished = false;
  const body = el.querySelector("#chat-body");
  const input = el.querySelector("#chat-input");
  const sendBtn = el.querySelector("#send-btn");
  const stageBadge = el.querySelector("#stage-badge");
  const turnBadge = el.querySelector("#turn-badge");

  const scroll = () => { body.scrollTop = body.scrollHeight; };
  const setBusy = (busy) => {
    streaming = busy;
    sendBtn.disabled = busy;
    input.disabled = busy;
    sendBtn.style.opacity = busy ? ".45" : "1";
  };
  const appendUser = (text) => {
    const idx = messages.length;
    messages.push({ id: `tmp-${idx}`, role: "user", content: text, created_at: new Date().toISOString(), meta: {} });
    const holder = document.createElement("div");
    holder.innerHTML = messageHTML(messages[idx], idx);
    body.insertBefore(holder.firstElementChild, el.querySelector("#stream-anchor"));
    scroll();
  };
  const appendAssistantPlaceholder = () => {
    const holder = document.createElement("div");
    holder.innerHTML = `<div class="msg assistant" id="stream-msg">
      <span class="msg-avatar">${icon("spark", 14)}</span>
      <div><div class="msg-bubble"><div class="msg-text prose" id="stream-text"></div><div class="typing-row" id="stream-typing"><span class="typing"><i></i><i></i><i></i></span><span id="stream-status">正在思考…</span></div></div><div class="msg-meta">教练 · 正在回答</div></div>
    </div>`;
    body.insertBefore(holder.firstElementChild, el.querySelector("#stream-anchor"));
    scroll();
    return {
      textEl: document.getElementById("stream-text"),
      typing: document.getElementById("stream-typing"),
      status: document.getElementById("stream-status"),
      root: document.getElementById("stream-msg"),
    };
  };

  async function send(text) {
    if (streaming || finished) return;
    const msg = (text ?? input.value).trim();
    if (!msg) return;
    input.value = "";
    input.style.height = "auto";
    setBusy(true);
    appendUser(msg);
    const placeholder = appendAssistantPlaceholder();
    let full = "";
    let serverSession = null;
    let report = null;
    try {
      await ctx.api.stream("/api/socratic/stream", { session_id: sid, message: msg }, (event, payload) => {
        if (event === "status") {
          placeholder.status.textContent = payload.text || "正在思考…";
          placeholder.typing.style.display = "flex";
        } else if (event === "delta") {
          if (!full) placeholder.typing.style.display = "none";
          full += payload.text || "";
          placeholder.textEl.innerHTML = streamingText(full);
          scroll();
        } else if (event === "done") {
          serverSession = payload.session || serverSession;
          report = payload.report || null;
          const last = serverSession?.messages?.slice().reverse().find(m => m.role === "assistant" && m.content !== placeholder.textEl.textContent);
          const finalText = serverSession?.messages?.slice().reverse().find(m => m.role === "assistant")?.content || full;
          placeholder.textEl.innerHTML = renderMarkdown(finalText);
          placeholder.typing.style.display = "none";
          placeholder.root.querySelector(".msg-meta").textContent = "教练 · 刚刚";
          ctx.renderMath(placeholder.textEl);
          if (serverSession) {
            session = serverSession;
            messages = serverSession.messages || messages;
            turnBadge.textContent = `第 ${session.turn || 0} 轮`;
            stageBadge.textContent = STAGE_LABELS[session.stage] || session.stage;
          }
          scroll();
        } else if (event === "error") {
          placeholder.typing.style.display = "none";
          placeholder.textEl.innerHTML = `<span class="danger">${escapeHTML(payload.message || "生成失败")}</span>`;
          toast(payload.message || "生成失败", "error");
        }
      });
      if (report) {
        finished = true;
        toast("对话结束，报告已生成。", "success");
        setTimeout(() => {
          data.session = session;
          data.report = report;
          data.session.status = "completed";
          el.innerHTML = reportHTML(data);
          ctx.renderMath(el);
          bindReport(el, ctx, data);
        }, 380);
        return;
      }
      setBusy(false);
      input.focus();
    } catch (e) {
      if (e.name === "AbortError") return;
      placeholder.typing.style.display = "none";
      placeholder.textEl.innerHTML = `<span class="danger">${escapeHTML(e.message || "连接失败")}</span>`;
      toast(e.message || "连接失败", "error");
      setBusy(false);
    }
  }

  async function finish() {
    if (finished) return;
    const btn = el.querySelector("[data-finish]");
    if (btn) { btn.disabled = true; btn.innerHTML = "正在整理…"; }
    try {
      const res = await ctx.api.post("/api/socratic/finish", { session_id: sid });
      data.session = res.session;
      data.report = res.report;
      data.session.status = "completed";
      finished = true;
      el.innerHTML = reportHTML(data);
      ctx.setPageTitle("SOCRATIC REPORT", "对话报告");
      ctx.renderMath(el);
      bindReport(el, ctx, data);
      toast("报告生成好了。", "success");
    } catch (e) {
      toast(e.message || "生成报告失败", "error");
      if (btn) { btn.disabled = false; btn.innerHTML = "结束并生成报告"; }
    }
  }

  sendBtn.addEventListener("click", () => send());
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });
  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 120) + "px";
  });
  el.querySelector("[data-hint]")?.addEventListener("click", () => send("我有点卡住了，给我一个最小提示，不要直接给答案。"));
  el.querySelector("[data-finish]")?.addEventListener("click", finish);
  el.querySelectorAll("[data-quick]").forEach(btn => btn.addEventListener("click", () => send(btn.dataset.quick)));
  input.focus();
  body.scrollTop = body.scrollHeight;
}
