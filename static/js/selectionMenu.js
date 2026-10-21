/* 划词菜单：在可复制的内容上选中文字或右键，把灵感存进笔记。 */
import { escapeHTML, icon, toast } from "./utils.js";

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

function menuHTML() {
  return `
    <button class="sel-btn" type="button" data-sel="note">${icon("wand", 13)} 存为灵感</button>
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
  menu.innerHTML = menuHTML();
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
  };

  function showMenu(x, y) {
    placeFloating(menu, x, y, TOP_BAR + 40);
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
      mounted = null;
    },
  };
  mounted = api;
  return api;
}

export function selectionMenu() {
  return mounted;
}
