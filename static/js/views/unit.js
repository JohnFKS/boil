/* Unit detail: the pre-digested learning surface. */
import {
  escapeHTML, escapeAttr, icon, pct, radarSVG, statusMeta, kindMeta,
  renderMarkdown, truncate, relativeTime, formatDateTime,
} from "../utils.js";

function listHTML(items, ordered = false) {
  if (!items?.length) return `<p class="muted small">材料里没有给出这一部分。</p>`;
  const tag = ordered ? "ol" : "ul";
  return `<${tag}>${items.map(x => `<li>${renderMarkdown(String(x))}</li>`).join("")}</${tag}>`;
}

function probeHTML(p) {
  const meta = kindMeta[p.kind] || kindMeta.recall;
  return `<div class="probe-item" data-probe>
    <button class="probe-head">
      <span class="probe-kind ${meta.className}">${meta.label}</span>
      <span class="probe-q">${escapeHTML(p.prompt)}</span>
      <span class="faint" style="font-size:10px">${p.attempts ? `${p.attempts} 次作答` : "未作答"}</span>
      ${icon("chevron", 14)}
    </button>
    <div class="probe-body">
      <div class="callout teal"><strong>参考答案</strong><br>${renderMarkdown(p.answer || "材料未给出参考答案。")}</div>
      ${p.hints?.length ? `<div class="mt-2"><span class="faint tiny">提示</span>${p.hints.map(h => `<div class="small muted">· ${escapeHTML(h)}</div>`).join("")}</div>` : ""}
      ${p.rubric?.length ? `<div class="chip-row mt-2">${p.rubric.slice(0, 6).map(r => `<span class="tag">${escapeHTML(truncate(r, 24))}</span>`).join("")}</div>` : ""}
    </div>
  </div>`;
}

export async function render(el, ctx, params) {
  const id = params.unitId;
  if (!id) { ctx.navigate("#/map", { replace: true }); return; }
  let data;
  try {
    data = await ctx.api.get(`/api/units/${encodeURIComponent(id)}`);
  } catch (e) {
    if (e.status === 404) {
      el.innerHTML = `<div class="empty"><div class="empty-icon">${icon("alert", 22)}</div><h3>单元不存在</h3><p>它可能已经被删除，或不属于当前目标。</p><a class="btn btn-primary mt-3" href="#/map">回到地图</a></div>`;
      return;
    }
    throw e;
  }
  const u = data.unit;
  const st = data.status || statusMeta[data.mastery?.status || "new"];
  const meta = statusMeta[st.status] || statusMeta.new;
  const facets = data.mastery?.facets || {};
  const facetLabels = { recall: "回忆", explain: "解释", apply: "应用", transfer: "迁移" };
  const facetValues = ["recall", "explain", "apply", "transfer"].map(k => facets[k]?.score || 0);
  const cards = data.cards || [];
  const probes = data.probes || [];
  const sessions = data.sessions || [];
  const dueCards = cards.filter(c => c.state !== "new" && new Date(c.due_at) <= new Date());
  ctx.setPageTitle("KNOWLEDGE UNIT", "知识单元");

  el.innerHTML = `
    <div class="unit-head">
      <div class="unit-head-left">
        <a class="row small muted" href="#/map" style="gap:5px">${icon("chevronLeft", 14)} 知识地图</a>
        <div class="row wrap mt-2">
          <span class="badge ${st.status === "boiled" ? "badge-teal" : st.status === "learning" ? "badge-gold" : st.status === "rusting" ? "badge-ember" : "badge-muted"}">${meta.label}</span>
          <span class="badge badge-muted">${u.estimated_minutes || 45} 分钟</span>
          <span class="badge badge-muted">难度 ${u.difficulty || 3}/5</span>
          ${u.source_ref?.system ? `<span class="badge badge-violet">${escapeHTML(u.source_ref.system)}</span>` : ""}
        </div>
        <h2>${escapeHTML(u.title)}</h2>
        <div class="tag-row">${(u.tags || []).slice(0, 8).map(t => `<span class="tag">${escapeHTML(t)}</span>`).join("")}</div>
      </div>
      <div class="unit-actions">
        <button class="btn btn-primary" data-socratic>${icon("chat", 16)} 开始苏格拉底</button>
        <a class="btn btn-teal" href="#/feynman/${escapeAttr(u.id)}">${icon("teach", 16)} 费曼挑战</a>
        <a class="btn btn-ghost" href="#/review">${icon("review", 16)} 复习</a>
      </div>
    </div>

    <div class="unit-grid">
      <div class="unit-main">
        <div class="essence-card">
          <div class="essence-label">${icon("spark", 13)} 一句话本质</div>
          <div class="essence-text">${escapeHTML(u.essence || "这个单元还没有本质描述。")}</div>
          <div class="essence-foot">
            <span>${icon("clock", 12)} 建议先花 10 分钟预消化</span>
            <span>${icon("target", 12)} 重要度 ${u.importance || 3}/5</span>
            <span>${icon("brain", 12)} 掌握度 ${pct(data.mastery?.average || 0)}</span>
          </div>
        </div>

        ${u.analogy ? `<div class="card unit-section"><h3>把它想成什么</h3><div class="callout ember">${renderMarkdown(u.analogy)}</div>${u.example ? `<h4>一个具体例子</h4><p>${renderMarkdown(u.example)}</p>` : ""}</div>` : ""}

        <div class="card unit-section">
          <h3>标准步骤</h3>
          ${listHTML(u.steps, true)}
        </div>

        <div class="card unit-section">
          <h3>边界与误解</h3>
          <h4>适用与失效</h4>
          ${listHTML(u.boundaries)}
          ${u.misconceptions?.length ? `<h4>常见误解</h4><div class="stack">${u.misconceptions.map(m => `<div class="callout">${renderMarkdown(m)}</div>`).join("")}</div>` : ""}
          ${u.faq?.length ? `<h4>FAQ</h4><div class="stack">${u.faq.map(f => `<div class="callout"><strong>${escapeHTML(f.q)}</strong><p class="mt-2">${renderMarkdown(f.a)}</p></div>`).join("")}</div>` : ""}
        </div>

        <div class="card unit-section">
          <div class="section-head" style="margin-bottom:6px"><div><h3 style="margin:0">探针</h3><div class="section-sub">先自己回答，再看参考答案；这些探针也会进入复习排程。</div></div><span class="badge badge-teal">${probes.length} 个</span></div>
          ${probes.length ? probes.map(probeHTML).join("") : `<div class="empty" style="padding:26px"><p>这个单元还没有探针。</p></div>`}
        </div>

        <div class="card unit-section">
          <div class="section-head" style="margin-bottom:6px"><div><h3 style="margin:0">对话与证据</h3><div class="section-sub">苏格拉底对话会留下证据，而不是一段聊完就忘的记录。</div></div></div>
          ${sessions.length ? sessions.map(s => {
            const report = s.report;
            const score = report ? Object.values(report.facets || {}).reduce((a, b) => a + b, 0) / 4 : null;
            return `<a class="session-row" href="#/socratic/${escapeAttr(s.id)}">
              <span class="row" style="gap:7px">${icon("chat", 13)}<span>${formatDateTime(s.started_at)} · ${s.turn} 轮</span></span>
              <span class="row" style="gap:8px">${s.status === "completed" ? `<span class="badge badge-teal">已完成</span>` : `<span class="badge badge-muted">进行中</span>`}${score !== null ? `<span class="session-score">${pct(score)}</span>` : ""}</span>
            </a>`;
          }).join("") : `<div class="empty" style="padding:26px"><p>还没有对话。开始一场苏格拉底式追问。</p><button class="btn btn-teal mt-3" data-socratic>开始对话</button></div>`}
        </div>
      </div>

      <aside class="unit-side">
        <div class="card side-card">
          <div class="between"><span class="side-label">掌握雷达</span><span class="badge ${st.status === "boiled" ? "badge-teal" : "badge-muted"}">${meta.label}</span></div>
          <div class="radar-wrap">${radarSVG(facetValues, ["回忆", "解释", "应用", "迁移"], { size: 210 })}</div>
          ${["recall", "explain", "apply", "transfer"].map(k => `<div class="facet-row"><span class="muted">${facetLabels[k]}</span><span class="facet-bar"><i style="width:${Math.round((facets[k]?.score || 0) * 100)}%"></i></span><span class="faint" style="text-align:right">${pct(facets[k]?.score || 0)}</span></div>`).join("")}
          <div class="callout mt-3" style="font-size:11px">${data.mastery?.weakest ? `最需要补的是「${facetLabels[data.mastery.weakest]}」。` : "还没有足够证据。完成一次复习或对话就会开始积累。"}</div>
        </div>

        <div class="card side-card">
          <div class="between"><span class="side-label">卡片</span><span class="badge ${dueCards.length ? "badge-ember" : "badge-muted"}">${dueCards.length ? `${dueCards.length} 张到期` : "无到期"}</span></div>
          <div class="mt-2">
            ${cards.length ? cards.map(c => `<div class="card-mini"><span style="min-width:0">${escapeHTML(truncate(c.front, 34))}</span><span class="due">${escapeHTML(c.due_label || "")}</span></div>`).join("") : `<p class="muted small">还没有卡片。</p>`}
          </div>
          <a class="btn btn-ghost btn-block mt-3" href="#/review">去复习 ${icon("arrow", 13)}</a>
        </div>

        <div class="card side-card">
          <div class="side-label">学习动作</div>
          <div class="side-actions">
            <button class="btn btn-primary btn-block" data-socratic>${icon("chat", 15)} 苏格拉底对话</button>
            <a class="btn btn-teal btn-block" href="#/feynman/${escapeAttr(u.id)}">${icon("teach", 15)} 费曼挑战</a>
            ${u.analogy ? `<div class="callout mt-2" style="font-size:11px">先读一遍本质与类比，再开始对话。不要一上来就要求答案。</div>` : ""}
          </div>
        </div>

        ${data.relations?.length ? `<div class="card side-card"><div class="side-label">关系</div><div class="mt-2">${data.relations.map(r => `<div class="card-mini"><span>${r.kind === "prerequisite" ? "前置" : r.kind}</span><span class="due">${escapeHTML(r.from_title || "")} → ${escapeHTML(r.to_title || "")}</span></div>`).join("")}</div></div>` : ""}

        <div class="card side-card">
          <div class="side-label">来源</div>
          <div class="source-line mt-2">
            ${u.source_ref?.system ? `<span class="badge badge-muted">${escapeHTML(u.source_ref.system)}</span>` : ""}
            ${u.source_ref?.kind ? `<span>${escapeHTML(u.source_ref.kind)}</span>` : ""}
            ${u.source_ref?.id ? `<code>${escapeHTML(u.source_ref.id)}</code>` : ""}
            ${u.source_ref?.version ? `<span>v${escapeHTML(u.source_ref.version)}</span>` : ""}
          </div>
          <div class="faint tiny mt-2">${u.source_ref?.path ? escapeHTML(u.source_ref.path) : "来源未记录"}</div>
        </div>
      </aside>
    </div>`;

  el.querySelectorAll("[data-socratic]").forEach(btn => btn.addEventListener("click", () => ctx.startSocratic(u.id)));
  el.querySelectorAll("[data-probe] .probe-head").forEach(head => {
    head.addEventListener("click", () => head.parentElement.classList.toggle("open"));
  });
}
