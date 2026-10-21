/* Unit detail: the pre-digested learning surface. */
import {
  escapeHTML, escapeAttr, icon, pct, radarSVG, ringSVG, statusMeta, kindMeta,
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
  const heatValue = Number(data.heat || 0);
  const pn = data.pn || { total: 0, missing: [], source: null, complete: null };
  const records = data.records || [];
  const heatRec = heatValue <= 0 ? "先点火，做预消化" : heatValue < 40 ? "进入苏格拉底" : (pn.missing?.length ? "用费曼补缺口" : heatValue >= 80 ? "做极致对标" : "费曼或对标");
  const pnText = pn.source
    ? (pn.missing?.length ? `标准步骤 ${pn.total || "—"} 步，最近报告还缺 ${pn.missing.length} 步。` : `最近报告没有发现缺口；用极致对标验收。`)
    : "还没有对话或讲解证据。先预消化，再开始苏格拉底。";
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
        <a class="btn btn-ghost" href="#/feynman/${escapeAttr(u.id)}?mode=examiner">${icon("trophy", 16)} 极致对标</a>
        <button class="btn btn-ghost" data-audio>${icon("volume", 16)} 听一耳</button>
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
          <div class="section-head" style="margin-bottom:6px"><div><h3 style="margin:0">火候记录</h3><div class="section-sub">每次苏格拉底、费曼、回测都会改变这壶水的温度。</div></div><span class="badge badge-muted">${records.length} 条</span></div>
          ${records.length ? records.map(r => {
            const modeLabel = {socratic:"苏格拉底",feynman:"费曼",benchmark:"极致对标",recall:"回测",digest:"预消化",audio:"听讲"}[r.mode] || r.mode;
            const summary = r.result?.summary || (r.result?.overall != null ? `讲解分 ${Math.round(r.result.overall * 100)}` : r.result?.rating ? `评分 ${r.result.rating}/4` : "");
            return `<div class="session-row"><span class="row" style="gap:7px"><span class="badge badge-muted">${escapeHTML(modeLabel)}</span><span class="faint tiny">${formatDateTime(r.created_at)}</span></span><span class="row" style="gap:8px"><span class="session-score">${Math.round(r.heat_before)}° → ${Math.round(r.heat_after)}°</span><span class="faint tiny" style="max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHTML(truncate(summary, 50))}</span></span></div>`;
          }).join("") : `<div class="empty" style="padding:24px"><p>还没有火候记录。完成一次对话、费曼或回测。</p></div>`}
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
        <div class="card side-card unit-heat-card">
          <div class="between"><span class="side-label">本单元火候</span><span class="badge ${heatValue >= 80 ? "badge-teal" : heatValue >= 40 ? "badge-gold" : heatValue > 0 ? "badge-ember" : "badge-muted"}">${escapeHTML(data.heat_label || "未点火")}</span></div>
          <div class="advisor-head mt-3">
            <div class="advisor-heat">
              ${ringSVG(heatValue, { size: 76, stroke: 6, color: heatValue >= 80 ? "var(--teal)" : heatValue >= 40 ? "var(--gold)" : heatValue > 0 ? "var(--ember)" : "var(--faint)" })}
              <div><strong>${Math.round(heatValue)}</strong><small>°C</small></div>
            </div>
            <div style="min-width:0;flex:1">
              <div class="advisor-title" style="font-size:15px">${escapeHTML(heatRec)}</div>
              <div class="advisor-target">${pn.missing?.length ? `还缺 ${pn.missing.length} 步` : escapeHTML(pnText)}</div>
            </div>
          </div>
          <div class="callout mt-3" style="font-size:11px">${escapeHTML(pnText)}</div>
          ${pn.missing?.length ? `<div class="advisor-pn mt-2">${pn.missing.slice(0, 4).map(x => `<span class="gap-chip">${escapeHTML(x)}</span>`).join("")}</div>` : ""}
          <div class="side-actions">
            <button class="btn btn-ghost btn-block" data-audio>${icon("volume", 14)} 听一耳 · 第 0 档</button>
            <button class="btn btn-primary btn-block" data-socratic>${icon("chat", 14)} 苏格拉底 · 第 2 档</button>
            <a class="btn btn-teal btn-block" href="#/feynman/${escapeAttr(u.id)}">${icon("teach", 14)} 费曼 · 第 4 档</a>
            <a class="btn btn-ghost btn-block" href="#/feynman/${escapeAttr(u.id)}?mode=examiner">${icon("trophy", 14)} 极致对标 · 非档位</a>
          </div>
        </div>

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
          <div class="side-label">六种模式</div>
          <div class="side-actions">
            <button class="btn btn-ghost btn-block" data-audio>${icon("volume", 15)} 听讲 · 音频浸泡</button>
            <a class="btn btn-ghost btn-block" href="#/unit/${escapeAttr(u.id)}">${icon("book", 15)} 阅读 · 预消化</a>
            <button class="btn btn-primary btn-block" data-socratic>${icon("chat", 15)} 讨论 · 苏格拉底</button>
            <a class="btn btn-ghost btn-block" href="#/review">${icon("review", 15)} 演练 · 高频回测</a>
            <a class="btn btn-teal btn-block" href="#/feynman/${escapeAttr(u.id)}">${icon("teach", 15)} 教别人 · 费曼</a>
            <a class="btn btn-ghost btn-block" href="#/feynman/${escapeAttr(u.id)}?mode=examiner">${icon("trophy", 15)} 非档位 · 极致对标</a>
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
  el.querySelectorAll("[data-audio]").forEach(btn => btn.addEventListener("click", () => openAudioModal(ctx, u)));
  el.querySelectorAll("[data-probe] .probe-head").forEach(head => {
    head.addEventListener("click", () => head.parentElement.classList.toggle("open"));
  });
  ctx.cleanup(() => { try { window.speechSynthesis?.cancel(); } catch { /* noop */ } });
  if (params.query?.get("action") === "audio") {
    setTimeout(() => openAudioModal(ctx, u), 120);
  }
}

function openAudioModal(ctx, unit) {
  const script = [
    `${unit.title}。`,
    `核心本质：${unit.essence || ""}`,
    (unit.steps || []).length ? `标准步骤：${unit.steps.map((s, i) => `${i + 1}. ${s}`).join("；")}` : "",
    (unit.boundaries || []).length ? `边界与失效：${unit.boundaries.join("；")}` : "",
    "听完之后，不要只点头。用你自己的话复述核心与边界，再回到苏格拉底对话。"
  ].filter(Boolean).join("\n");
  const dom = document.createElement("div");
  dom.innerHTML = `
    <div class="audio-card">
      <div class="audio-wave">${[1,2,3,4,5,6,7].map(i=>`<i style="animation-delay:${i*0.12}s"></i>`).join("")}</div>
      <div class="side-label">第 0 档 · 熟悉感前置</div>
      <div class="audio-script">${escapeHTML(script)}</div>
      <div class="audio-controls">
        <button class="btn btn-primary btn-sm" data-audio-play>${icon("play", 14)} 播放</button>
        <button class="btn btn-ghost btn-sm" data-audio-pause>暂停</button>
        <button class="btn btn-ghost btn-sm" data-audio-stop>停止</button>
        <span class="faint tiny" data-audio-status>建议 2–4 分钟；听完做 90 秒回忆。</span>
      </div>
      <div class="callout mt-3">音频只建立熟悉感，不负责烧开。真正的验收在第 2 档对话和第 4 档费曼。</div>
      <div class="row mt-3"><button class="btn btn-teal btn-sm" data-audio-socratic>听完了，做 90 秒回忆</button></div>
    </div>`;
  let utter = null;
  const synth = window.speechSynthesis;
  const status = () => dom.querySelector("[data-audio-status]");
  const play = () => {
    if (!synth) {
      status().textContent = "当前浏览器不支持语音合成；可以直接读下面的文稿。";
      return;
    }
    synth.cancel();
    utter = new SpeechSynthesisUtterance(script);
    utter.lang = "zh-CN";
    utter.rate = 0.95;
    utter.pitch = 1.02;
    const voice = synth.getVoices().find(v => /zh|chinese|中文/i.test(`${v.lang} ${v.name}`));
    if (voice) utter.voice = voice;
    utter.onend = () => { status().textContent = "听完了。现在花 90 秒，用自己的话复述核心与边界。"; };
    utter.onerror = () => { status().textContent = "语音播放失败；可以直接读下面的文稿。"; };
    status().textContent = "正在播放…";
    synth.speak(utter);
  };
  const modal = ctx.openModal({
    title: "听一耳 · 第 0 档",
    subtitle: `${unit.title} · 熟悉感前置`,
    body: dom,
    wide: true,
    actions: [{ label: "关闭", kind: "btn-ghost", onClick: () => { try { synth?.cancel(); } catch { /* noop */ } } }],
  });
  dom.querySelector("[data-audio-play]")?.addEventListener("click", play);
  dom.querySelector("[data-audio-pause]")?.addEventListener("click", () => {
    if (!synth) return;
    if (synth.paused) { synth.resume(); status().textContent = "继续播放…"; }
    else { synth.pause(); status().textContent = "已暂停"; }
  });
  dom.querySelector("[data-audio-stop]")?.addEventListener("click", () => {
    try { synth?.cancel(); } catch { /* noop */ }
    status().textContent = "已停止。可以随时重播。";
  });
  dom.querySelector("[data-audio-socratic]")?.addEventListener("click", () => {
    try { synth?.cancel(); } catch { /* noop */ }
    modal.close();
    ctx.startSocratic(unit.id);
  });
  if (!synth) status().textContent = "当前浏览器不支持语音合成；可以直接读下面的文稿。";
}
