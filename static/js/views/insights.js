/* Insights: learning evidence, not vanity metrics. */
import {
  escapeHTML, escapeAttr, icon, pct, radarSVG, lineChartSVG, barChartSVG,
  heatmapHTML, formatMinutes, relativeTime, formatDate, statusMeta,
} from "../utils.js";

const FACETS = { recall: "回忆", explain: "解释", apply: "应用", transfer: "迁移" };

export async function render(el, ctx, params) {
  let data;
  try {
    data = await ctx.api.get("/api/insights");
  } catch (e) {
    if (e.status === 400 || e.status === 404) { ctx.navigate("#/onboarding", { replace: true }); return; }
    throw e;
  }
  const m = data.mastery || {};
  const counts = data.counts || {};
  const stats = data.stats || {};
  const values = ["recall", "explain", "apply", "transfer"].map(k => m.facets?.[k] || 0);
  const retentionPoints = (data.retention || []).map(r => ({
    label: r.date?.slice(5) || "", value: Number(r.rate || 0),
  }));
  const forecast = (data.forecast || []).map(f => ({ label: f.weekday, value: f.due }));
  const forecastTotal = forecast.reduce((a, b) => a + (Number(b.value) || 0), 0);
  const maxHeat = Math.max(60, ...(data.heatmap || []).map(d => d.minutes || 0));
  const next = data.next_action || {};
  const sessions = data.recent_sessions || [];
  const gaps = data.gaps || [];

  el.innerHTML = `
    <div class="page-head">
      <div>
        <h2>学习证据</h2>
        <p>${escapeHTML(data.goal?.title || "")} · 不看“学了多少”，看“30 天后还能提取、解释、迁移多少”。</p>
      </div>
      <div class="row wrap">
        <span class="badge badge-teal">${counts.boiled || 0} 已烧开</span>
        <span class="badge badge-gold">${counts.learning || 0} 学习中</span>
        <span class="badge badge-ember">${counts.rusting || 0} 需复习</span>
      </div>
    </div>

    <div class="stats-grid">
      <div class="stat"><span class="stat-icon">${icon("brain", 18)}</span><div><div class="stat-value">${pct(m.average || 0)}</div><div class="stat-label">平均掌握度</div></div></div>
      <div class="stat"><span class="stat-icon teal">${icon("review", 18)}</span><div><div class="stat-value">${pct(stats.retention_30d || 0)}</div><div class="stat-label">30 天保留率</div></div></div>
      <div class="stat"><span class="stat-icon gold">${icon("clock", 18)}</span><div><div class="stat-value">${formatMinutes(stats.week_minutes || 0)}</div><div class="stat-label">本周投入</div></div></div>
      <div class="stat"><span class="stat-icon violet">${icon("flame", 18)}</span><div><div class="stat-value">${stats.streak || 0} 天</div><div class="stat-label">连续学习</div></div></div>
    </div>

    <div class="insight-grid mt-4">
      <div class="card chart-card">
        <div class="section-head"><div><div class="section-title">掌握雷达</div><div class="section-sub">四维证据的平均值</div></div><a class="badge badge-muted" href="#/map">看地图 →</a></div>
        <div class="radar-wrap">${radarSVG(values, ["回忆", "解释", "应用", "迁移"], { size: 240 })}</div>
        <div class="radar-legend">
          ${["recall", "explain", "apply", "transfer"].map((k, i) => `<div><span class="legend-dot" style="background:${["var(--ember)","var(--gold)","var(--teal)","var(--violet)"][i]}"></span>${FACETS[k]} ${pct(values[i])}</div>`).join("")}
        </div>
      </div>
      <div class="card chart-card">
        <div class="section-head"><div><div class="section-title">保留率趋势</div><div class="section-sub">每次复习前的可提取概率</div></div><span class="badge badge-teal">近 30 天</span></div>
        ${lineChartSVG(retentionPoints, { width: 440, height: 210, color: "var(--teal)" })}
        <p class="faint tiny mt-2">保留率不是越高越好；目标是 85–90% 附近，保留一点“合意困难”更有利于长期记忆。</p>
      </div>
      <div class="card chart-card wide">
        <div class="section-head"><div><div class="section-title">学习热力</div><div class="section-sub">最近 8 周每天投入的分钟数</div></div><span class="badge badge-muted">${formatMinutes(stats.week_minutes || 0)} / 本周</span></div>
        ${heatmapHTML(data.heatmap || [], maxHeat)}
      </div>
      <div class="card chart-card">
        <div class="section-head"><div><div class="section-title">未来 7 天到期</div><div class="section-sub">提前知道哪一天会忙</div></div><a class="btn btn-ghost btn-sm" href="#/review">去复习</a></div>
        ${forecastTotal ? barChartSVG(forecast, { width: 440, height: 180, color: "var(--ember)" }) : `<div class="empty" style="padding:26px"><div class="empty-icon">${icon("trophy", 20)}</div><p>未来 7 天没有到期卡。</p></div>`}
      </div>
      <div class="card chart-card">
        <div class="section-head"><div><div class="section-title">还模糊的地方</div><div class="section-sub">来自苏格拉底报告的真实缺口</div></div><span class="badge badge-ember">${gaps.length}</span></div>
        <div class="gaps-list">
          ${gaps.length ? gaps.map(g => `<a class="gap-item" href="#/unit/${escapeAttr(g.unit_id)}"><span class="g">!</span><span>${escapeHTML(g.gap)}</span><span class="faint" style="margin-left:auto;white-space:nowrap">${relativeTime(g.at)}</span></a>`).join("") : `<div class="empty" style="padding:22px"><p>还没有记录到缺口。完成一次苏格拉底对话或费曼挑战。</p></div>`}
        </div>
      </div>
      <div class="card chart-card wide">
        <div class="section-head"><div><div class="section-title">最近会话</div><div class="section-sub">对话与讲解都会变成证据</div></div><a class="btn btn-ghost btn-sm" href="#/map">继续学习 ${icon("arrow", 13)}</a></div>
        <div class="session-list">
          ${sessions.length ? sessions.map(s => {
            const href = s.kind === "socratic" ? `#/socratic/${escapeAttr(s.id)}` : "#/map";
            return `<a class="session-row" href="${href}">
              <span class="row" style="gap:8px">${icon(s.kind === "socratic" ? "chat" : "teach", 13)}<span>${s.kind === "socratic" ? "苏格拉底对话" : "学习会话"} · ${formatDate(s.started_at)}</span></span>
              <span class="row" style="gap:8px"><span class="faint tiny">${s.turn || 0} 轮</span><span class="badge ${s.status === "completed" ? "badge-teal" : "badge-muted"}">${s.status === "completed" ? "已完成" : "进行中"}</span></span>
            </a>`;
          }).join("") : `<div class="empty" style="padding:22px"><p>还没有会话。</p></div>`}
        </div>
      </div>
    </div>

    <div class="card card-pad mt-4">
      <div class="section-head"><div><div class="section-title">下一步建议</div><div class="section-sub">系统根据到期、掌握度和最弱维度给出</div></div>${icon("compass", 20)}</div>
      <div class="between" style="gap:16px;flex-wrap:wrap">
        <div style="min-width:0"><strong style="font-size:15px">${escapeHTML(next.title || "保持节奏")}</strong><p class="muted small mt-2">${escapeHTML(next.subtitle || "完成一个闭环，留下证据。")}</p></div>
        ${next.href ? `<a class="btn btn-primary" href="${next.href}">${escapeHTML(next.cta || "开始")} ${icon("arrow", 14)}</a>` : ""}
      </div>
      <div class="divider"></div>
      <div class="grid grid-3">
        <div class="callout ${counts.boiled ? "teal" : ""}"><strong>${counts.boiled || 0}</strong> 个单元四维都过线，才算“烧开”。</div>
        <div class="callout gold"><strong>${counts.learning || 0}</strong> 个单元正在积累证据；继续用不同探针测。</div>
        <div class="callout ember"><strong>${counts.rusting || 0}</strong> 个单元开始变凉；先复习，再学新的。</div>
      </div>
    </div>`;
}
