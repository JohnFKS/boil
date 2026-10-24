/* 灵感库：任何页面划词保存下来的引用与想法，都在这里被重新看见。 */
import {
  escapeHTML, escapeAttr, icon, toast, relativeTime, confirmDialog,
} from "../utils.js";

const SOURCE_LABELS = {
  unit: "知识单元", socratic: "苏格拉底对话", feynman: "费曼挑战",
  review: "复习", insights: "洞察", dashboard: "今日",
  map: "知识地图", library: "内容库", notes: "灵感库",
};

let activeCleanup = null;

function noteCardHTML(n) {
  const source = SOURCE_LABELS[n.source] || "";
  const meta = [source, n.unit_title, relativeTime(n.created_at)].filter(Boolean);
  return `
    <article class="note-card" data-note-id="${escapeAttr(n.id)}">
      ${n.quote ? `<blockquote class="note-quote">${escapeHTML(n.quote)}</blockquote>` : ""}
      ${n.content ? `<div class="note-body">${escapeHTML(n.content)}</div>` : ""}
      <div class="note-foot">
        <span class="note-meta">${meta.map(escapeHTML).join(" · ")}</span>
        <span class="row" style="gap:6px">
          ${n.unit_id ? `<a class="badge badge-muted" href="#/unit/${escapeAttr(n.unit_id)}">回到单元</a>` : ""}
          <button class="icon-btn" type="button" data-note-delete="${escapeAttr(n.id)}" title="删除这条灵感">${icon("trash", 13)}</button>
        </span>
      </div>
    </article>`;
}

export async function render(el, ctx, params) {
  if (activeCleanup) { activeCleanup(); activeCleanup = null; }
  let data = { notes: [], total: 0 };
  let keyword = params.query?.get("q") || "";
  let unitFilter = params.query?.get("unit_id") || "";
  const unitOptions = ctx.state.dashboard?.map_preview?.units || [];

  async function load() {
    const query = new URLSearchParams();
    if (unitFilter) query.set("unit_id", unitFilter);
    if (keyword) query.set("q", keyword);
    const suffix = query.toString() ? `?${query}` : "";
    data = await ctx.api.get(`/api/notes${suffix}`);
  }

  function listHTML() {
    const notes = data.notes || [];
    if (!notes.length) {
      return `<div class="empty" style="padding:44px 20px">
        <div class="empty-icon">${icon("spark", 22)}</div>
        <h3>还没有灵感</h3>
        <p>在任何学习页面划选一段文字——或在选中的文字上点右键——选择「存为灵感」，它就会出现在这里。</p>
      </div>`;
    }
    return notes.map(noteCardHTML).join("");
  }

  function paint() {
    const body = el.querySelector("#notes-body");
    if (body) body.innerHTML = listHTML();
    const count = el.querySelector("#notes-count");
    if (count) count.textContent = `${data.notes?.length || 0} / ${data.total || 0} 条`;
  }

  await load();

  el.innerHTML = `
    <div class="page-head">
      <div>
        <h2>灵感库</h2>
        <p>划词保存下来的句子与想法。它们不会被排程，但会在单元页被重新点亮。</p>
      </div>
      <div class="row wrap" style="gap:8px">
        <span class="badge badge-teal" id="notes-count"></span>
        <button class="btn btn-ghost btn-sm" type="button" data-notes-export>${icon("download", 13)} 导出 Markdown</button>
      </div>
    </div>

    <div class="notes-toolbar">
      <input class="input" id="notes-search" type="search" aria-label="搜索灵感笔记" placeholder="搜索引用或想法…" value="${escapeHTML(keyword)}">
      <select class="select" id="notes-unit" aria-label="按单元筛选笔记">
        <option value="">全部单元</option>
        ${unitOptions.map(u => `<option value="${escapeAttr(u.id)}" ${u.id === unitFilter ? "selected" : ""}>${escapeHTML(u.title)}</option>`).join("")}
      </select>
    </div>

    <div class="notes-list mt-3" id="notes-body">${listHTML()}</div>`;

  paint();

  const search = el.querySelector("#notes-search");
  let timer = null;
  search?.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      keyword = search.value.trim();
      await load();
      paint();
    }, 300);
  });
  el.querySelector("#notes-unit")?.addEventListener("change", async (e) => {
    unitFilter = e.target.value;
    await load();
    paint();
  });
  el.querySelector("[data-notes-export]")?.addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      await ctx.api.download("/api/notes/export", "boil-notes.md");
    } catch (err) {
      toast(err.message || "导出失败", "error");
    } finally {
      btn.disabled = false;
    }
  });
  el.querySelector("#notes-body")?.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-note-delete]");
    if (!btn) return;
    const id = btn.dataset.noteDelete;
    const ok = await confirmDialog({
      title: "删除这条灵感？",
      message: "删除后无法恢复，导出的文件里也不会再有它。",
      confirmLabel: "删除",
      danger: true,
    });
    if (!ok) return;
    try {
      await ctx.api.post("/api/notes/delete", { id });
      await load();
      paint();
      toast("已删除。", "success", 1800);
    } catch (err) {
      toast(err.message || "删除失败", "error");
    }
  });

  const onSaved = () => { load().then(paint); };
  document.addEventListener("boil:note-saved", onSaved);
  activeCleanup = () => {
    clearTimeout(timer);
    document.removeEventListener("boil:note-saved", onSaved);
    activeCleanup = null;
  };
}
