/* Review: FSRS queue with four ratings and interval previews. */
import {
  escapeHTML, escapeAttr, icon, pct, barChartSVG, renderMarkdown, toast,
  formatMinutes, truncate, overlayOpen,
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
  const mistakeTags = queue.mistake_tags || [];
  let pickedTags = [];
  let tagNudge = false;
  let skipTags = false;
  let onlySelected = false;
  const picked = new Set();
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
        ${(queue.due_total || 0)
          ? `<span class="badge badge-ember">${queue.due_total} 张到期</span>`
          : `<span class="badge badge-muted">今天没有到期的复习</span>`}
        ${(queue.new_total || 0) ? `<span class="badge badge-teal">先来 ${queue.new_total} 张新卡</span>` : ""}
        <span class="badge badge-teal">${stats.reviews_today || 0} 今日已复习</span>
      </div>
    </div>
    <div class="review-layout">
      <div class="review-main" id="review-main">
        <div class="review-head">
          <div class="row" style="gap:10px"><span class="side-label">本轮进度</span><span class="badge badge-muted" id="review-progress-label">0 / ${total}</span></div>
          <div class="review-progress"><i id="review-progress-bar" style="width:0%"></i></div>
          <a class="btn btn-ghost btn-sm" href="#/settings">调整节奏</a>
        </div>
        <div id="review-card-slot"></div>
      </div>
      <aside class="review-side">
        <div class="card" id="last-review" style="display:none"></div>
        <div class="card queue-card">
          <div class="between">
            <span class="side-label">本轮队列</span>
            <span class="row" style="gap:6px">
              <button class="link-btn" type="button" data-queue-select>只练选中</button>
              <button class="link-btn" type="button" data-queue-add>提前加卡</button>
            </span>
          </div>
          <div class="queue-tools">
            <button class="link-btn tiny" type="button" data-queue-all>全选</button>
            <button class="link-btn tiny" type="button" data-queue-none>清空选择</button>
            <span class="faint tiny" id="queue-count"></span>
          </div>
          <div class="queue-list" id="queue-list"></div>
          <div class="faint tiny mt-2">点任意一行可以直接跳到那张卡；已完成会变灰划线。</div>
        </div>
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
          <div class="faint tiny">快捷键：空格翻面，1–4 评分，<code>/</code> 搜索（⌘K 亦可）。不要为了“好看”选记得；系统会按真实记忆排程。</div>
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
    progressBar.style.width = `${all ? (done / all) * 100 : 0}%`;
    paintQueue();
  }

  function queueItemHTML(card, i) {
    const done = i < index;
    const current = i === index;
    return `<div class="queue-item${current ? " current" : ""}${done ? " done" : ""}" data-queue-jump="${i}">
      <input type="checkbox" data-queue-pick="${i}" ${picked.has(card.id) ? "checked" : ""} ${done ? "disabled" : ""} aria-label="选中这张卡">
      <span class="queue-text">
        <span class="queue-title">${escapeHTML(truncate(card.front, 34))}</span>
        <span class="queue-sub">${escapeHTML(truncate(card.unit_title || "", 18))} · ${done ? "已完成" : card.state === "new" ? "新卡" : escapeHTML(card.due_label || "")}</span>
      </span>
    </div>`;
  }

  function paintQueue() {
    const list = el.querySelector("#queue-list");
    if (!list) return;
    list.innerHTML = cards.length
      ? cards.map((c, i) => queueItemHTML(c, i)).join("")
      : `<div class="faint tiny">本轮没有卡片。</div>`;
    const count = el.querySelector("#queue-count");
    if (count) count.textContent = picked.size ? `已选 ${picked.size}` : "";
    const selectBtn = el.querySelector("[data-queue-select]");
    if (selectBtn) selectBtn.textContent = onlySelected ? "恢复整轮" : "只练选中";
  }

  function applyOnlySelected() {
    if (!picked.size) { toast("先勾选几张卡", "info"); return; }
    const chosen = cards.slice(index).filter(c => picked.has(c.id));
    const rest = cards.slice(index).filter(c => !picked.has(c.id));
    cards = cards.slice(0, index).concat(chosen, rest);
    onlySelected = !onlySelected;
    pickedTags = [];
    renderCard();
    toast(onlySelected ? `只练选中的 ${chosen.length} 张，其余排在后面。` : "已恢复整轮顺序。", "success");
  }

  function openAddPicker() {
    let remote = null;
    let loading = true;
    const body = document.createElement("div");
    const paint = () => {
      const items = remote ? remote.cards : [];
      body.innerHTML = `
        <input class="input" id="picker-q" placeholder="搜索卡片正面/背面/单元…" value="${escapeAttr(keyword)}">
        <div class="picker-list" id="picker-list">${loading
          ? '<p class="muted small">正在读取全部卡片…</p>'
          : (items.length ? items.map(c => `
              <label class="picker-item">
                <input type="checkbox" data-pick-card="${escapeAttr(c.id)}" ${inQueue.has(c.id) ? "disabled checked" : ""}>
                <span class="queue-text">
                  <span class="queue-title">${escapeHTML(truncate(c.front, 46))}</span>
                  <span class="queue-sub">${escapeHTML(truncate(c.unit_title || "", 20))} · ${escapeHTML(c.due_label || "")}${inQueue.has(c.id) ? " · 已在本轮" : ""}</span>
                </span>
              </label>`).join("")
              : '<p class="muted small">没有匹配的卡片。</p>')}</div>`;
      const input = body.querySelector("#picker-q");
      let timer = null;
      input?.addEventListener("input", () => {
        keyword = input.value.trim();
        clearTimeout(timer);
        timer = setTimeout(load, 300);
      });
      body.querySelectorAll("[data-pick-card]").forEach(box => box.addEventListener("change", () => {
        const id = box.dataset.pickCard;
        if (box.checked) queueAdds.set(id, items.find(c => c.id === id));
        else queueAdds.delete(id);
        const btn = modal.modal.querySelector(".modal-foot .btn-primary");
        if (btn) btn.textContent = queueAdds.size ? `加进本轮 ${queueAdds.size} 张` : "加进本轮";
      }));
    };
    let keyword = "";
    const queueAdds = new Map();
    const inQueue = new Set(cards.map(c => c.id));
    const load = async () => {
      loading = true;
      paint();
      try {
        remote = await ctx.api.get(`/api/cards?limit=80${keyword ? `&q=${encodeURIComponent(keyword)}` : ""}`);
      } catch (e) {
        remote = { cards: [] };
        toast(e.message || "读取卡片失败", "error");
      }
      loading = false;
      paint();
    };
    const modal = ctx.openModal({
      title: "提前加卡",
      subtitle: "把还没到期、但你现在想练的卡加进本轮",
      body,
      wide: true,
      actions: [
        { label: "取消", kind: "btn-ghost", onClick: () => modal.close() },
        { label: "加进本轮", kind: "btn-primary", onClick: () => {
          if (!queueAdds.size) { toast("先勾选要练的卡", "info"); return; }
          const added = [...queueAdds.values()].filter(c => c && !inQueue.has(c.id));
          cards = cards.concat(added);
          added.forEach(c => inQueue.add(c.id));
          modal.close();
          renderCard();
          toast(`已加进本轮 ${added.length} 张。没到期也能练，只是评分会按你实际记忆更新排程。`, "success", 4200);
        } },
      ],
    });
    load();
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
    slot.innerHTML = `<div class="card flashcard" id="flashcard" data-unit-id="${escapeAttr(card.unit_id || "")}">
      <div class="flashcard-front" id="flash-front" data-selectable data-source="review">
        <div class="flashcard-label">先回忆，再翻面</div>
        <div class="flashcard-text">${escapeHTML(card.front)}</div>
        <div class="flashcard-unit">${escapeHTML(card.unit_title || "")} · ${card.state === "new" ? "新卡" : "到期复习"}</div>
        ${card.mnemonic ? `<div class="mt-2"><button class="hint-chip" id="mnemonic-btn">${icon("hint", 12)} 速记线索</button><div class="callout gold mt-2" id="mnemonic-box" style="display:none;text-align:left">${escapeHTML(card.mnemonic)}</div></div>` : ""}
        <div class="flashcard-divider"></div>
        <div class="recall-block mt-2">
          <textarea class="textarea" id="recall-input" rows="3" aria-label="先用自己的话回忆一遍" placeholder="先不看答案，用自己的话回忆一下——写不出来正好说明这里还没懂。"></textarea>
          <div class="row between mt-1" style="gap:8px">
            <span class="faint tiny">评分后这份回忆会存进单元页的「回忆记录」，方便对照前后的说法</span>
            <button class="skip-recall" type="button" id="skip-recall">想跳过，直接看答案</button>
          </div>
        </div>
        <button class="btn btn-primary btn-lg mt-3" id="reveal-btn">${icon("eye", 16)} 显示答案</button>
        <div class="faint tiny mt-2">空格键也可以</div>
      </div>
      <div class="flashcard-back" id="flash-back" style="display:none" data-selectable data-source="review">
        <div class="flashcard-label">答案 · ${card.reps ? `已复习 ${card.reps} 次` : "第一次学习"}</div>
        <div class="recall-echo" id="recall-echo" style="display:none">
          <div class="side-label">你刚才写的</div>
          <div class="recall-echo-text"></div>
        </div>
        <div class="flashcard-text prose">${renderMarkdown(card.back)}</div>
        ${card.mnemonic ? `<div class="callout gold mt-3" style="text-align:left;font-size:11px">速记：${escapeHTML(card.mnemonic)}</div>` : ""}
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
    <div class="mistake-row" id="mistake-row" style="display:none">
      <div class="mistake-head">
        <span class="side-label">哪里没对上？（选 1–4 个）</span>
        <span class="row" style="gap:8px">
          <span class="faint tiny" id="mistake-hint"></span>
          <button class="link-btn" type="button" data-tag-skip>这次不标</button>
        </span>
      </div>
      <div class="mistake-chips">
        ${mistakeTags.map(t => `<button class="mistake-chip" type="button" data-tag="${escapeAttr(t.key)}">${escapeHTML(t.label)}</button>`).join("")}
      </div>
    </div>
    <div class="faint tiny mt-2" style="text-align:center">评分后，系统会计算下一次到期时间；选“记得”不等于真的记得。</div>`;
    document.getElementById("reveal-btn")?.addEventListener("click", reveal);
    document.getElementById("skip-recall")?.addEventListener("click", reveal);
    document.getElementById("mnemonic-btn")?.addEventListener("click", () => {
      const box = document.getElementById("mnemonic-box");
      if (box) box.style.display = box.style.display === "none" ? "block" : "none";
    });
    document.querySelectorAll("[data-rate]").forEach(btn => btn.addEventListener("click", () => rate(Number(btn.dataset.rate))));
    el.querySelectorAll("[data-tag]").forEach(chip => chip.addEventListener("click", () => {
      const key = chip.dataset.tag;
      const at = pickedTags.indexOf(key);
      if (at >= 0) pickedTags.splice(at, 1);
      else if (pickedTags.length < 4) pickedTags.push(key);
      chip.classList.toggle("on", pickedTags.includes(key));
      const hint = el.querySelector("#mistake-hint");
      if (hint) hint.textContent = pickedTags.length ? `已选 ${pickedTags.length} 个` : "";
      if (pickedTags.length) {
        skipTags = false;
        const mrow = el.querySelector("#mistake-row");
        if (mrow) mrow.classList.remove("nudge");
        const skip = el.querySelector("[data-tag-skip]");
        if (skip) skip.textContent = "这次不标";
      }
    }));
    el.querySelector("[data-tag-skip]")?.addEventListener("click", () => {
      skipTags = true;
      tagNudge = false;
      const mrow = el.querySelector("#mistake-row");
      if (mrow) mrow.classList.remove("nudge");
      const hint = el.querySelector("#mistake-hint");
      if (hint) hint.textContent = "好，这次不标；直接评分即可。";
      const skip = el.querySelector("[data-tag-skip]");
      if (skip) skip.textContent = "已跳过";
    });
    ctx.renderMath(document.getElementById("flash-back"));
    updateProgress();
  }

  function recallText() {
    return (document.getElementById("recall-input")?.value || "").trim();
  }

  function reveal() {
    if (revealed) return;
    revealed = true;
    revealTime = Date.now();
    pickedTags = [];
    tagNudge = false;
    skipTags = false;
    const front = document.getElementById("flash-front");
    const back = document.getElementById("flash-back");
    const row = document.getElementById("rating-row");
    const said = recallText();
    if (said) {
      const echo = document.getElementById("recall-echo");
      const box = document.querySelector(".recall-echo-text");
      if (box) box.textContent = said;
      if (echo) echo.style.display = "block";
    }
    if (front) front.style.display = "none";
    if (back) back.style.display = "block";
    if (row) row.style.display = "grid";
    const mrow = document.getElementById("mistake-row");
    if (mrow && mistakeTags.length) mrow.style.display = "block";
    ctx.renderMath(back);
    document.querySelector("[data-rate='3']")?.focus();
  }

  async function rate(value) {
    if (!revealed || busy) return;
    const card = cards[index];
    if (!card) return;
    // 答错/模糊时要求写下错因：这是后面「按错因刷」的唯一数据来源。
    // 但只挡一次，用户仍可以「跳过」，不把评分变成死路。
    if (value <= 2 && mistakeTags.length && !pickedTags.length && !skipTags && !tagNudge) {
      tagNudge = true;
      const mrow = document.getElementById("mistake-row");
      const hint = document.getElementById("mistake-hint");
      if (mrow) { mrow.classList.add("nudge"); mrow.scrollIntoView({ block: "nearest" }); }
      if (hint) hint.textContent = "选一个错因再评分，帮系统知道该补哪一步；不想标就再点一次评分。";
      // 只抖一下（0.32s，且减少动效时根本没有）会让新人以为「点了没反应」，所以再说一句
      toast("先选一个错因，或者再点一次评分直接跳过", "info", 3600);
      return;
    }
    busy = true;
    const elapsed = revealTime ? Date.now() - revealTime : 0;
    try {
      const res = await ctx.api.post(`/api/review/${encodeURIComponent(card.id)}`, {
        rating: value, elapsed_ms: elapsed, recall: recallText(),
        mistake_tags: pickedTags,
      });
      rated += 1;
      index += 1;
      toast(`下一次：${res.result?.interval_label || "已安排"}`, "success", 2600);
      showUndo(res.undo);
      busy = false;
      renderCard();
    } catch (e) {
      busy = false;
      toast(e.message || "评分失败", "error");
    }
  }

  function showUndo(undo) {
    const box = el.querySelector("#last-review");
    if (!box || !undo) return;
    const label = (RATINGS.find(r => r.value === Number(undo.rating)) || {}).label || undo.rating;
    const before = undo.mastery_before === null || undo.mastery_before === undefined
      ? "首评" : pct(undo.mastery_before);
    box.style.display = "block";
    box.innerHTML = `
      <div class="between"><span class="side-label">刚才这次</span>
        <span class="faint tiny">${escapeHTML(undo.interval_label || "")}</span></div>
      <p class="small mt-1">${escapeHTML(label)} · 掌握度 ${before} → ${pct(undo.mastery_after || 0)}</p>
      <button class="link-btn" type="button" data-undo-review>撤销这次评分</button>`;
    box.querySelector("[data-undo-review]").addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      btn.textContent = "撤销中…";
      try {
        const res = await ctx.api.post("/api/reviews/undo", { op: undo.op });
        toast(res.note || "已撤销这次评分", "success", 4200);
        await ctx.refreshView();
      } catch (err) {
        toast(err.message || "撤销失败", "error", 5200);
        btn.disabled = false;
        btn.textContent = "撤销这次评分";
      }
    });
  }

  el.querySelector("[data-queue-select]")?.addEventListener("click", applyOnlySelected);
  el.querySelector("[data-queue-add]")?.addEventListener("click", openAddPicker);
  el.querySelector("[data-queue-all]")?.addEventListener("click", () => {
    cards.slice(index).forEach(c => picked.add(c.id));
    paintQueue();
    renderCard();
  });
  el.querySelector("[data-queue-none]")?.addEventListener("click", () => {
    picked.clear();
    paintQueue();
    renderCard();
  });
  el.querySelector("#queue-list")?.addEventListener("click", (e) => {
    const box = e.target.closest("[data-queue-pick]");
    if (box) {
      const id = cards[Number(box.dataset.queuePick)]?.id;
      if (!id) return;
      if (box.checked) picked.add(id); else picked.delete(id);
      const count = el.querySelector("#queue-count");
      if (count) count.textContent = picked.size ? `已选 ${picked.size}` : "";
      return;
    }
    const row = e.target.closest("[data-queue-jump]");
    if (!row) return;
    const target = Number(row.dataset.queueJump);
    if (!Number.isInteger(target) || target < index || target >= cards.length) return;
    if (target !== index) {
      const [card] = cards.splice(target, 1);
      cards.splice(index, 0, card);
      pickedTags = [];
      renderCard();
      toast("已跳到这张卡；做完它会回到原来的顺序。", "info", 2600);
    }
  });

  const keyHandler = (e) => {
    const tag = (e.target?.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea") return;
    // 加卡选择器（或其它浮层）打开时，空格与数字属于那个界面
    if (overlayOpen()) return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.code === "Space") { e.preventDefault(); reveal(); return; }
    const n = Number(e.key);
    if (n >= 1 && n <= 4) { e.preventDefault(); rate(n); }
  };
  document.addEventListener("keydown", keyHandler);
  ctx.cleanup(() => document.removeEventListener("keydown", keyHandler));

  renderCard();
}
