/* Knowledge map: layered SVG graph with pan/zoom, filters and a side inspector. */
import {
  escapeHTML, escapeAttr, icon, pct, statusMeta, truncate, toast,
} from "../utils.js";

function layout(nodes, edges) {
  const byId = new Map(nodes.map(n => [n.id, n]));
  const indeg = new Map(nodes.map(n => [n.id, 0]));
  const out = new Map(nodes.map(n => [n.id, []]));
  for (const e of edges) {
    if (e.kind !== "prerequisite") continue;
    if (byId.has(e.source) && byId.has(e.target) && e.source !== e.target) {
      out.get(e.source).push(e.target);
      indeg.set(e.target, (indeg.get(e.target) || 0) + 1);
    }
  }
  const depth = new Map();
  const queue = nodes.filter(n => !indeg.get(n.id)).sort((a, b) => (a.order_index || 0) - (b.order_index || 0));
  queue.forEach(n => depth.set(n.id, 0));
  const seen = new Set();
  while (queue.length) {
    const n = queue.shift();
    if (seen.has(n.id)) continue;
    seen.add(n.id);
    for (const target of out.get(n.id) || []) {
      depth.set(target, Math.max(depth.get(target) || 0, (depth.get(n.id) || 0) + 1));
      indeg.set(target, (indeg.get(target) || 0) - 1);
      if (indeg.get(target) <= 0) {
        const t = byId.get(target);
        if (t) queue.push(t);
      }
    }
  }
  nodes.forEach((n, i) => {
    if (!depth.has(n.id)) depth.set(n.id, Math.min(8, Math.floor(i / 3)));
  });
  // 蛇形 4 列：线性课程不会再被拉成 8 列细线
  const maxDepth = Math.max(...[...depth.values()], 0);
  const COLS = Math.max(1, Math.min(4, maxDepth + 1));
  const NW = 184, NH = 82, HG = 64, VG = 18, PAD = 36, BAND_GAP = 54;
  const byDepth = new Map();
  for (const n of nodes) {
    const d = depth.get(n.id) || 0;
    if (!byDepth.has(d)) byDepth.set(d, []);
    byDepth.get(d).push(n);
  }
  for (const list of byDepth.values()) list.sort((a, b) => (a.order_index || 0) - (b.order_index || 0));
  const bands = Math.ceil((maxDepth + 1) / COLS);
  const bandRows = Array.from({ length: bands }, () => 1);
  for (const [d, list] of byDepth) {
    const band = Math.floor(d / COLS);
    bandRows[band] = Math.max(bandRows[band], list.length);
  }
  const bandHeights = bandRows.map(rows => PAD * 2 + rows * NH + Math.max(0, rows - 1) * VG);
  const bandTops = [];
  let cursor = 0;
  for (let b = 0; b < bands; b += 1) {
    bandTops.push(cursor);
    cursor += bandHeights[b] + (b < bands - 1 ? BAND_GAP : 0);
  }
  const height = Math.max(360, cursor);
  const width = Math.max(700, PAD * 2 + COLS * NW + (COLS - 1) * HG);
  const pos = new Map();
  for (const [d, list] of byDepth) {
    const band = Math.floor(d / COLS);
    const slot = d % COLS;
    const col = band % 2 === 0 ? slot : COLS - 1 - slot;
    const totalH = list.length * NH + Math.max(0, list.length - 1) * VG;
    const offsetY = bandTops[band] + PAD + (bandHeights[band] - PAD * 2 - totalH) / 2;
    list.forEach((n, i) => {
      pos.set(n.id, {
        x: PAD + col * (NW + HG),
        y: offsetY + i * (NH + VG),
        w: NW, h: NH,
      });
    });
  }
  return { pos, width, height, depth };
}
function edgePath(a, b) {
  if (!a || !b) return "";
  if (b.x >= a.x + a.w) {
    const x1 = a.x + a.w, y1 = a.y + a.h / 2;
    const x2 = b.x, y2 = b.y + b.h / 2;
    const dx = Math.max(28, Math.abs(x2 - x1) * 0.45);
    return `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}`;
  }
  // 蛇形换行：从源节点底部连到目标顶部
  const x1 = a.x + a.w / 2, y1 = a.y + a.h;
  const x2 = b.x + b.w / 2, y2 = b.y;
  const dy = Math.max(25, Math.abs(y2 - y1) * 0.45);
  return `M${x1},${y1} C${x1},${y1 + dy} ${x2},${y2 - dy} ${x2},${y2}`;
}

function nodeHTML(n, p) {
  const st = statusMeta[n.status] || statusMeta.new;
  const mastery = Math.round((n.mastery || 0) * 100);
  return `<g class="map-node-wrap" data-id="${escapeAttr(n.id)}" data-status="${escapeAttr(n.status)}">
    <foreignObject x="${p.x}" y="${p.y}" width="${p.w}" height="${p.h}">
      <div xmlns="http://www.w3.org/1999/xhtml" class="map-node-card ${st.className}">
        <div class="node-top">
          <span class="node-title">${escapeHTML(n.title)}</span>
          <span class="node-status" style="background:${st.color};box-shadow:0 0 0 3px ${st.color}22"></span>
        </div>
        <div class="node-bottom">
          <span class="node-bar"><i style="width:${mastery}%;background:${st.color}"></i></span>
          <span style="font-size:9px;color:var(--faint)">${n.due_count ? `${n.due_count} 待复习` : st.label}</span>
        </div>
      </div>
    </foreignObject>
  </g>`;
}

function defaultSide(data) {
  const goal = data.goal || {};
  const s = data.summary || {};
  return `<div class="card map-side-card">
    <div class="side-label">当前目标</div>
    <div class="map-side-title">${escapeHTML(goal.title || "")}</div>
    <div class="map-side-essence">${escapeHTML(goal.target || "点击左侧任意节点，查看单元本质、掌握度与下一步。")}</div>
    <div class="divider"></div>
    <div class="facet-row"><span class="muted">单元</span><span>${s.units || 0} 个</span><span></span></div>
    <div class="facet-row"><span class="muted">已烧开</span><span style="color:var(--teal)">${s.boiled || 0} 个</span><span></span></div>
    <div class="facet-row"><span class="muted">学习中</span><span style="color:var(--gold)">${s.learning || 0} 个</span><span></span></div>
    <div class="facet-row"><span class="muted">平均掌握</span><span>${pct(s.average || 0)}</span><span></span></div>
    <div class="divider"></div>
    <div class="map-tip">滚轮缩放 · 拖拽平移 · 点击节点查看</div>
  </div>
  <div class="card map-side-card">
    <div class="side-label">怎么用这张图</div>
    <div class="rule-list">
      <div class="rule"><span class="n">1</span><span>先看颜色：灰=未开始，金=学习中，绿=已烧开，橙=需复习。</span></div>
      <div class="rule"><span class="n">2</span><span>沿着线走：前置关系是从左到右，不要跳着啃。</span></div>
      <div class="rule"><span class="n">3</span><span>点开一个节点，直接开始预习、苏格拉底或费曼。</span></div>
    </div>
  </div>`;
}

function nodeSide(n) {
  const st = statusMeta[n.status] || statusMeta.new;
  const facets = n.facets || {};
  const labels = { recall: "回忆", explain: "解释", apply: "应用", transfer: "迁移" };
  return `<div class="card map-side-card">
    <div class="between"><span class="badge ${n.status === "boiled" ? "badge-teal" : n.status === "rusting" ? "badge-ember" : n.status === "learning" ? "badge-gold" : "badge-muted"}">${st.label}</span><span class="faint tiny">T ${Math.round(n.heat || 0)}° · ${n.due_count ? `${n.due_count} 张待复习` : `${n.estimated_minutes || 45} 分钟`}</span></div>
    <div class="map-side-title mt-3">${escapeHTML(n.title)}</div>
    <div class="map-side-essence">${escapeHTML(n.essence || "")}</div>
    ${n.tags?.length ? `<div class="chip-row mt-3">${n.tags.slice(0, 4).map(t => `<span class="tag">${escapeHTML(t)}</span>`).join("")}</div>` : ""}
    <div class="divider"></div>
    ${Object.keys(labels).map(k => `<div class="facet-row"><span class="muted">${labels[k]}</span><span class="facet-bar"><i style="width:${Math.round((facets[k]?.score || 0) * 100)}%"></i></span><span class="faint" style="text-align:right">${pct(facets[k]?.score || 0)}</span></div>`).join("")}
    <div class="map-side-actions">
      <a class="btn btn-primary btn-block" href="#/unit/${escapeAttr(n.id)}">${icon("book", 15)} 打开单元</a>
      <button class="btn btn-teal btn-block" data-start-soc="${escapeAttr(n.id)}">${icon("chat", 15)} 苏格拉底对话</button>
      <a class="btn btn-ghost btn-block" href="#/feynman/${escapeAttr(n.id)}">${icon("teach", 15)} 费曼挑战</a>
    </div>
  </div>`;
}

export async function render(el, ctx, params) {
  let data;
  try {
    data = await ctx.api.get("/api/map");
  } catch (e) {
    if (e.status === 404) { ctx.navigate("#/onboarding", { replace: true }); return; }
    throw e;
  }
  const nodes = data.nodes || [];
  const edges = data.edges || [];
  if (!nodes.length) {
    el.innerHTML = `<div class="empty"><div class="empty-icon">${icon("map", 22)}</div><h3>地图还是空的</h3><p>这个目标还没有知识单元。</p><a class="btn btn-primary mt-3" href="#/library">去内容库看看</a></div>`;
    return;
  }
  const lay = layout(nodes, edges);
  const edgeHTML = edges.map((e, i) => {
    const a = lay.pos.get(e.source), b = lay.pos.get(e.target);
    if (!a || !b) return "";
    return `<path class="map-edge" data-index="${i}" data-source="${escapeAttr(e.source)}" data-target="${escapeAttr(e.target)}" d="${edgePath(a, b)}" fill="none" stroke="var(--line-strong)" stroke-width="1.4" marker-end="url(#arrow)" opacity=".7"/>`;
  }).join("");
  const nodeHTMLs = nodes.map(n => nodeHTML(n, lay.pos.get(n.id))).join("");
  const initialW = Math.max(700, lay.width);
  const initialH = Math.max(360, lay.height);

  el.innerHTML = `
    <div class="page-head">
      <div>
        <h2>学习路径</h2>
        <p>${escapeHTML(data.goal?.title || "")} · ${nodes.length} 个单元。颜色是状态，线是前置关系；不要跳着啃。</p>
      </div>
      <div class="row wrap">
        <span class="badge badge-teal">${data.summary?.boiled || 0} 已烧开</span>
        <span class="badge badge-gold">${data.summary?.learning || 0} 学习中</span>
        <span class="badge badge-muted">${data.summary?.new || 0} 未开始</span>
      </div>
    </div>
    <div class="map-layout">
      <div class="card map-panel">
        <div class="map-toolbar">
          <div class="map-filters">
            <button class="filter-chip active" data-filter="all">全部</button>
            <button class="filter-chip" data-filter="new">未开始</button>
            <button class="filter-chip" data-filter="learning">学习中</button>
            <button class="filter-chip" data-filter="boiled">已烧开</button>
            <button class="filter-chip" data-filter="rusting">需复习</button>
          </div>
          <div class="map-legend">
            ${Object.entries(statusMeta).map(([k, v]) => `<span><i class="map-dot ${k}"></i>${v.label}</span>`).join("")}
          </div>
        </div>
        <div class="map-svg-wrap" id="map-wrap">
          <svg id="map-svg" class="map-svg" viewBox="0 0 ${initialW} ${initialH}" preserveAspectRatio="xMidYMid meet">
            <defs>
              <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
                <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--faint)"></path>
              </marker>
            </defs>
            <g id="map-edges">${edgeHTML}</g>
            <g id="map-nodes">${nodeHTMLs}</g>
          </svg>
          <div class="map-controls">
            <button data-zoom="in" aria-label="放大">+</button>
            <button data-zoom="out" aria-label="缩小">−</button>
            <button data-zoom="fit" aria-label="适应">⤢</button>
          </div>
        </div>
      </div>
      <div class="map-side" id="map-side" data-selectable data-source="map">${defaultSide(data)}</div>
    </div>`;

  const side = el.querySelector("#map-side");
  const svg = el.querySelector("#map-svg");
  const wrap = el.querySelector("#map-wrap");
  let view = { x: 0, y: 0, w: initialW, h: initialH };
  let dragging = false;
  let dragStart = null;
  let selectedId = null;
  let filter = "all";

  function applyView() {
    svg.setAttribute("viewBox", `${view.x} ${view.y} ${view.w} ${view.h}`);
  }
  function fit() {
    view = { x: 0, y: 0, w: initialW, h: initialH };
    applyView();
  }
  function zoom(factor, cx = null, cy = null) {
    const rect = svg.getBoundingClientRect();
    const px = cx == null ? 0.5 : (cx - rect.left) / rect.width;
    const py = cy == null ? 0.5 : (cy - rect.top) / rect.height;
    const nw = Math.max(280, Math.min(initialW * 2.6, view.w * factor));
    const nh = nw * (view.h / view.w);
    const wx = view.x + view.w * px;
    const wy = view.y + view.h * py;
    view = { x: wx - nw * px, y: wy - nh * py, w: nw, h: nh };
    applyView();
  }
  function select(id) {
    selectedId = id;
    const node = nodes.find(n => n.id === id);
    if (!node) { side.innerHTML = defaultSide(data); return; }
    side.innerHTML = nodeSide(node);
    svg.querySelectorAll(".map-node-card").forEach(card => card.classList.remove("selected"));
    svg.querySelector(`[data-id="${CSS.escape(id)}"] .map-node-card`)?.classList.add("selected");
    svg.querySelectorAll(".map-edge").forEach(edge => {
      const active = edge.dataset.source === id || edge.dataset.target === id;
      edge.setAttribute("stroke", active ? "var(--ember)" : "var(--line-strong)");
      edge.setAttribute("opacity", active ? "1" : ".35");
    });
    side.querySelector("[data-start-soc]")?.addEventListener("click", () => ctx.startSocratic(id));
  }
  function clearSelect() {
    selectedId = null;
    side.innerHTML = defaultSide(data);
    svg.querySelectorAll(".map-node-card").forEach(c => c.classList.remove("selected"));
    svg.querySelectorAll(".map-edge").forEach(edge => {
      edge.setAttribute("stroke", "var(--line-strong)");
      edge.setAttribute("opacity", ".7");
    });
  }
  function setFilter(f) {
    filter = f;
    el.querySelectorAll("[data-filter]").forEach(b => b.classList.toggle("active", b.dataset.filter === f));
    svg.querySelectorAll(".map-node-wrap").forEach(node => {
      const show = f === "all" || node.dataset.status === f;
      node.style.opacity = show ? "1" : "0.12";
      node.style.pointerEvents = show ? "auto" : "none";
    });
    svg.querySelectorAll(".map-edge").forEach(edge => {
      const s = nodes.find(n => n.id === edge.dataset.source);
      const t = nodes.find(n => n.id === edge.dataset.target);
      const show = f === "all" || s?.status === f || t?.status === f;
      edge.style.opacity = show ? (selectedId ? ".35" : ".7") : "0.05";
    });
  }

  svg.addEventListener("click", (e) => {
    const wrapEl = e.target.closest?.(".map-node-wrap");
    if (wrapEl) {
      e.stopPropagation();
      select(wrapEl.dataset.id);
    }
  });
  side.addEventListener("click", (e) => {
    const soc = e.target.closest("[data-start-soc]");
    if (soc) ctx.startSocratic(soc.dataset.startSoc);
  });
  svg.addEventListener("wheel", (e) => {
    e.preventDefault();
    zoom(e.deltaY < 0 ? 0.88 : 1.12, e.clientX, e.clientY);
  }, { passive: false });
  svg.addEventListener("pointerdown", (e) => {
    if (e.target.closest?.(".map-node-wrap")) return;
    dragging = true;
    dragStart = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y };
    svg.classList.add("dragging");
    svg.setPointerCapture?.(e.pointerId);
  });
  svg.addEventListener("pointermove", (e) => {
    if (!dragging || !dragStart) return;
    const rect = svg.getBoundingClientRect();
    const dx = (e.clientX - dragStart.x) / rect.width * view.w;
    const dy = (e.clientY - dragStart.y) / rect.height * view.h;
    view.x = dragStart.vx - dx;
    view.y = dragStart.vy - dy;
    applyView();
  });
  const endDrag = () => { dragging = false; dragStart = null; svg.classList.remove("dragging"); };
  svg.addEventListener("pointerup", endDrag);
  svg.addEventListener("pointerleave", endDrag);
  el.querySelectorAll("[data-zoom]").forEach(btn => btn.addEventListener("click", () => {
    if (btn.dataset.zoom === "in") zoom(0.85);
    else if (btn.dataset.zoom === "out") zoom(1.18);
    else fit();
  }));
  el.querySelectorAll("[data-filter]").forEach(btn => btn.addEventListener("click", () => setFilter(btn.dataset.filter)));
  wrap.addEventListener("dblclick", clearSelect);
  ctx.cleanup(() => { /* listeners are on removed DOM nodes */ });
}
