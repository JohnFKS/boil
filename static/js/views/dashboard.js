/* Dashboard: one screen to decide the next best action. */
import {
  escapeHTML, icon, pct, relativeTime, ringSVG, radarSVG, statusMeta,
  eventMeta, daysUntil, formatMinutes, truncate, toast,
} from "../utils.js";
import { openStyleTest } from "../styleTest.js";

function greeting() {
  const h = new Date().getHours();
  if (h < 6) return "夜深了";
  if (h < 11) return "早上好";
  if (h < 14) return "中午好";
  if (h < 18) return "下午好";
  return "晚上好";
}

function miniMapSVG(units) {
  if (!units?.length) return "";
  const w = 520, h = 150;
  const n = units.length;
  const gap = n > 1 ? (w - 100) / (n - 1) : 0;
  const nodes = units.map((u, i) => {
    const x = 50 + i * gap;
    const y = 64 + (i % 2 ? 18 : -8);
    const color = statusMeta[u.status]?.color || "var(--faint)";
    return `<g data-unit="${escapeHTML(u.id)}" style="cursor:pointer">
      <circle cx="${x}" cy="${y}" r="14" fill="${color}" opacity="${u.status === "new" ? 0.35 : 0.9}"/>
      <circle cx="${x}" cy="${y}" r="5" fill="var(--panel-solid)"/>
      <text x="${x}" y="${y + 34}" fill="var(--muted)" font-size="9" text-anchor="middle">${escapeHTML(truncate(u.title, 13))}</text>
    </g>`;
  }).join("");
  const lines = units.slice(1).map((_, i) => {
    const x1 = 50 + i * gap, x2 = 50 + (i + 1) * gap;
    const y1 = 64 + (i % 2 ? 18 : -8), y2 = 64 + ((i + 1) % 2 ? 18 : -8);
    return `<line x1="${x1 + 14}" y1="${y1}" x2="${x2 - 14}" y2="${y2}" stroke="var(--line-strong)" stroke-width="1.2" stroke-dasharray="3 4"/>`;
  }).join("");
  return `<svg viewBox="0 0 ${w} ${h}" class="mini-map" style="width:100%;height:180px" role="img" aria-label="知识地图预览">${lines}${nodes}</svg>`;
}

function stepHTML(index, title, sub, state, href) {
  const cls = state === "current" ? "current" : state === "done" ? "done" : "";
  return `<a class="loop-step ${cls}" href="${href}">
    <span class="step-index">${state === "done" ? icon("check", 13) : String(index).padStart(2, "0")}</span>
    <span class="loop-step-body"><span class="loop-step-title">${title}</span><span class="loop-step-sub">${sub}</span></span>
    <span class="badge ${state === "done" ? "badge-teal" : state === "current" ? "badge-ember" : "badge-muted"}">${state === "done" ? "完成" : state === "current" ? "现在" : "待进行"}</span>
  </a>`;
}

export async function render(el, ctx, params) {
  let d = ctx.state.dashboard;
  if (!d?.has_goal) {
    try { d = await ctx.api.get("/api/dashboard"); ctx.state.dashboard = d; } catch { /* handled below */ }
  }
  if (!d?.has_goal) { ctx.navigate("#/onboarding", { replace: true }); return; }
  const goal = d.goal, plan = d.plan || {}, counts = d.counts || {}, stats = d.stats || {};
  const mastery = d.mastery || { facets: {}, average: 0 };
  const next = d.next_action || {};
  const due = counts.due || 0;
  const newCount = counts.new || 0;
  const unit = d.next_units?.[0] || null;
  const days = goal.deadline ? daysUntil(goal.deadline) : null;
  const today = plan.today || null;
  const progress = plan.progress || { percent: 0, done: 0, total: counts.units || 0 };
  const facetLabels = { recall: "回忆", explain: "解释", apply: "应用", transfer: "迁移" };
  const facetValues = ["recall", "explain", "apply", "transfer"].map(k => mastery.facets?.[k] || 0);
  const digest = d.digest || goal?.meta?.ai_digest || {};
  const persona = goal?.meta?.persona || null;
  const byModel = (d.generated_by || goal?.meta?.ai_generated_by) === "model";
  const currentUnitId = unit?.id;
  const mapUnits = d.map_preview?.units || d.next_units?.slice(0, 6) || [];
  const weakest = mastery.weakest;

  el.innerHTML = `
    <div class="stagger">
      <section class="hero">
        <div class="hero-copy">
          <div class="hero-kicker"><span class="line"></span> ${escapeHTML(goal.title)}</div>
          <h1>${greeting()}。<br>今天，把哪壶水<em>烧开</em>？</h1>
          <p class="hero-sub">${due > 0 ? `有 ${due} 张卡已经到期。先复习，再学新内容——这不是勤奋，是让之前的时间不作废。` : next.kind === "done" ? "这个目标已经全部烧开。你可以去洞察页复盘，或开启一个新目标。" : "今天只需要完成一个小闭环：预消化 → 苏格拉底 → 费曼 → 留下证据。"}</p>
          <div class="hero-actions">
            ${next.href ? `<a class="btn btn-primary btn-lg" href="${next.href}">${icon(next.kind === "review" ? "review" : next.kind === "feynman" ? "teach" : "play", 16)} ${escapeHTML(next.cta || "开始")}</a>` : ""}
            ${due > 0 && currentUnitId ? `<a class="btn btn-ghost btn-lg" href="#/unit/${escapeAttr(currentUnitId)}">先看一眼单元</a>` : ""}
            <a class="btn btn-ghost btn-lg" href="#/map">${icon("map", 15)} 知识地图</a>
          </div>
          <div class="hero-meta">
            <div class="hero-meta-item"><strong>${progress.done}/${progress.total}</strong><span>已烧开</span></div>
            <div class="hero-meta-item"><strong>${days !== null ? (days >= 0 ? `${days} 天` : "已到期") : "自定"}</strong><span>目标倒计时</span></div>
            <div class="hero-meta-item"><strong>${formatMinutes(stats.today_minutes || 0)}</strong><span>今天投入</span></div>
            <div class="hero-meta-item"><strong>${stats.streak || 0} 天</strong><span>连续学习</span></div>
          </div>
        </div>
        <div class="hero-visual">
          <div class="ring-wrap">
            ${ringSVG(progress.percent || 0, { size: 190, stroke: 8 })}
            <div class="ring-center">
              <strong>${Math.round(progress.percent || 0)}<small style="font-size:18px">%</small></strong>
              <span>烧开进度</span>
              <small>${counts.boiled || 0} / ${counts.units || 0} 单元</small>
            </div>
          </div>
        </div>
      </section>

      <div class="stats-grid">
        <div class="stat">
          <span class="stat-icon">${icon("review", 18)}</span>
          <div><div class="stat-value">${stats.reviews_today || 0}</div><div class="stat-label">今天已复习</div></div>
        </div>
        <div class="stat">
          <span class="stat-icon teal">${icon("flame", 18)}</span>
          <div><div class="stat-value">${counts.boiled || 0}</div><div class="stat-label">已烧开单元</div></div>
        </div>
        <div class="stat">
          <span class="stat-icon gold">${icon("clock", 18)}</span>
          <div><div class="stat-value">${formatMinutes(stats.week_minutes || 0)}</div><div class="stat-label">本周投入</div></div>
        </div>
        <div class="stat">
          <span class="stat-icon violet">${icon("target", 18)}</span>
          <div><div class="stat-value">${pct(stats.retention_30d || 0)}</div><div class="stat-label">30 天保留率</div></div>
        </div>
      </div>

      <div class="grid grid-main mt-4" data-selectable data-source="dashboard">
        <div class="stack">
          <div class="card card-pad">
            <div class="section-head">
              <div><div class="section-title">学习闭环</div><div class="section-sub">完整链路参考；今天具体从哪一档开始，看右侧「火候建议」。</div></div>
              <span class="badge badge-ember">${today?.total_minutes || 0} 分钟</span>
            </div>
            <div class="loop-steps">
              ${stepHTML(1, "复习到期卡", due > 0 ? `${due} 张卡在等你，先还掉` : "今天没有到期复习", due > 0 ? "current" : "done", "#/review")}
              ${stepHTML(2, "预消化", currentUnitId ? "读一句话本质、类比与边界" : "暂无待学单元", "", currentUnitId ? `#/unit/${currentUnitId}` : "#/map")}
              ${stepHTML(3, "苏格拉底", "一次一个问题，把理解走通", "", currentUnitId ? `#/unit/${currentUnitId}` : "#/map")}
              ${stepHTML(4, "费曼挑战", "讲给一个外行听，暴露真实缺口", "", currentUnitId ? `#/feynman/${currentUnitId}` : "#/map")}
              ${stepHTML(5, "留下证据", "复习卡与掌握雷达会记住今天", "", "#/insights")}
            </div>
            <div class="callout mt-3">${today?.is_rest ? "今天是计划里的休息日。休息也是学习的一部分；如果手痒，只做复习即可。" : `今日重点：<strong>${escapeHTML(today?.focus || "按自己的节奏来")}</strong>。不要贪多，完成一个闭环比刷十个视频更有用。`}</div>
          </div>

          ${digest.overview ? `
          <div class="card card-pad">
            <div class="section-head">
              <div><div class="section-title">材料总览</div><div class="section-sub">读完全部材料后的整体判断</div></div>
              <span class="badge ${byModel ? "badge-teal" : "badge-muted"}">${byModel ? "AI 分析" : "本地规则"}</span>
            </div>
            <p class="digest-copy">${escapeHTML(digest.overview)}</p>
            ${(digest.advice || []).length ? `<ul class="digest-advice">${digest.advice.map(a => `<li>${escapeHTML(a)}</li>`).join("")}</ul>` : ""}
            ${(d.study_plan_phases || []).length ? `<div class="phase-strip">${d.study_plan_phases.map(p => `<span class="phase-chip"><strong>${escapeHTML(p.phase || "")}</strong>${escapeHTML(p.focus || "")}</span>`).join("")}</div>` : ""}
          </div>` : ""}

          ${persona ? `
          <div class="card card-pad persona-card">
            <div class="between">
              <div><div class="section-title">教练的带法</div><div class="section-sub">${escapeHTML(persona.strategy || "")}</div></div>
              <span class="badge badge-violet">${icon("compass", 12)} ${escapeHTML(persona.label)}</span>
            </div>
            <div class="row mt-2" style="gap:8px;flex-wrap:wrap">
              ${persona.secondary_label ? `<span class="badge badge-muted">次倾向：${escapeHTML(persona.secondary_label)}</span>` : ""}
              <button class="btn btn-ghost btn-sm" data-style-test>${icon("refresh", 13)} 重测带法</button>
            </div>
          </div>` : `
          <div class="card card-pad persona-card">
            <div class="between">
              <div><div class="section-title">还没测过带法</div><div class="section-sub">5 道情境题，决定追问是「拆小步」还是「多对照」。不测也能正常学习。</div></div>
              <button class="btn btn-ghost btn-sm" data-style-test>${icon("compass", 13)} 花 3 分钟测一下</button>
            </div>
          </div>`}

          <div class="grid grid-2">
            <div class="card card-pad">
              <div class="section-head"><div><div class="section-title">掌握雷达</div><div class="section-sub">证据来自复习、对话、讲解与探针</div></div><span class="badge badge-teal">${pct(mastery.average || 0)}</span></div>
              <div class="radar-wrap">${radarSVG(facetValues, ["回忆", "解释", "应用", "迁移"], { size: 224 })}</div>
              <div class="radar-legend">
                ${["recall", "explain", "apply", "transfer"].map((k, i) => `<div><span class="legend-dot" style="background:${["var(--ember)","var(--gold)","var(--teal)","var(--violet)"][i]}"></span>${facetLabels[k]} ${pct(facetValues[i])}</div>`).join("")}
              </div>
            </div>
            <div class="card card-pad">
              <div class="section-head"><div><div class="section-title">最近发生</div><div class="section-sub">学习证据是长出来的，不是自评的</div></div><a class="badge badge-muted" href="#/insights">洞察 →</a></div>
              <div class="timeline">
                ${(d.activity || []).slice(0, 6).map(ev => {
                  const meta = eventMeta(ev);
                  return `<div class="timeline-item">
                    <span class="timeline-dot">${icon(meta.icon, 12)}</span>
                    <div class="timeline-body"><div class="timeline-title">${escapeHTML(meta.title)}</div><div class="timeline-sub">${relativeTime(ev.created_at)}</div></div>
                  </div>`;
                }).join("") || `<div class="empty" style="padding:24px 0"><p>还没有学习记录。开始第一个闭环。</p></div>`}
              </div>
            </div>
          </div>

          <div class="card card-pad">
            <div class="section-head"><div><div class="section-title">知识地图</div><div class="section-sub">节点是单元，线是前置关系。颜色告诉你哪里该动手。</div></div><a class="btn btn-ghost btn-sm" href="#/map">打开完整地图 ${icon("arrow", 13)}</a></div>
            ${miniMapSVG(mapUnits)}
            <div class="row wrap" style="gap:14px;margin-top:6px">
              ${Object.entries(statusMeta).map(([k, v]) => `<span class="row" style="gap:6px;font-size:10.5px;color:var(--muted)"><span class="map-dot ${k}"></span>${v.label}</span>`).join("")}
            </div>
          </div>
        </div>

        <div class="stack">
          <div class="card card-pad advisor-card" id="advisor-card">
            <div class="between">
              <div><div class="side-label">火候建议</div><div class="section-sub">沿强度阶梯，只推进一步</div></div>
              <span class="badge badge-ember" id="advisor-level">计算中</span>
            </div>
            <div id="advisor-body" data-advice-ready="0">
              <div class="advisor-head mt-3">
                <div class="skeleton" style="width:76px;height:76px;border-radius:50%"></div>
                <div style="flex:1"><div class="skeleton" style="height:16px;width:70%"></div><div class="skeleton mt-2" style="height:12px;width:90%"></div></div>
              </div>
              <div class="skeleton mt-3" style="height:88px"></div>
            </div>
            <div class="divider"></div>
            <div class="between"><span class="muted small">复习到期</span><span class="badge ${due ? "badge-ember" : "badge-muted"}">${due} 张</span></div>
            <div class="between mt-2"><span class="muted small">新卡</span><span class="badge badge-muted">${newCount} 张</span></div>
            <div class="between mt-2"><span class="muted small">最弱维度</span><span class="badge badge-gold">${weakest ? facetLabels[weakest] : "还没有数据"}</span></div>
          </div>

          <div class="card card-pad">
            <div class="section-head" style="margin-bottom:10px"><div><div class="section-title" style="font-size:16px">学习契约</div><div class="section-sub">计划是活的</div></div></div>
            <div class="facet-row"><span class="muted">模式</span><span style="color:var(--text-2)">${plan.strategy?.mode === "minimum_viable" ? "最少必要路径" : "完整路径"}</span><span class="faint mono">v${plan.version || 1}</span></div>
            <div class="facet-row"><span class="muted">每日</span><span style="color:var(--text-2)">${goal.daily_minutes} 分钟</span><span></span></div>
            <div class="facet-row"><span class="muted">暂存</span><span style="color:var(--text-2)">${plan.strategy?.parked?.length || 0} 个单元</span><span></span></div>
            <p class="small muted" style="margin-top:12px;line-height:1.7">${escapeHTML(plan.strategy?.summary || "还没有生成计划。")}</p>
            <a class="btn btn-ghost btn-block mt-3" href="#/map">查看路径与暂存</a>
          </div>

          <div class="card card-pad">
            <div class="section-head" style="margin-bottom:10px"><div><div class="section-title" style="font-size:16px">今天的时间</div><div class="section-sub">张弛有度，别把弦拉断</div></div><span class="badge badge-teal">${stats.streak || 0} 天</span></div>
            <div class="row" style="justify-content:space-between"><span class="muted small">今日投入</span><span><strong>${formatMinutes(stats.today_minutes || 0)}</strong></span></div>
            <div class="progress-line mt-2"><i style="width:${Math.min(100, ((stats.today_minutes || 0) / Math.max(1, goal.daily_minutes)) * 100)}%"></i></div>
            <div class="row" style="justify-content:space-between;margin-top:8px"><span class="faint tiny">计划 ${goal.daily_minutes} 分钟</span><span class="faint tiny">${stats.week_sessions || 0} 个会话</span></div>
          </div>
        </div>
      </div>
    </div>`;

  el.querySelectorAll("[data-unit]").forEach(node => {
    node.addEventListener("click", () => ctx.navigate(`#/unit/${node.dataset.unit}`));
  });
  el.querySelector("[data-style-test]")?.addEventListener("click", () => {
    openStyleTest(ctx, () => {
      ctx.refreshView().catch(() => toast("刷新失败，刷新页面可看到新带法", "info"));
    });
  });

  const advisorState = { advice: null, minutes: Math.min(60, Math.max(10, goal.daily_minutes || 30)) };
  el.querySelector("#advisor-card")?.addEventListener("click", (e) => {
    const minuteBtn = e.target.closest("[data-advice-minutes]");
    if (minuteBtn) { loadAdvice(ctx, Number(minuteBtn.dataset.adviceMinutes), advisorState); return; }
    const refresh = e.target.closest("[data-advice-refresh]");
    if (refresh) { loadAdvice(ctx, advisorState.minutes, advisorState); return; }
    const dismiss = e.target.closest("[data-advice-dismiss]");
    if (dismiss) {
      const a = advisorState.advice;
      if (!a) return;
      adviceRequestSeq += 1;
      const body = document.getElementById("advisor-body");
      if (body) { body.dataset.dismissed = "1"; body.dataset.adviceReady = "1"; }
      ctx.api.post("/api/advice/feedback", { action: "dismissed", advice: a }).catch(() => {});
      if (body) body.innerHTML = `<div class="callout mt-3">已记录：这次先不用建议。你可以自由选择，或重新获取一条。<div class="row mt-2"><button class="btn btn-ghost btn-sm" data-advice-refresh>重新获取建议</button><a class="btn btn-ghost btn-sm" href="#/map">去知识地图</a></div></div>`;
      advisorState.advice = null;
      return;
    }
    const accept = e.target.closest("[data-advice-accept]");
    if (accept) {
      const a = advisorState.advice;
      if (!a || accept.disabled) return;
      accept.disabled = true;
      ctx.api.post("/api/advice/feedback", { action: "accepted", advice: a }).catch(() => {});
      if (a.mode === "socratic" && a.target?.id) return ctx.startSocratic(a.target.id, a.id);
      if (a.mode === "audio" && a.target?.id) return ctx.navigate(`#/unit/${a.target.id}?action=audio`);
      if (a.mode === "digest" && a.target?.id) return ctx.navigate(`#/unit/${a.target.id}?action=digest`);
      return ctx.navigate(a.href || "#/dashboard");
    }
  });
  loadAdvice(ctx, advisorState.minutes, advisorState);
}


let adviceRequestSeq = 0;
const HEAT_COLORS = (v) => v >= 80 ? "var(--teal)" : v >= 40 ? "var(--gold)" : v > 0 ? "var(--ember)" : "var(--faint)";

function advisorHTML(a, currentMinutes) {
  const target = a.target ? `<div class="advisor-target">${escapeHTML(a.target.title)}</div>` : "";
  const levelLabel = a.level === null || a.level === undefined
    ? (a.mode_label || "开始")
    : (a.mode === "benchmark" ? "非档位" : `第 ${a.level} 档 · ${a.mode_label}`);
  const pn = (a.pn?.missing || []).length
    ? `<div class="mt-2"><span class="side-label">还缺这一步</span><div class="advisor-pn mt-2">${a.pn.missing.slice(0, 4).map(x => `<span class="gap-chip">${escapeHTML(x)}</span>`).join("")}</div></div>`
    : "";
  const ladder = (a.ladder || []).map(x => `<div class="ladder-pill ${x.active ? "active" : ""} ${x.done ? "done" : ""}" title="${escapeHTML(x.feature)}">
    <span>${x.level === "gate" ? "★" : x.level}</span>${escapeHTML(x.label)}
  </div>`).join("");
  return `
    <div class="advisor-head mt-3">
      <div class="advisor-heat">
        ${ringSVG(a.heat || 0, { size: 76, stroke: 6, color: HEAT_COLORS(a.heat || 0) })}
        <div><strong>${Math.round(a.heat || 0)}</strong><small>°C</small></div>
      </div>
      <div style="min-width:0;flex:1">
        <div class="row wrap" style="gap:6px">
          <span class="badge ${a.mode === "benchmark" ? "badge-violet" : "badge-ember"}">${escapeHTML(levelLabel)}</span>
          <span class="badge badge-muted">约 ${a.cost_minutes || 0} 分钟</span>
        </div>
        <div class="advisor-title">${escapeHTML(a.title || "下一档")}</div>
        ${target}
      </div>
    </div>
    <div class="advisor-reason">${escapeHTML(a.reason || "")}</div>
    ${pn}
    <div class="advisor-ladder">${ladder}</div>
    <div class="time-chips mt-2">
      ${[...new Set([10, 30, 60, Number(currentMinutes)])].sort((a, b) => a - b).map(m => `<button class="time-chip ${m === currentMinutes ? "active" : ""}" data-advice-minutes="${m}">${m === Number(currentMinutes) && ![10,30,60].includes(m) ? "按计划 " : "我有 "}${m} 分钟</button>`).join("")}
    </div>
    <div class="advisor-actions">
      <button class="btn btn-primary" data-advice-accept>${icon(a.mode === "recall" ? "review" : a.mode === "audio" ? "volume" : a.mode === "benchmark" ? "trophy" : "arrow", 15)} ${escapeHTML(a.cta || "开始")}</button>
      <button class="btn btn-ghost btn-sm" data-advice-dismiss>今天先不用</button>
    </div>`;
}

async function loadAdvice(ctx, minutes, state = null) {
  const body = document.getElementById("advisor-body");
  if (!body) return;
  body.dataset.dismissed = "0";
  const seq = ++adviceRequestSeq;
  if (state) state.minutes = Number(minutes);
  try {
    const a = await ctx.api.get(`/api/advice?minutes=${encodeURIComponent(minutes)}`);
    if (seq !== adviceRequestSeq || body.dataset.dismissed === "1" || !document.body.contains(body)) return;
    body.innerHTML = advisorHTML(a, minutes);
    body.dataset.adviceReady = "1";
    if (state) state.advice = a;
    const level = document.getElementById("advisor-level");
    if (level) level.textContent = a.mode === "benchmark" ? "非档位" : a.mode === "done" ? "完成" : a.mode === "onboarding" ? "开始" : "推荐";
  } catch (e) {
    if (state) state.advice = null;
    body.dataset.adviceReady = "1";
    body.innerHTML = `<div class="callout mt-3">暂时给不出建议：${escapeHTML(e.message || "未知错误")}</div>`;
  }
}
