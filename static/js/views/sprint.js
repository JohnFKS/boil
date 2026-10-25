/* 备考冲刺：错题本、考前速背、限时自测卷。
 *
 * 这三件事在长期学习里是配角，在"离考试还有八天"时是主角。它们和 FSRS 队列刻意分开：
 * 间隔重复管的是"以后别忘了"，冲刺管的是"这两天别再丢分"，用一个排程同时满足两者
 * 只会两头都做坏。 */
import {
  escapeHTML, escapeAttr, icon, toast, relativeTime, confirmDialog,
} from "../utils.js";


function tagRow(items) {
  return (items || []).map((t) => `<span class="badge badge-muted">${escapeHTML(t.label)}${t.count ? ` ×${t.count}` : ""}</span>`).join("");
}

function verdictBadge(verdict) {
  const map = {
    correct: ["✓", "teal", "答对"], partial: ["△", "gold", "部分得分"],
    wrong: ["✗", "ember", "答错"], blank: ["—", "muted", "没作答"],
    ungraded: ["?", "muted", "没有评分要点，需自己核对"],
  };
  const [mark, tone, label] = map[verdict] || ["?", "muted", "未知"];
  return `<span class="badge badge-${tone}">${mark} ${label}</span>`;
}

function mistakeHTML(item) {
  return `
    <article class="card card-pad mistake-item" data-card="${escapeAttr(item.id)}">
      <div class="section-head">
        <div><div class="section-title">${escapeHTML(item.front)}</div>
          <div class="section-sub">${escapeHTML(item.unit_title)} · 最近错于 ${escapeHTML(relativeTime(item.last_wrong_at))}</div></div>
        <span class="badge badge-ember">错 ${item.wrongs} 次</span>
      </div>
      <div class="row wrap mt-2 mistake-tags" style="gap:6px">${tagRow(item.tag_counts)}</div>
      <details class="mt-2"><summary class="faint small">看答案</summary>
        <p class="prose mt-2">${escapeHTML(item.back)}</p>
      </details>
      <div class="row wrap mt-2" style="gap:8px">
        <label class="tiny muted" style="display:flex;gap:6px;align-items:center">
          <input type="checkbox" data-pick-mistake="${escapeAttr(item.id)}" checked>重练这张
        </label>
        <a class="badge badge-muted" href="#/unit/${escapeAttr(item.unit_id)}">回到单元</a>
        <span class="badge badge-muted">${escapeHTML(item.due_label)}</span>
      </div>
    </article>`;
}

function examMissHTML(item) {
  return `
    <article class="card card-pad exam-miss">
      <div class="section-head">
        <div><div class="section-title">${escapeHTML(item.prompt)}</div>
          <div class="section-sub">${escapeHTML(item.unit_title)} · ${escapeHTML(relativeTime(item.at))} · ${escapeHTML(item.exam_title)}</div></div>
        ${verdictBadge(item.verdict)}
      </div>
      ${item.yours ? `<p class="small mt-2">你写的：${escapeHTML(item.yours)}</p>` : '<p class="small mt-2 faint">这题空着没写。</p>'}
      <details class="mt-2" open><summary class="faint small">标准答案</summary>
        <p class="prose mt-2">${escapeHTML(item.answer)}</p>
        ${item.missed?.length ? `<div class="faint tiny mt-2">没覆盖到的要点：${item.missed.map(escapeHTML).join("、")}</div>` : ""}
      </details>
    </article>`;
}

function cramHTML(item) {
  const probes = (item.probes || []).map((p) => `
    <li class="cram-probe"><strong>${escapeHTML(p.prompt)}</strong>
      <details><summary class="faint small">看参考答案</summary><p class="mt-2">${escapeHTML(p.answer)}</p></details>
    </li>`).join("");
  const points = (item.key_points || []).map((k) => `<li>${escapeHTML(typeof k === "string" ? k : JSON.stringify(k))}</li>`).join("");
  const warns = (item.misconceptions || []).map((m) => `<li>${escapeHTML(typeof m === "string" ? m : JSON.stringify(m))}</li>`).join("");
  return `
    <article class="card card-pad cram-item" data-unit="${escapeAttr(item.unit_id)}">
      <div class="section-head">
        <div><div class="section-title">${escapeHTML(item.title)}</div>
          <div class="section-sub">${escapeHTML(item.reason)} · 掌握度 ${(item.mastery * 100).toFixed(0)}%${item.due_count ? ` · ${item.due_count} 张卡到期` : ""}${item.mistakes ? ` · 错过 ${item.mistakes} 次` : ""}</div></div>
        <button class="btn btn-ghost btn-sm" data-shot="${escapeAttr(item.unit_id)}">${icon("check", 14)} 背熟了，斩掉</button>
      </div>
      <p class="cram-essence mt-2">${escapeHTML(item.essence)}</p>
      ${points ? `<div class="cram-block"><div class="side-label">必背要点</div><ul class="prose">${points}</ul></div>` : ""}
      ${item.mnemonic ? `<div class="cram-block"><div class="side-label">口诀</div><p class="small">${escapeHTML(item.mnemonic)}</p></div>` : ""}
      ${probes ? `<div class="cram-block"><div class="side-label">常见考法</div><ul class="prose">${probes}</ul></div>` : ""}
      ${warns ? `<div class="cram-block"><div class="side-label">易错提醒</div><ul class="prose">${warns}</ul></div>` : ""}
    </article>`;
}

function resultHTML(data) {
  const res = data.result || {};
  const counts = res.counts || {};
  const score = res.score === null || res.score === undefined ? "—" : `${(res.score * 100).toFixed(0)}%`;
  const rows = (res.items || []).map((item, i) => `
    <div class="exam-grade-row">
      <span class="exam-no">${i + 1}</span>
      <div class="exam-grade-body">
        <div class="small">${escapeHTML(item.prompt)}</div>
        <div class="row wrap mt-2" style="gap:6px">${verdictBadge(item.verdict)}
          ${item.score !== null && item.score !== undefined ? `<span class="badge badge-muted">要点覆盖 ${(item.score * 100).toFixed(0)}%</span>` : ""}
          ${item.missed?.length ? `<span class="faint tiny">漏了：${item.missed.map(escapeHTML).join("、")}</span>` : ""}
        </div>
        <details class="mt-2"><summary class="faint small">标准答案</summary><p class="prose mt-2">${escapeHTML(item.answer)}</p></details>
      </div>
    </div>`).join("");
  return `
    <div class="card card-pad exam-result" id="exam-result">
      <div class="section-head">
        <div><div class="section-title">${escapeHTML(data.title)} · 判分</div>
          <div class="section-sub">规则判分（按资料里的评分要点），不模型也不编结论</div></div>
        <div class="row" style="gap:8px"><span class="badge badge-gold">得分 ${score}</span>
          <button class="btn btn-ghost btn-sm" data-print="exam">${icon("download", 14)} 打印这份卷子</button></div>
      </div>
      <div class="grid grid-3 mt-3">
        <div class="callout teal"><strong>${counts.correct || 0}</strong> 题答对。</div>
        <div class="callout gold"><strong>${counts.partial || 0}</strong> 题部分得分。</div>
        <div class="callout ember"><strong>${(counts.wrong || 0) + (counts.blank || 0)}</strong> 题答错或空着，已进错题本。</div>
      </div>
      ${res.needs_self_check ? `<p class="small mt-3">有 ${res.needs_self_check} 道题没有评分要点，系统没有替你判分 —— 请自己对照答案。</p>` : ""}
      <div class="divider"></div>
      <div class="exam-grade-list">${rows}</div>
      <p class="faint tiny mt-3">分数按"每题要点覆盖率"的平均值算；空题算 0。这份卷子不能重交，要再来一次就重新组卷。</p>
    </div>`;
}

function shell(data) {
  const book = data.book || {};
  const cram = data.cram || {};
  const totals = book.totals || {};
  const daysLeft = cram.days_left;
  const countdown = daysLeft === null || daysLeft === undefined
    ? "没设截止日期"
    : daysLeft < 0 ? "截止日期已过" : daysLeft === 0 ? "今天考" : `倒计时 ${daysLeft} 天`;
  return `
    <div class="sprint-head card card-pad">
      <div class="section-head">
        <div><div class="section-title">备考冲刺</div>
          <div class="section-sub">错题本、考前速背、限时自测卷 —— 这三样按"这两天别再丢分"来排，不动你的间隔重复排程</div></div>
        <span class="badge ${daysLeft !== null && daysLeft <= 7 ? "badge-ember" : "badge-muted"}">${countdown}</span>
      </div>
      <div class="grid grid-3 mt-3">
        <div class="callout ember"><strong>${totals.cards || 0}</strong> 张卡近 ${book.days || 30} 天答错过；${totals.due_now || 0} 张已经到期。</div>
        <div class="callout"><strong>${cram.left || 0}</strong> 个单元还在速背清单上（已斩 ${cram.shot || 0} / 共 ${cram.total || 0}）。</div>
        <div class="callout gold"><strong>${(data.exams?.exams || []).length}</strong> 份自测卷记录；最近一次
          ${(data.exams?.exams || []).find((e) => e.status === "graded") ? `${((data.exams.exams.find((e) => e.status === "graded").score || 0) * 100).toFixed(0)}%` : "还没交卷"}。</div>
      </div>
    </div>

    <section class="card card-pad mt-4 sprint-section" id="sprint-mistakes">
      <div class="section-head">
        <div><div class="section-title">错题本</div>
          <div class="section-sub">只列"现在还会错"的：后来答对回去的自动退出本子</div></div>
        <div class="row" style="gap:8px">
          <button class="btn btn-ghost btn-sm" data-practice>${icon("review", 14)} 重练勾上的 ${(totals.cards || 0)} 张</button>
          <button class="btn btn-ghost btn-sm" data-print="mistakes">${icon("download", 14)} 打印</button>
        </div>
      </div>
      ${tagRow(totals.tags || [])}
      <div id="mistake-list" class="mt-3">
        ${(book.items || []).length ? book.items.map(mistakeHTML).join("")
          : '<p class="muted">近 30 天没有答错过的卡。要么还没复习过，要么你真的记住了。</p>'}
      </div>
      ${(book.exam_items || []).length ? `
        <div class="divider"></div>
        <div class="section-title mt-2">自测卷里丢分的题</div>
        <p class="faint tiny">这些题没有对应的复习卡，靠卷子本身重看；判分规则与答案都留在原地。</p>
        <div class="mt-2">${book.exam_items.map(examMissHTML).join("")}</div>` : ""}
    </section>

    <section class="card card-pad mt-4 sprint-section" id="sprint-cram">
      <div class="section-head">
        <div><div class="section-title">考前速背</div>
          <div class="section-sub">按 截止日期 × 重要度 × 掌握度 排序，背一个斩一个；斩掉只影响这份清单，不改排程</div></div>
        <div class="row" style="gap:8px">
          <button class="btn btn-ghost btn-sm" data-print="cram">${icon("download", 14)} 打印速背清单</button>
          <button class="btn btn-ghost btn-sm" data-cram-reset>全部重列</button>
        </div>
      </div>
      <div id="cram-list" class="mt-3">
        ${(cram.items || []).length ? cram.items.map(cramHTML).join("")
          : `<p class="muted">${cram.has_goal ? "清单空了：这个目标的单元都斩完了，或者还没有学过的单元。" : "还没有目标：先去今日页开一门。"}</p>`}
      </div>
    </section>

    <section class="card card-pad mt-4 sprint-section" id="sprint-exam">
      <div class="section-head">
        <div><div class="section-title">限时自测卷</div>
          <div class="section-sub">题目只从已编译的探测题里出，不现编；每个单元最多 2 题，逼出跨单元覆盖</div></div>
      </div>
      <div class="form-grid mt-3">
        <div class="field"><label for="ex-minutes">时长（分钟）</label>
          <input class="input" id="ex-minutes" type="number" min="5" max="180" step="5" value="25"></div>
        <div class="field"><label for="ex-count">题数</label>
          <input class="input" id="ex-count" type="number" min="3" max="25" step="1" value="8"></div>
      </div>
      <div class="row wrap mt-3" style="gap:8px">
        <button class="btn btn-primary" data-exam-build>${icon("review", 15)} 组一卷</button>
        <span class="faint tiny">判分按资料里的评分要点走；没写要点的题会标成"需要自己核对"，不会假装判过。</span>
      </div>
      <div id="exam-paper" class="mt-3"></div>
      <div id="exam-history" class="mt-3">
        ${(data.exams?.exams || []).length ? `<div class="side-label">最近的卷子</div>
          ${(data.exams.exams).map((e) => `
            <div class="row exam-history-row">
              <span class="badge badge-muted">${escapeHTML(e.status === "graded" ? "已判" : "未交")}</span>
              <span class="small">${escapeHTML(e.title)} · ${e.count} 题 · ${e.minutes} 分钟</span>
              <span class="faint tiny">${escapeHTML(relativeTime(e.started_at))}</span>
              ${e.status === "graded" ? `<span class="badge badge-gold">${(((e.score || 0)) * 100).toFixed(0)}%</span>` : ""}
            </div>`).join("")}` : ""}
      </div>
    </section>`;
}

export async function render(el, ctx, params) {
  if (!ctx.state.dashboard?.has_goal) {
    el.innerHTML = `<p class="muted">冲刺页需要一个进行中的目标：先去<a href="#/onboarding">开一门</a>。</p>`;
    return;
  }
  async function loadAll() {
    const [book, cram, exams] = await Promise.all([
      ctx.api.get("/api/mistakes", { timeout: 30000 }),
      ctx.api.get("/api/cram", { timeout: 30000 }),
      ctx.api.get("/api/exams", { timeout: 30000 }),
    ]);
    return { book, cram, exams };
  }

  let data = await loadAll();
  let paper = null;                 // 当前正在做的卷子
  let ticker = null;

  function paint() {
    el.innerHTML = shell(data);
    if (paper) renderPaper();
    bind();
  }

  function renderPaper() {
    const host = el.querySelector("#exam-paper");
    if (!host) return;
    host.innerHTML = `
      <div class="exam-paper" id="exam-paper-inner">
        <div class="section-head">
          <div><div class="section-title">${escapeHTML(paper.title)}</div>
            <div class="section-sub">${paper.questions.length} 题 · 限时 ${paper.minutes} 分钟${paper.note ? ` · ${escapeHTML(paper.note)}` : ""}</div></div>
          <span class="badge badge-ember" id="exam-clock">剩余 ${paper.left} 秒</span>
        </div>
        <div class="exam-actions row wrap mt-2" style="gap:8px">
          <button class="btn btn-primary btn-sm" data-exam-submit>${icon("check", 14)} 交卷并判分</button>
          <button class="btn btn-ghost btn-sm" data-exam-reveal>${icon("spark", 14)} 答案速览（先自己写完再用）</button>
          <button class="btn btn-ghost btn-sm" data-exam-abandon>不做了</button>
        </div>
        <ol class="exam-list mt-3">
          ${paper.questions.map((q, i) => `
            <li class="exam-q" data-q="${escapeAttr(q.id)}">
              <div class="exam-q-head"><span class="exam-no">${i + 1}</span>
                <span class="exam-prompt">${escapeHTML(q.prompt)}</span>
                <span class="faint tiny">${escapeHTML(q.unit_title)}</span></div>
              <textarea class="textarea mt-2" data-answer="${escapeAttr(q.id)}" rows="3"
                placeholder="合上书，用自己的话写下来。写不出来正好说明这里还没懂。"></textarea>
              <div class="exam-key faint tiny mt-2" hidden>参考答案：${escapeHTML(q.answer || "（这份卷子没带答案）")}</div>
            </li>`).join("")}
        </ol>
      </div>`;
    const clock = el.querySelector("#exam-clock");
    if (clock) clock.classList.toggle("warn", paper.left <= 60);
    bindPaper();
  }

  function startTicker() {
    if (ticker) clearInterval(ticker);
    ticker = setInterval(() => {
      if (!paper) return;
      paper.left -= 1;
      const node = el.querySelector("#exam-clock");
      if (!node) return;
      if (paper.left <= 0) {
        paper.left = 0;
        node.textContent = "时间到（还能继续写或交卷）";
        node.classList.add("warn");
        clearInterval(ticker);
        ticker = null;
        return;
      }
      const mins = Math.floor(paper.left / 60), secs = paper.left % 60;
      node.textContent = `剩余 ${mins}:${String(secs).padStart(2, "0")}`;
      node.classList.toggle("warn", paper.left <= 60);
    }, 1000);
  }

  function collect() {
    return [...el.querySelectorAll("[data-answer]")].map((box) => ({ id: box.dataset.answer, text: box.value }));
  }

  async function printAs(kind) {
    const host = document.createElement("div");
    host.className = "print-sheet";
    if (kind === "cram") {
      host.innerHTML = `<h1>${escapeHTML(data.cram.goal?.title || "")} · 考前速背清单</h1>
        ${data.cram.days_left !== null && data.cram.days_left !== undefined ? `<p>距截止日期 ${data.cram.days_left} 天</p>` : ""}
        ${data.cram.items.map((i) => `<section><h2>${escapeHTML(i.title)}</h2>
          <p>${escapeHTML(i.essence)}</p>
          <ul>${(i.key_points || []).map((k) => `<li>${escapeHTML(String(k))}</li>`).join("")}</ul>
          ${i.mnemonic ? `<p><strong>口诀：</strong>${escapeHTML(i.mnemonic)}</p>` : ""}
          <ul>${(i.probes || []).map((p) => `<li>${escapeHTML(p.prompt)}<br><em>${escapeHTML(p.answer)}</em></li>`).join("")}</ul>
          <p class="warn-line">易错：${(i.misconceptions || []).map((m) => escapeHTML(String(m))).join("；") || "—"}</p>
        </section>`).join("")}`;
    } else if (kind === "mistakes") {
      host.innerHTML = `<h1>错题本（近 ${data.book.days} 天）</h1>
        ${data.book.items.map((i) => `<section><h2>${escapeHTML(i.front)}</h2>
          <p>${escapeHTML(i.unit_title)} · 错 ${i.wrongs} 次 · ${(i.tag_counts || []).map((t) => t.label).join("、")}</p>
          <p>${escapeHTML(i.back)}</p></section>`).join("")}
        ${data.book.exam_items.map((i) => `<section><h2>${escapeHTML(i.prompt)}</h2>
          <p>${escapeHTML(i.unit_title)}</p><p>你写的：${escapeHTML(i.yours || "（空）")}</p>
          <p>参考答案：${escapeHTML(i.answer)}</p></section>`).join("")}`;
    } else {
      const inner = el.querySelector("#exam-paper-inner") || el.querySelector("#exam-result");
      if (!inner) { toast("现在没有可打印的卷子", "info"); return; }
      host.innerHTML = `<h1>${escapeHTML(paper ? paper.title : "自测卷")}</h1>${inner.innerHTML}`;
    }
    // <details> 收起时里面的答案不会被打印出来：纸质卷子上不该出现"翻面才有答案"
    host.querySelectorAll("details").forEach((d) => { d.open = true; });
    document.body.appendChild(host);
    try {
      window.print();
    } finally {
      host.remove();
    }
  }

  function bind() {
    el.querySelectorAll("[data-shot]").forEach((btn) => btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        await ctx.api.post("/api/cram/shot", { unit_id: btn.dataset.shot });
        data = await loadAll();
        toast("已斩掉，不会再出现在这份清单上", "success");
        paint();
      } catch (err) {
        toast(err.message || "斩失败", "error");
        btn.disabled = false;
      }
    }));
    el.querySelector("[data-cram-reset]")?.addEventListener("click", async () => {
      const ok = await confirmDialog({
        title: "重列速背清单？",
        message: "把所有已斩的单元放回清单。不影响复习排程，也不删任何数据。",
        confirmLabel: "重列",
      });
      if (!ok) return;
      await ctx.api.post("/api/cram/reset", {});
      data = await loadAll();
      paint();
    });
    el.querySelector("[data-practice]")?.addEventListener("click", () => {
      const ids = [...el.querySelectorAll("[data-pick-mistake]")]
        .filter((box) => box.checked).map((box) => box.dataset.pickMistake);
      if (!ids.length) { toast("一张都没勾：勾上要重练的错题", "info"); return; }
      ctx.navigate(`#/review?ids=${encodeURIComponent(ids.join(","))}`);
    });
    el.querySelectorAll("[data-print]").forEach((btn) => btn.addEventListener("click", () => printAs(btn.dataset.print)));
    el.querySelector("[data-exam-build]")?.addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      try {
        const minutes = parseInt(el.querySelector("#ex-minutes")?.value || "25", 10);
        const count = parseInt(el.querySelector("#ex-count")?.value || "8", 10);
        const built = await ctx.api.post("/api/exam", { minutes, count });
        if (!built.questions?.length) { toast("没能组出题来", "info"); return; }
        paper = { ...built, minutes, left: minutes * 60 };
        renderPaper();
        startTicker();
        el.querySelector("#exam-paper")?.scrollIntoView({ block: "start", behavior: "instant" });
      } catch (err) {
        toast(err.message || "组卷失败", "error");
      } finally {
        btn.disabled = false;
      }
    });
  }

  function bindPaper() {
    // 卷面是点「组一卷」之后才注入 DOM 的：绑定必须跟着注入走，否则这些按钮一个都不响应。
    el.querySelector("[data-exam-reveal]")?.addEventListener("click", (e) => {
      const on = e.currentTarget.dataset.on === "1";
      e.currentTarget.dataset.on = on ? "0" : "1";
      el.querySelectorAll(".exam-key").forEach((k) => { k.hidden = on; });
      toast(on ? "答案已收起" : "答案速览只给你对照，判分还是要交卷", "info");
    });
    el.querySelector("[data-exam-abandon]")?.addEventListener("click", async () => {
      const ok = await confirmDialog({
        title: "放弃这份卷子？", message: "未交卷的卷子会留在记录里标成「未交」，但不会被判分。",
        confirmLabel: "放弃", danger: true,
      });
      if (!ok) return;
      paper = null;
      if (ticker) { clearInterval(ticker); ticker = null; }
      renderPaperClear();
    });
    el.querySelector("[data-exam-submit]")?.addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      if (!paper) return;
      const blanks = collect().filter((a) => !a.text.trim()).length;
      if (blanks && !await confirmDialog({
        title: `还有 ${blanks} 题空着`, message: "空题按 0 分计入平均分。确定现在就交卷吗？",
        confirmLabel: "交卷",
      })) return;
      btn.disabled = true;
      try {
        const graded = await ctx.api.post("/api/exam/submit",
                                          { exam_id: paper.exam_id, answers: collect() });
        if (ticker) { clearInterval(ticker); ticker = null; }
        paper = null;
        const result = graded.result || {};
        data = await loadAll();
        el.innerHTML = shell(data);
        const host = el.querySelector("#exam-paper");
        if (host) host.innerHTML = resultHTML(graded);
        bind();
        toast(`判完：答对 ${result.counts?.correct || 0} 题，平均分 ${
          result.score === undefined || result.score === null ? "—" : `${(result.score * 100).toFixed(0)}%`}`,
          "success");
      } catch (err) {
        toast(err.message || "交卷失败", "error");
        btn.disabled = false;
      }
    });
  }

  function renderPaperClear() {
    const host = el.querySelector("#exam-paper");
    if (host && !host.querySelector("#exam-result")) host.innerHTML = "";
  }

  paint();
  // 离开页面必须停表：不然倒计时还在跑，只是再也找不到那个钟的节点
  ctx.cleanup(() => { if (ticker) clearInterval(ticker); ticker = null; });
}
