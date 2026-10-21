/* Review: FSRS queue with four ratings and interval previews. */
import {
  escapeHTML, escapeAttr, icon, pct, barChartSVG, renderMarkdown, toast,
  formatMinutes,
} from "../utils.js";

const RATINGS = [
  { value: 1, key: "1", label: "忘了", cls: "again", hint: "完全想不起来" },
  { value: 2, key: "2", label: "模糊", cls: "hard", hint: "想起来一部分" },
  { value: 3, key: "3", label: "记得", cls: "good", hint: "基本完整" },
  { value: 4, key: "4", label: "很简单", cls: "easy", hint: "毫不费力" },
];

function emptyHTML(ctx) {
  return `<div class="empty" style="padding-top:12vh">
    <div class="empty-icon">${icon("trophy", 24)}</div>
    <h3>这一轮复习清空了</h3>
    <p>没有到期卡片。去学一个新单元，或者让系统明天再来找你。</p>
    <div class="row mt-3" style="justify-content:center">
      <a class="btn btn-primary" href="#/map">去知识地图</a>
      <button class="btn btn-ghost" data-reload>${icon("refresh", 14)} 再检查一遍</button>
    </div>
  </div>`;
}

export async function render(el, ctx, params) {
  let queue, insights = null;
  try {
    [queue, insights] = await Promise.all([
      ctx.api.get("/api/review?limit=40"),
      ctx.api.get("/api/insights").catch(() => null),
    ]);
  } catch (e) {
    if (e.status === 404) { ctx.navigate("#/onboarding", { replace: true }); return; }
    throw e;
  }
  let cards = queue.cards || [];
  let index = 0;
  let revealed = false;
  let revealTime = 0;
  let busy = false;
  let rated = 0;
  let startedAt = Date.now();
  const total = cards.length;
  const stats = queue.stats || {};
  const forecast = (insights?.forecast || []).map(f => ({ label: f.weekday, value: f.due }));
  const forecastTotal = forecast.reduce((a, b) => a + (Number(b.value) || 0), 0);

  el.innerHTML = `
    <div class="page-head">
      <div>
        <h2>今日复习</h2>
        <p>不是重读，是提取。先想，再翻面；按真实记忆程度评分。</p>
      </div>
      <div class="row wrap">
        <span class="badge badge-ember">${queue.due_total || 0} 张到期</span>
        <span class="badge badge-muted">${queue.new_total || 0} 张新卡</span>
        <span class="badge badge-teal">${stats.reviews_today || 0} 今日已复习</span>
      </div>
    </div>
    <div class="review-layout">
      <div class="review-main" id="review-main">
        <div class="review-head">
          <div class="row" style="gap:10px"><span class="side-label">本轮到卡</span><span class="badge badge-muted" id="review-progress-label">0 / ${total}</span></div>
          <div class="review-progress"><i id="review-progress-bar" style="width:0%"></i></div>
          <a class="btn btn-ghost btn-sm" href="#/settings">调整节奏</a>
        </div>
        <div id="review-card-slot"></div>
      </div>
      <aside class="review-side">
        <div class="card">
          <div class="side-label">未来 7 天到期</div>
          ${forecastTotal ? barChartSVG(forecast, { width: 280, height: 140, color: "var(--ember)" }) : `<div class="empty" style="padding:24px 8px"><div class="empty-icon" style="width:42px;height:42px">${icon("trophy", 18)}</div><p>未来 7 天没有到期卡。</p></div>`}
          <div class="faint tiny mt-2">按当前 FSRS 排程预测；实际会随评分变化。</div>
        </div>
        <div class="card">
          <div class="side-label">记忆状态</div>
          <div class="facet-row"><span class="muted">30 天保留率</span><span>${pct(stats.retention_30d || 0)}</span><span></span></div>
          <div class="facet-row"><span class="muted">今日复习</span><span>${stats.reviews_today || 0} 张</span><span></span></div>
          <div class="facet-row"><span class="muted">连续学习</span><span>${stats.streak || 0} 天</span><span></span></div>
          <div class="facet-row"><span class="muted">今天投入</span><span>${formatMinutes(stats.today_minutes || 0)}</span><span></span></div>
        </div>
        <div class="card">
          <div class="side-label">评分怎么选</div>
          <div class="rule-list">
            <div class="rule"><span class="n">1</span><span>忘了：完全想不起来，或答案错了。</span></div>
            <div class="rule"><span class="n">2</span><span>模糊：想起来一部分，但不确定。</span></div>
            <div class="rule"><span class="n">3</span><span>记得：基本完整，有一点小卡顿。</span></div>
            <div class="rule"><span class="n">4</span><span>很简单：毫不费力，能立即复述。</span></div>
          </div>
          <div class="divider"></div>
          <div class="faint tiny">快捷键：空格翻面，1–4 评分。不要为了“好看”选记得；系统会按真实记忆排程。</div>
        </div>
      </aside>
    </div>`;

  const slot = el.querySelector("#review-card-slot");
  const progressLabel = el.querySelector("#review-progress-label");
  const progressBar = el.querySelector("#review-progress-bar");

  function updateProgress() {
    const done = rated;
    const all = rated + (cards.length - index);
    progressLabel.textContent = `${done} / ${all}`;
    progressBar.style.width = `${all ? (done / all) * 100 : 100}%`;
  }

  function renderEmpty() {
    slot.innerHTML = `<div class="card card-pad">
      <div class="empty" style="padding:56px 18px">
        <div class="empty-icon">${icon("trophy", 24)}</div>
        <h3>${rated ? "这一轮复习完成" : "现在没有到期卡片"}</h3>
        <p>${rated ? `你完成了 ${rated} 张卡的提取。下一轮会按 FSRS 自动安排。` : "去学一个新单元，或者等系统明天来找你。"}</p>
        <div class="row mt-3" style="justify-content:center;flex-wrap:wrap">
          <a class="btn btn-primary" href="#/map">去知识地图</a>
          <button class="btn btn-ghost" data-reload>${icon("refresh", 14)} 再检查一遍</button>
        </div>
      </div>
    </div>`;
    slot.querySelector("[data-reload]")?.addEventListener("click", () => location.reload());
    ctx.refreshDashboard().catch(() => {});
  }

  function renderCard() {
    const card = cards[index];
    if (!card) { renderEmpty(); return; }
    revealed = false;
    revealTime = 0;
    const preview = card.preview || {};
    slot.innerHTML = `<div class="card flashcard" id="flashcard">
      <div class="flashcard-front" id="flash-front">
        <div class="flashcard-label">先回忆，再翻面</div>
        <div class="flashcard-text">${escapeHTML(card.front)}</div>
        <div class="flashcard-unit">${escapeHTML(card.unit_title || "")} · ${card.state === "new" ? "新卡" : "到期复习"}</div>
        <div class="flashcard-divider"></div>
        <button class="btn btn-primary btn-lg mt-3" id="reveal-btn">${icon("eye", 16)} 显示答案</button>
        <div class="faint tiny mt-2">空格键也可以</div>
      </div>
      <div class="flashcard-back" id="flash-back" style="display:none">
        <div class="flashcard-label">答案 · ${card.reps ? `已复习 ${card.reps} 次` : "第一次学习"}</div>
        <div class="flashcard-text prose">${renderMarkdown(card.back)}</div>
        <div class="flashcard-unit">${escapeHTML(card.unit_title || "")}</div>
      </div>
    </div>
    <div class="rating-row mt-3" id="rating-row" style="display:none">
      ${RATINGS.map(r => `<button class="rating-btn ${r.cls}" data-rate="${r.value}">
        <span class="key">${r.key}</span>
        <strong>${r.label}</strong>
        <span>${escapeHTML(preview[String(r.value)] || r.hint)}</span>
      </button>`).join("")}
    </div>
    <div class="faint tiny mt-2" style="text-align:center">评分后，系统会计算下一次到期时间；选“记得”不等于真的记得。</div>`;
    document.getElementById("reveal-btn")?.addEventListener("click", reveal);
    document.querySelectorAll("[data-rate]").forEach(btn => btn.addEventListener("click", () => rate(Number(btn.dataset.rate))));
    ctx.renderMath(document.getElementById("flash-back"));
    updateProgress();
  }

  function reveal() {
    if (revealed) return;
    revealed = true;
    revealTime = Date.now();
    const front = document.getElementById("flash-front");
    const back = document.getElementById("flash-back");
    const row = document.getElementById("rating-row");
    if (front) front.style.display = "none";
    if (back) back.style.display = "block";
    if (row) row.style.display = "grid";
    ctx.renderMath(back);
    document.querySelector("[data-rate='3']")?.focus();
  }

  async function rate(value) {
    if (!revealed || busy) return;
    const card = cards[index];
    if (!card) return;
    busy = true;
    const elapsed = revealTime ? Date.now() - revealTime : 0;
    try {
      const res = await ctx.api.post(`/api/review/${encodeURIComponent(card.id)}`, { rating: value, elapsed_ms: elapsed });
      rated += 1;
      index += 1;
      toast(`下一次：${res.result?.interval_label || "已安排"}`, "success", 2600);
      busy = false;
      renderCard();
    } catch (e) {
      busy = false;
      toast(e.message || "评分失败", "error");
    }
  }

  const keyHandler = (e) => {
    const tag = (e.target?.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea") return;
    if (e.code === "Space") { e.preventDefault(); reveal(); return; }
    const n = Number(e.key);
    if (n >= 1 && n <= 4) { e.preventDefault(); rate(n); }
  };
  document.addEventListener("keydown", keyHandler);
  ctx.cleanup(() => document.removeEventListener("keydown", keyHandler));

  renderCard();
}
