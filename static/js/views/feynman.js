/* Feynman challenge: explain it to someone, then see the real gaps. */
import {
  escapeHTML, escapeAttr, icon, pct, ringSVG, renderMarkdown, toast,
} from "../utils.js";

const MODES = {
  child: { label: "讲给 10 岁小孩", hint: "不用术语，用生活里的东西打比方；但类比不能代替定义。", prompt: "想象你面前坐着一个 10 岁的小孩。用他能听懂的话，把下面这个单元讲清楚。" },
  peer: { label: "讲给同学", hint: "说清因果、步骤和关键条件，像在给同学讲题。", prompt: "想象你在给一个同学讲题。他听过一点，但还没有真正理解。" },
  examiner: { label: "讲给阅卷人", hint: "定义、条件、步骤、边界都要精确，不要跳步。", prompt: "想象你在向阅卷人解释你的解题思路。每一步都要有依据。" },
};

const DIMS = [
  ["correctness", "正确性"], ["completeness", "完整性"], ["structure", "结构"],
  ["simplicity", "通俗度"], ["boundary", "边界"], ["analogy", "类比"],
];

function fmtTime(sec) {
  const m = Math.floor(sec / 60).toString().padStart(2, "0");
  const s = Math.floor(sec % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

function countWords(text) {
  const cn = (text.match(/[\u4e00-\u9fff]/g) || []).length;
  const en = (text.match(/[a-zA-Z0-9]+/g) || []).length;
  return cn + en;
}

function promptHTML(mode) {
  const m = MODES[mode];
  return `<div class="card prompt-card">
    <div class="prompt-label">FEYNMAN MODE · ${escapeHTML(m.label)}</div>
    <div class="prompt-text">${escapeHTML(m.prompt)}</div>
    <div class="prompt-hint">${escapeHTML(m.hint)}</div>
    ${mode === "examiner" ? `<div class="callout violet mt-3" style="border-color:rgba(167,139,250,.22);background:var(--violet-soft)"><strong>极致对标：</strong>完整复现标准步骤，不能跳步。AI 会像阅卷人一样按 rubric 找缺口；过了才算烧开。</div>` : ""}
  </div>`;
}

function rubricHTML() {
  const weights = { correctness: "35%", completeness: "25%", structure: "15%", simplicity: "10%", boundary: "10%", analogy: "5%" };
  return `<div class="rubric">${DIMS.map(([k, label]) => `<div class="rubric-row"><span>${label}</span><span class="rubric-weight">${weights[k]}</span></div>`).join("")}</div>`;
}

function resultHTML(result) {
  const score = Math.round((result.overall || 0) * 100);
  const dims = result.dimensions || {};
  return `<div class="card result-card" id="feynman-result">
    <div class="result-hero">
      <div class="score-ring">
        ${ringSVG(score, { size: 130, stroke: 8, color: score >= 75 ? "var(--teal)" : score >= 50 ? "var(--gold)" : "var(--ember)" })}
        <div style="position:relative;z-index:1;text-align:center"><strong>${score}</strong><span>讲解分</span></div>
      </div>
      <div style="min-width:0">
        <div class="result-title">${score >= 85 ? "讲得很清楚。" : score >= 65 ? "基本讲通了，但还有洞。" : score >= 40 ? "理解在，但表达和边界还缺。" : "先回去补一下关键点。"}</div>
        <div class="muted small mt-2">${escapeHTML(result.mode_label || "")} · ${result.engine === "demo" ? "演示评估" : "语义评估"}</div>
        <div class="row wrap mt-3">
          <a class="btn btn-ghost btn-sm" href="#/unit/${escapeAttr(result.unit_id)}">回到单元</a>
          <button class="btn btn-ghost btn-sm" data-again>${icon("refresh", 13)} 再讲一次</button>
          <button class="btn btn-teal btn-sm" data-soc>${icon("chat", 13)} 去对话补漏</button>
        </div>
      </div>
    </div>
    <div class="dim-list">
      ${DIMS.map(([k, label]) => {
        const d = dims[k] || {};
        return `<div class="dim-row">
          <span class="muted">${label}</span>
          <span class="dim-bar"><i style="width:${Math.round((d.score || 0) * 100)}%"></i></span>
          <span class="faint" style="text-align:right">${Math.round((d.score || 0) * 100)}</span>
        </div>${d.reason ? `<div class="faint tiny" style="grid-column:1/-1;margin-top:-5px">${escapeHTML(d.reason)}</div>` : ""}`;
      }).join("")}
    </div>
    ${(result.gaps || []).length ? `<div class="divider"></div><div class="side-label">真正的缺口</div><div class="gap-row">${result.gaps.map(g => `<span class="gap-chip">${escapeHTML(g)}</span>`).join("")}</div>` : ""}
    ${(result.jargon || []).length ? `<div class="mt-3"><span class="side-label">用了但没解释的术语</span><div class="gap-row">${result.jargon.map(g => `<span class="tag">${escapeHTML(g)}</span>`).join("")}</div></div>` : ""}
    ${result.model_answer ? `<details class="mt-3"><summary class="side-label" style="cursor:pointer">参考讲解（先别急着看）</summary><div class="model-answer">${renderMarkdown(result.model_answer)}</div></details>` : ""}
    ${(result.followups || []).length ? `<div class="divider"></div><div class="side-label">继续逼问自己</div><div class="followup-list">${result.followups.map(f => `<div class="followup">${escapeHTML(f)}</div>`).join("")}</div>` : ""}
    ${result.next_step ? `<div class="callout teal mt-3"><strong>下一步：</strong>${escapeHTML(result.next_step)}</div>` : ""}
    ${result.note ? `<div class="demo-note">${escapeHTML(result.note)}</div>` : ""}
  </div>`;
}

export async function render(el, ctx, params) {
  const unitId = params.unitId;
  if (!unitId) { ctx.navigate("#/map", { replace: true }); return; }
  let data;
  try {
    data = await ctx.api.get(`/api/units/${encodeURIComponent(unitId)}`);
  } catch (e) {
    el.innerHTML = `<div class="empty"><div class="empty-icon">${icon("alert", 22)}</div><h3>单元不存在</h3><p>${escapeHTML(e.message)}</p><a class="btn btn-primary mt-3" href="#/map">回到地图</a></div>`;
    return;
  }
  const unit = data.unit;
  const status = data.status;
  ctx.setPageTitle("FEYNMAN CHALLENGE", "费曼挑战");
  const requestedMode = params.query?.get("mode");
  let mode = MODES[requestedMode] ? requestedMode : "child";
  let result = null;
  let started = null;
  let elapsed = 0;
  let timer = null;
  let submitting = false;

  el.innerHTML = `
    <div class="page-head">
      <div>
        <a class="row small muted" href="#/unit/${escapeAttr(unit.id)}" style="gap:5px">${icon("chevronLeft", 14)} 返回单元</a>
        <h2>${escapeHTML(unit.title)}</h2>
        <p>费曼挑战：讲给一个外行听。讲不清楚的地方，就是还没学会的地方。</p>
      </div>
      <div class="row wrap">
        <span class="badge ${status.status === "boiled" ? "badge-teal" : "badge-muted"}">${escapeHTML(status.status_label || "学习中")}</span>
        <span class="badge badge-muted">${unit.estimated_minutes || 45} 分钟单元</span>
      </div>
    </div>
    <div class="feynman-layout">
      <div class="feynman-main" id="feynman-main" data-selectable data-unit-id="${escapeAttr(unitId)}" data-source="feynman">
        <div class="card explain-card">
          <div class="mode-tabs" id="mode-tabs">
            ${Object.entries(MODES).map(([k, m]) => `<button class="mode-tab ${k === mode ? "active" : ""}" data-mode="${k}">${m.label}</button>`).join("")}
          </div>
          <div id="prompt-slot" class="mt-3">${promptHTML(mode)}</div>
          <div class="explain-meta mt-3">
            <div class="row" style="gap:12px"><span class="explain-timer" id="feynman-timer">00:00</span><span class="faint tiny" id="feynman-count">0 字</span></div>
            <div class="row" style="gap:6px"><button class="hint-chip" id="peek-btn">${icon("eye", 12)} 偷看关键点</button><button class="hint-chip" id="clear-btn">清空</button></div>
          </div>
          <textarea class="textarea tall mt-2" id="feynman-text" placeholder="开始讲吧。不要写提纲，直接讲给对象听。"></textarea>
          <div id="peek-box" class="callout gold mt-2" style="display:none">${unit.key_points?.length ? unit.key_points.map(k => `<div>· ${escapeHTML(k)}</div>`).join("") : "这个单元没有预先提取关键点。先讲，再看评分报告。"}</div>
          <div class="row mt-3" style="justify-content:space-between;gap:10px;flex-wrap:wrap">
            <span class="faint tiny">建议 2–5 分钟；提交后不可修改。</span>
            <button class="btn btn-primary" id="submit-btn">${icon("send", 15)} 提交讲解</button>
          </div>
        </div>
        <div id="result-slot"></div>
      </div>
      <aside class="feynman-main">
        <div class="card card-pad">
          <div class="side-label">评分标准</div>
          <p class="small muted mt-2">真实模型按这六维评分；演示模式用关键点覆盖、结构与边界关键词估算。</p>
          <div class="mt-3">${rubricHTML()}</div>
        </div>
        <div class="card card-pad">
          <div class="side-label">这个单元的本质</div>
          <div class="side-value">${escapeHTML(unit.essence || "")}</div>
          ${unit.boundaries?.length ? `<div class="divider"></div><div class="side-label">边界</div>${unit.boundaries.slice(0, 3).map(b => `<div class="small muted mt-2">· ${escapeHTML(b)}</div>`).join("")}` : ""}
        </div>
        <div class="card card-pad">
          <div class="side-label">讲解提示</div>
          <div class="rule-list">
            <div class="rule"><span class="n">1</span><span>先给本质，再给步骤，最后说边界。</span></div>
            <div class="rule"><span class="n">2</span><span>不要用“显然”“众所周知”跳过关键一步。</span></div>
            <div class="rule"><span class="n">3</span><span>类比只帮助理解；讲完要回到准确定义。</span></div>
          </div>
        </div>
      </aside>
    </div>`;

  const textEl = el.querySelector("#feynman-text");
  const timerEl = el.querySelector("#feynman-timer");
  const countEl = el.querySelector("#feynman-count");
  const submitBtn = el.querySelector("#submit-btn");
  const resultSlot = el.querySelector("#result-slot");

  const tick = () => {
    elapsed += 1;
    timerEl.textContent = fmtTime(elapsed);
    timerEl.classList.toggle("warn", elapsed > 300);
  };
  const startTimer = () => {
    if (started) return;
    started = true;
    timer = setInterval(tick, 1000);
  };
  const stopTimer = () => { if (timer) { clearInterval(timer); timer = null; } started = true; };

  textEl.addEventListener("input", () => {
    startTimer();
    countEl.textContent = `${countWords(textEl.value)} 字`;
  });
  el.querySelectorAll("[data-mode]").forEach(btn => btn.addEventListener("click", () => {
    mode = btn.dataset.mode;
    el.querySelectorAll("[data-mode]").forEach(b => b.classList.toggle("active", b === btn));
    el.querySelector("#prompt-slot").innerHTML = promptHTML(mode);
  }));
  el.querySelector("#peek-btn").addEventListener("click", () => {
    const box = el.querySelector("#peek-box");
    box.style.display = box.style.display === "none" ? "block" : "none";
  });
  el.querySelector("#clear-btn").addEventListener("click", () => {
    textEl.value = "";
    countEl.textContent = "0 字";
    textEl.focus();
  });

  submitBtn.addEventListener("click", async () => {
    if (submitting) return;
    const text = textEl.value.trim();
    if (text.length < 20) return toast("讲解太短了，至少写 20 个字", "error");
    submitting = true;
    stopTimer();
    submitBtn.disabled = true;
    submitBtn.innerHTML = "正在评分…";
    try {
      result = await ctx.api.post("/api/feynman/evaluate", { unit_id: unit.id, text, mode });
      textEl.disabled = true;
      resultSlot.innerHTML = resultHTML(result);
      ctx.renderMath(resultSlot);
      el.querySelector("#result-slot").scrollIntoView({ behavior: "smooth", block: "start" });
      el.querySelector("[data-again]")?.addEventListener("click", reset);
      el.querySelector("[data-soc]")?.addEventListener("click", () => ctx.startSocratic(unit.id));
      if (result.engine === "demo") toast("演示模式评分完成；配置模型后可获得语义评分。", "info", 4800);
      else toast("费曼报告已生成。", "success");
      ctx.refreshDashboard().catch(() => {});
    } catch (e) {
      toast(e.message || "评分失败", "error", 5000);
    } finally {
      submitting = false;
      submitBtn.disabled = false;
      submitBtn.innerHTML = `${icon("send", 15)} 提交讲解`;
    }
  });

  function reset() {
    result = null;
    started = null;
    elapsed = 0;
    timerEl.textContent = "00:00";
    timerEl.classList.remove("warn");
    countEl.textContent = "0 字";
    textEl.disabled = false;
    textEl.value = "";
    resultSlot.innerHTML = "";
    textEl.focus();
  }

  ctx.cleanup(() => { if (timer) clearInterval(timer); });
  ctx.renderMath(el);
}
