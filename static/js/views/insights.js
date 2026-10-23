/* Insights: learning evidence, not vanity metrics. */
import {
  escapeHTML, escapeAttr, icon, pct, radarSVG, lineChartSVG, barChartSVG,
  heatmapHTML, formatMinutes, relativeTime, formatDate, statusMeta,
  aiEnabled, aiMode, aiFailure, aiSuccess,
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
  const heatStates = Object.values(data.statuses || {}).map(s => Number(s.heat) || 0);
  const heatBuckets = [
    { label: "未点火", n: heatStates.filter(x => x <= 0).length, cls: "muted" },
    { label: "温热", n: heatStates.filter(x => x > 0 && x < 40).length, cls: "ember" },
    { label: "升温中", n: heatStates.filter(x => x >= 40 && x < 80).length, cls: "gold" },
    { label: "接近沸腾", n: heatStates.filter(x => x >= 80 && x < 99.5).length, cls: "violet" },
    { label: "已烧开", n: heatStates.filter(x => x >= 99.5).length, cls: "teal" },
  ];
  const records = data.records || [];
  const next = data.next_action || {};
  const sessions = data.recent_sessions || [];
  const gaps = data.gaps || [];
  const mistakes = data.mistakes || {};
  const mistakeTags = mistakes.tags || [];
  const mistakeMax = Math.max(1, ...mistakeTags.map(t => Number(t.count) || 0));

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
      <div class="stat"><span class="stat-icon gold">${icon("flame", 18)}</span><div><div class="stat-value">${Math.round(data.average_heat || 0)}°</div><div class="stat-label">平均火候</div></div></div>
      <div class="stat"><span class="stat-icon violet">${icon("flame", 18)}</span><div><div class="stat-value">${stats.streak || 0} 天</div><div class="stat-label">连续学习</div></div></div>
      <div class="stat"><span class="stat-icon teal">${icon("brain", 18)}</span><div><div class="stat-value">${data.memory_health === null || data.memory_health === undefined ? "—" : pct(data.memory_health)}</div><div class="stat-label">记忆健康度</div></div></div>
    </div>
    <p class="faint tiny mt-2">记忆健康度 = 所有已学卡片此刻平均还能提取多少（按 FSRS 的遗忘曲线算）。它和上面的「30 天保留率」不是一回事：那个看的是过去复习答对的比例，这个看的是现在的状态。目标同样停在 85–90%，掉到 80% 以下就该先补复习。</p>

    <div class="card card-pad mt-4" id="insight-ai-card" data-selectable data-source="insights">
      <div class="section-head">
        <div><div class="section-title">本周解读</div><div class="section-sub">把全部证据与材料总览一起交给模型，再回给你一段判断</div></div>
        ${aiEnabled(ctx) ? `<button class="btn btn-ghost btn-sm" id="insight-ai-btn" type="button">${icon("wand", 13)} 生成解读</button>` : ""}
      </div>
      <div id="insight-ai-body" class="mt-2">
        <p class="muted small">${aiEnabled(ctx)
          ? "还没有生成。点击「生成解读」，AI 会读完掌握度、保留率、火候分布与材料总览后给出结论。"
          : aiMode(ctx) === "live"
            ? "AI 暂时不可用，入口已隐藏；下面的证据与图表都是本地计算的，可以照常使用。"
            : "没有配置模型。证据与图表都来自本地计算；配置模型后可以点这里生成一句话解读。"}</p>
      </div>
    </div>

    <div class="insight-grid mt-4" data-selectable data-source="insights">
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
      <div class="card chart-card">
        <div class="section-head"><div><div class="section-title">错因分布</div><div class="section-sub">答得不好时你自己标的「哪里没对上」</div></div><span class="badge badge-muted">近 ${mistakes.sample || 0} 次复习</span></div>
        ${mistakeTags.length ? `<div class="mistake-dist">${mistakeTags.map(t => `<div class="mistake-dist-row"><span class="mistake-dist-label">${escapeHTML(t.label)}</span><span class="mistake-dist-bar" style="width:${Math.max(4, (Number(t.count) || 0) / mistakeMax * 100)}%"></span><span class="mistake-dist-count">${t.count}</span></div>`).join("")}</div>
          <p class="faint tiny mt-2">${mistakes.tagged_reviews || 0} / ${mistakes.sample || 0} 次复习标了错因，没标的不算在内；共有 ${mistakes.missed_total || 0} 次评成「忘了」或「模糊」。</p>`
          : `<div class="empty" style="padding:22px"><div class="empty-icon">${icon("review", 20)}</div><p>还没有错因记录。复习评「忘了／模糊」时勾一个「哪里没对上」，这里才会积累。</p></div>`}
      </div>
      <div class="card chart-card wide">
        <div class="section-head"><div><div class="section-title">学习热力</div><div class="section-sub">最近 8 周每天投入的分钟数</div></div><span class="badge badge-muted">${formatMinutes(stats.week_minutes || 0)} / 本周</span></div>
        ${heatmapHTML(data.heatmap || [], maxHeat)}
      </div>
      <div class="card chart-card">
        <div class="section-head"><div><div class="section-title">火候分布</div><div class="section-sub">每壶水现在在哪一段</div></div><span class="badge badge-teal">平均 ${Math.round(data.average_heat || 0)}°</span></div>
        <div class="heat-dist">${heatBuckets.map(b => `<div class="heat-bucket"><div class="heat-bucket-top"><strong>${b.n}</strong><span>${b.label}</span></div><div class="heat-bucket-bar ${b.cls}" style="width:${heatStates.length ? Math.max(4, b.n / heatStates.length * 100) : 0}%"></div></div>`).join("")}</div>
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
        <div class="section-head"><div><div class="section-title">火候记录</div><div class="section-sub">同一份 BoilRecord 串起所有模式；温度变化就是学习证据</div></div><span class="badge badge-muted">${records.length} 条</span></div>
        <div class="session-list">
          ${records.length ? records.map(r => {
            const modes = {socratic:"苏格拉底",feynman:"费曼",benchmark:"极致对标",recall:"回测",digest:"预消化",audio:"听讲"};
            const summary = r.result?.summary || (r.result?.overall != null ? `讲解分 ${Math.round(r.result.overall * 100)}` : r.result?.rating ? `评分 ${r.result.rating}/4` : "");
            return `<div class="session-row"><span class="row" style="gap:7px"><span class="badge badge-muted">${modes[r.mode] || r.mode}</span><span class="faint tiny">${escapeHTML(r.unit_title || "")}</span></span><span class="row" style="gap:8px"><span class="session-score">${Math.round(r.heat_before)}° → ${Math.round(r.heat_after)}°</span><span class="faint tiny">${formatDate(r.created_at)}</span></span></div>`;
          }).join("") : `<div class="empty" style="padding:22px"><p>还没有火候记录。完成一次对话、费曼或回测。</p></div>`}
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
      <div class="between" style="gap:16px;flex-wrap:wrap">
        <div style="min-width:0">
          <div class="section-title">下一步</div>
          <div class="section-sub">洞察页只负责看证据；具体该用哪一档，回到今日看火候建议。</div>
          <p class="muted small mt-2">每场只给一个下一步，理由、时间预算和 PN 缺口都会写在那里。</p>
        </div>
        <a class="btn btn-primary" href="#/dashboard">回到今日 ${icon("arrow", 14)}</a>
      </div>
      <div class="divider"></div>
      <div class="grid grid-3">
        <div class="callout ${counts.boiled ? "teal" : ""}"><strong>${counts.boiled || 0}</strong> 个单元四维都过线，才算“烧开”。</div>
        <div class="callout gold"><strong>${counts.learning || 0}</strong> 个单元正在积累证据；继续用不同探针测。</div>
        <div class="callout ember"><strong>${counts.rusting || 0}</strong> 个单元开始变凉；先复习，再学新的。</div>
      </div>
    </div>`;

  const aiBtn = el.querySelector("#insight-ai-btn");
  const aiBody = el.querySelector("#insight-ai-body");
  aiBtn?.addEventListener("click", async () => {
    aiBtn.disabled = true;
    const label = aiBtn.innerHTML;
    aiBtn.innerHTML = "分析中…";
    aiBody.innerHTML = `<p class="muted small">AI 正在把全部证据与材料总览放在一起分析，可能需要十几秒…</p>`;
    try {
      const res = await ctx.api.post("/api/insights/synthesize", {}, { timeout: 120000 });
      aiSuccess(ctx);
      const badge = res.source === "model"
        ? `<span class="badge badge-teal">AI 分析</span>`
        : `<span class="badge badge-muted">本地规则</span>`;
      aiBody.innerHTML = `
        <div class="row wrap" style="gap:8px">${badge}${(res.warnings || []).map(w => `<span class="badge badge-ember">${escapeHTML(w)}</span>`).join("")}</div>
        <p class="ai-summary mt-2">${escapeHTML(res.summary || "")}</p>
        ${(res.suggestions || []).length ? `<div class="side-label mt-2">建议</div><ul class="digest-advice">${res.suggestions.map(s => `<li>${escapeHTML(s)}</li>`).join("")}</ul>` : ""}
        ${(res.risks || []).length ? `<div class="callout ember mt-2"><div class="side-label">风险</div><ul class="digest-advice">${res.risks.map(r => `<li>${escapeHTML(r)}</li>`).join("")}</ul></div>` : ""}`;
    } catch (e) {
      aiFailure(ctx);
      aiBody.innerHTML = `<p class="muted small">生成失败：${escapeHTML(e.message || "未知错误")}。已临时隐藏这个入口，证据与图表照常可用。</p>`;
      aiBtn.style.display = "none";
    } finally {
      aiBtn.disabled = false;
      aiBtn.innerHTML = label;
    }
  });
}
