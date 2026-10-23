/* 划词菜单：在可复制的内容上选中文字或右键，把灵感存进笔记。 */
import { escapeHTML, icon, toast, aiEnabled, aiFailure, aiSuccess } from "./utils.js";

const MIN_CHARS = 4;
const MAX_CHARS = 2000;
const TOP_BAR = 64;

let mounted = null;

function activeContainer() {
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
  const text = sel.toString();
  if (!text || !text.trim()) return null;
  if (text.trim().length < MIN_CHARS || text.length > MAX_CHARS) return null;
  let range;
  try {
    range = sel.getRangeAt(0);
  } catch {
    return null;
  }
  const node = range.commonAncestorContainer;
  const element = node.nodeType === 1 ? node : node.parentElement;
  if (!element) return null;
  const editable = element.closest("input, textarea, select, [contenteditable='true']");
  if (editable) return null;
  const container = element.closest("[data-selectable]");
  if (!container) return null;
  const rect = range.getBoundingClientRect();
  if (!rect || (rect.width === 0 && rect.height === 0)) return null;
  const unitHost = element.closest("[data-unit-id]");
  return {
    text,
    rect,
    container,
    unitId: (unitHost && unitHost.dataset.unitId) || container.dataset.unitId || "",
    source: container.dataset.source || "",
  };
}

function menuHTML(aiReady) {
  return `
    <button class="sel-btn" type="button" data-sel="note">${icon("wand", 13)} 存为灵感</button>
    ${aiReady ? `<span class="sel-sep"></span><button class="sel-btn" type="button" data-sel="cards">${icon("review", 13)} 做卡片</button>
    <span class="sel-sep"></span><button class="sel-btn" type="button" data-sel="ask">${icon("chat", 13)} 问 AI</button>` : ""}
    <span class="sel-sep"></span>
    <button class="sel-btn" type="button" data-sel="copy">${icon("list", 13)} 复制</button>`;
}

function placeFloating(el, x, y, flipThreshold) {
  el.style.visibility = "hidden";
  el.style.display = "flex";
  const w = el.offsetWidth;
  const h = el.offsetHeight;
  const left = Math.max(8, Math.min(x - w / 2, window.innerWidth - w - 8));
  const below = y < flipThreshold;
  const top = below ? y + 10 : y - h - 10;
  el.style.left = `${left}px`;
  el.style.top = `${Math.max(8, Math.min(top, window.innerHeight - h - 8))}px`;
  el.style.visibility = "visible";
}

function pointInRange(range, x, y) {
  const rects = range.getClientRects();
  for (let i = 0; i < rects.length; i += 1) {
    const r = rects[i];
    if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) return true;
  }
  const box = range.getBoundingClientRect();
  return box ? (x >= box.left && x <= box.right && y >= box.top && y <= box.bottom) : false;
}

export function initSelectionMenu(ctx) {
  if (mounted) return mounted.rebind(ctx);
  const menu = document.createElement("div");
  menu.className = "selection-menu";
  menu.setAttribute("role", "menu");
  menu.style.display = "none";
  menu.innerHTML = menuHTML(aiEnabled(ctx));
  document.body.appendChild(menu);

  const popover = document.createElement("div");
  popover.className = "note-popover";
  popover.style.display = "none";
  document.body.appendChild(popover);

  let current = null;
  let ignoreUntil = 0;
  let lastScrollY = window.scrollY;

  const hideMenu = () => {
    menu.style.display = "none";
  };
  const hidePopover = () => {
    popover.style.display = "none";
    popover.innerHTML = "";
  };
  const hideAll = () => {
    hideMenu();
    hidePopover();
    document.querySelectorAll(".sel-panel").forEach(p => hidePanel(p));
  };

  function showMenu(x, y) {
    menu.innerHTML = menuHTML(aiEnabled(ctx));
    placeFloating(menu, x, y, TOP_BAR + 40);
  }

  function anchorContext(state) {
    // 只给模型选中位置前后各约 800 字，不整篇塞进去
    const full = (state.container?.innerText || state.text).replace(/[ \t]+\n/g, "\n");
    const at = full.indexOf(state.text);
    if (at < 0) return full.slice(0, 1600);
    return full.slice(Math.max(0, at - 800), Math.min(full.length, at + state.text.length + 800));
  }

  function placePanel(panel) {
    if (!current) return;
    panel.style.display = "grid";
    const w = panel.offsetWidth;
    const h = panel.offsetHeight;
    const left = Math.max(8, Math.min(current.rect.left + current.rect.width / 2 - w / 2, window.innerWidth - w - 8));
    const below = current.rect.bottom + h + 16 <= window.innerHeight;
    const top = below ? current.rect.bottom + 10 : Math.max(8, current.rect.top - h - 10);
    panel.style.left = `${left}px`;
    panel.style.top = `${Math.min(top, window.innerHeight - h - 8)}px`;
  }

  function ensurePanel(kind, label) {
    const id = `sel-panel-${kind}`;
    let panel = document.getElementById(id);
    if (panel) return panel;
    panel = document.createElement("div");
    panel.id = id;
    panel.className = "sel-panel";
    panel.dataset.panel = kind;
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", label);
    panel.style.display = "none";
    document.body.appendChild(panel);
    panel.addEventListener("mousedown", (e) => {
      if (e.target.closest("textarea")) return;
      e.preventDefault();
    });
    panel.addEventListener("click", (e) => {
      if (e.target.closest("[data-panel-cancel]")) hidePanel(panel);
    });
    return panel;
  }

  function hidePanel(panel) {
    if (panel) { panel.style.display = "none"; panel.innerHTML = ""; }
  }

  function openCardsPanel() {
    if (!current) return;
    const panel = ensurePanel("cards", "做成复习卡");
    panel.innerHTML = `<div class="panel-head">${icon("review", 13)} 做成复习卡</div>
      <div class="panel-body"><p class="muted small">正在从选中的 ${current.text.length} 个字里提炼…</p></div>`;
    placePanel(panel);
    ctx.api.post("/api/cards/preview", {
      text: current.text, unit_id: current.unitId || undefined,
    }).then((res) => {
      const badge = res.engine === "model"
        ? '<span class="badge badge-teal">AI 提炼</span>'
        : '<span class="badge badge-muted">本地规则</span>';
      panel.innerHTML = `
        <div class="panel-head"><span>做成复习卡</span>${badge}</div>
        <div class="panel-body">
          <p class="faint tiny">${escapeHTML(res.engine_note || "")}</p>
          <div class="card-preview-list">
            ${res.cards.map((c, i) => `<div class="card-preview">
              <div class="cp-front"><span class="cp-tag">${i + 1}</span>${escapeHTML(c.front)}</div>
              <div class="cp-back">${escapeHTML(c.back)}</div>
            </div>`).join("")}
          </div>
          <p class="faint tiny">${res.unit_title
            ? `会加入「${escapeHTML(res.unit_title)}」的复习队列。`
            : "没有指定单元，确认时会归到当前目标的第一个单元。"}</p>
        </div>
        <div class="panel-foot">
          <button class="btn btn-ghost btn-sm" type="button" data-panel-cancel>取消</button>
          <button class="btn btn-primary btn-sm" type="button" data-cards-confirm>确认存入</button>
        </div>`;
      panel.querySelector("[data-cards-confirm]")?.addEventListener("click", async (e) => {
        const btn = e.currentTarget;
        btn.disabled = true; btn.textContent = "存入中…";
        try {
          const done = await ctx.api.post("/api/cards/confirm", {
            cards: res.cards, unit_id: res.unit_id || current.unitId || undefined,
          });
          hidePanel(panel);
          hideMenu();
          const sel = window.getSelection();
          sel?.removeAllRanges();
          toast(done.summary || "已加入复习队列。", "success", 4200);
        } catch (err) {
          btn.disabled = false; btn.textContent = "确认存入";
          toast(err.message || "存入失败", "error");
        }
      });
    }).catch((e) => {
      hidePanel(panel);
      toast(e.message || "提炼失败", "error", 4600);
    });
  }

  function openAskPanel() {
    if (!current) return;
    const panel = ensurePanel("ask", "对这段追问");
    panel.innerHTML = `<div class="panel-head">${icon("chat", 13)} 对这段追问</div>
      <div class="panel-body">
        <div class="note-quote ask-anchor">${escapeHTML(current.text.trim().slice(0, 160))}</div>
        <textarea class="textarea" id="ask-input" rows="2" placeholder="问一句，例如：这跟前面那条有什么区别？"></textarea>
        <div class="ask-answer" id="ask-answer" style="display:none"></div>
      </div>
      <div class="panel-foot">
        <button class="btn btn-ghost btn-sm" type="button" data-panel-cancel>关闭</button>
        <button class="btn btn-primary btn-sm" type="button" data-ask-go>提问</button>
      </div>`;
    placePanel(panel);
    const input = panel.querySelector("#ask-input");
    input?.focus();
    const go = async () => {
      const question = (input?.value || "").trim();
      if (!question) return toast("先写一个问题", "info");
      const btn = panel.querySelector("[data-ask-go]");
      const box = panel.querySelector("#ask-answer");
      if (btn) { btn.disabled = true; btn.textContent = "思考中…"; }
      if (box) { box.style.display = "block"; box.innerHTML = '<p class="muted small">正在结合选中位置的上下文回答…</p>'; }
      try {
        const res = await ctx.api.post("/api/ask", {
          question,
          context: anchorContext(current),
          unit_id: current.unitId || undefined,
        }, { timeout: 90000 });
        aiSuccess(ctx);
        if (box) {
          box.innerHTML = `<div class="between"><span class="badge badge-teal">AI 回答</span>`
            + `<span class="faint tiny">只看得到选中位置前后各约 800 字</span></div>`
            + `<div class="ask-text">${escapeHTML(res.answer)}</div>`;
        }
      } catch (e) {
        aiFailure(ctx);
        if (box) box.innerHTML = `<p class="muted small">${escapeHTML(e.message || "回答失败")}</p>`;
      } finally {
        if (btn) { btn.disabled = false; btn.textContent = "再问一次"; }
      }
    };
    panel.querySelector("[data-ask-go]")?.addEventListener("click", go);
    input?.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); go(); }
    });
  }

  function openPopover() {
    if (!current) return;
    const quote = current.text.trim().slice(0, 600);
    popover.innerHTML = `
      <div class="note-popover-head">存为灵感</div>
      <div class="note-quote">${escapeHTML(quote)}${current.text.trim().length > 600 ? "…" : ""}</div>
      <textarea class="textarea" id="sel-note" placeholder="这一刻的想法、用法、疑问…（可以只保存引用）"></textarea>
      <div class="note-popover-foot">
        ${current.unitId ? `<span class="badge badge-muted" title="会关联到当前单元">关联单元</span>` : "<span></span>"}
        <span class="row" style="gap:6px">
          <button class="btn btn-ghost btn-sm" type="button" data-note-cancel>取消</button>
          <button class="btn btn-primary btn-sm" type="button" data-note-save>保存</button>
        </span>
      </div>`;
    popover.style.display = "grid";
    const w = popover.offsetWidth;
    const h = popover.offsetHeight;
    const left = Math.max(8, Math.min(current.rect.left + current.rect.width / 2 - w / 2, window.innerWidth - w - 8));
    const top = current.rect.bottom + h + 16 > window.innerHeight
      ? Math.max(8, current.rect.top - h - 10)
      : current.rect.bottom + 10;
    popover.style.left = `${left}px`;
    popover.style.top = `${Math.max(8, Math.min(top, window.innerHeight - h - 8))}px`;
    popover.querySelector("#sel-note")?.focus();
  }

  async function saveNote() {
    if (!current) return;
    const area = popover.querySelector("#sel-note");
    const content = (area?.value || "").trim();
    const saveBtn = popover.querySelector("[data-note-save]");
    if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = "保存中…"; }
    try {
      await ctx.api.post("/api/notes", {
        quote: current.text.trim(),
        content,
        unit_id: current.unitId || undefined,
        source: current.source || undefined,
      });
      hideAll();
      const sel = window.getSelection();
      sel?.removeAllRanges();
      toast("已存入灵感库。", "success", 2600);
      document.dispatchEvent(new CustomEvent("boil:note-saved"));
    } catch (e) {
      if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = "保存"; }
      toast(e.message || "保存失败", "error", 4000);
    }
  }

  menu.addEventListener("mousedown", (e) => e.preventDefault());
  menu.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-sel]");
    if (!btn || !current) return;
    if (btn.dataset.sel === "copy") {
      try {
        await navigator.clipboard.writeText(current.text);
        toast("已复制。", "success", 1800);
      } catch {
        toast("浏览器拒绝了复制权限", "error");
      }
      hideAll();
      return;
    }
    hideMenu();
    if (btn.dataset.sel === "cards") return openCardsPanel();
    if (btn.dataset.sel === "ask") return openAskPanel();
    openPopover();
  });

  popover.addEventListener("click", (e) => {
    if (e.target.closest("[data-note-cancel]")) return hideAll();
    if (e.target.closest("[data-note-save]")) return saveNote();
  });
  popover.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); saveNote(); }
  });
  popover.addEventListener("mousedown", (e) => {
    if (e.target.closest("textarea")) return;
    e.preventDefault();
  });

  const onSelectionChange = () => {
    if (Date.now() < ignoreUntil) return;
    if (popover.style.display !== "none") return;
    if ([...document.querySelectorAll(".sel-panel")].some(p => p.style.display !== "none")) return;
    const found = activeContainer();
    if (!found) return hideMenu();
    current = found;
    showMenu(found.rect.left + found.rect.width / 2, found.rect.top);
  };

  const onContextMenu = (e) => {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) return;
    let range;
    try {
      range = sel.getRangeAt(0);
    } catch {
      return;
    }
    if (!pointInRange(range, e.clientX, e.clientY)) return;
    const found = activeContainer();
    if (!found) return;
    e.preventDefault();
    current = found;
    ignoreUntil = Date.now() + 320;
    showMenu(e.clientX, e.clientY + 20);
  };

  const onMouseDown = (e) => {
    if (menu.contains(e.target) || popover.contains(e.target)) return;
    // 面板里的按下不是「点外部」：否则 hideAll 先拆掉 DOM，按钮永远收不到 click
    // （真实鼠标必现；合成 element.click() 不派发 mousedown，测不出来）。
    if (e.target.closest && e.target.closest(".sel-panel")) return;
    hideAll();
  };
  const onScroll = () => {
    if (Math.abs(window.scrollY - lastScrollY) > 10) {
      lastScrollY = window.scrollY;
      hideAll();
    }
  };
  const onKeyDown = (e) => {
    if (e.key === "Escape") hideAll();
  };

  document.addEventListener("selectionchange", onSelectionChange);
  document.addEventListener("contextmenu", onContextMenu);
  document.addEventListener("mousedown", onMouseDown);
  window.addEventListener("scroll", onScroll, { passive: true });
  document.addEventListener("keydown", onKeyDown);

  const api = {
    hide: hideAll,
    rebind(nextCtx) { ctx = nextCtx; return api; },
    destroy() {
      document.removeEventListener("selectionchange", onSelectionChange);
      document.removeEventListener("contextmenu", onContextMenu);
      document.removeEventListener("mousedown", onMouseDown);
      document.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("scroll", onScroll);
      menu.remove();
      popover.remove();
      document.querySelectorAll(".sel-panel").forEach(p => p.remove());
      mounted = null;
    },
  };
  mounted = api;
  return api;
}

export function selectionMenu() {
  return mounted;
}
