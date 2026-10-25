/* Knowledge map: layered SVG graph with pan/zoom, filters and a side inspector. */
import {
  escapeHTML, escapeAttr, icon, pct, statusMeta, truncate, toast, confirmDialog,
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
  const kindLabel = { concept: "概念", principle: "原理", procedure: "方法", fact: "事实", meta: "策略" }[n.point_kind] || "";
  return `<g class="map-node-wrap" data-id="${escapeAttr(n.id)}" data-status="${escapeAttr(n.status)}" data-section="${escapeAttr(String(n.section || "").split("#")[0])}">
    <foreignObject x="${p.x}" y="${p.y}" width="${p.w}" height="${p.h}">
      <div xmlns="http://www.w3.org/1999/xhtml" class="map-node-card ${st.className}">
        <div class="node-top">
          <span class="node-title">${escapeHTML(n.title)}</span>
          <span class="node-status" style="background:${st.color};box-shadow:0 0 0 3px ${st.color}22"></span>
        </div>
        <div class="node-bottom">
          <span class="node-bar"><i style="width:${mastery}%;background:${st.color}"></i></span>
          <span style="font-size:9px;color:var(--faint)">${n.due_count ? `${n.due_count} 待复习` : (kindLabel || st.label)}</span>
        </div>
      </div>
    </foreignObject>
  </g>`;
}

const KIND_GAP_LABEL = {
  concept: "概念", principle: "原理", procedure: "方法/步骤", fact: "事实/参数", meta: "学习策略",
};

function structureCard(block) {
  const cov = block.coverage, ground = block.grounding;
  const sections = block.sections || [];
  const chapters = block.chapters || [];
  const gaps = block.kind_gaps || [];
  const charCount = (ids) => sections.filter(s => ids.has(s.id))
    .reduce((n, s) => n + (Number(s.chars) || 0), 0);
  const chapterRows = chapters.map((ch) => {
    const ids = new Set(ch.section_ids || []);
    return `<button type="button" class="chapter-row" data-sections="${escapeAttr([...ids].join(","))}">
      <span class="chapter-title">${escapeHTML(truncate(ch.title || "未命名章", 20))}</span>
      <span class="faint tiny">${ids.size} 节 · ${charCount(ids).toLocaleString()} 字${ch.engine === "order" ? " · 按顺序分章" : ""}</span>
    </button>`;
  }).join("");
  return `<div class="card map-side-card">
    <div class="between"><span class="side-label">拆解体检</span>
      ${block.coverage_mode ? `<span class="badge badge-muted">覆盖档 ${block.coverage_mode === "full" ? "高" : "标准"}</span>` : ""}</div>
    <div class="facet-row"><span class="muted">材料覆盖</span><span>${cov ? pct(cov.ratio) : "—"}</span>
      <span class="faint tiny">${cov ? `${cov.covered}/${cov.sections} 节` : "没有小节登记表"}</span></div>
    <div class="facet-row"><span class="muted">能指回原文</span><span>${ground ? pct(ground.rate) : "—"}</span>
      <span class="faint tiny">${ground ? `${ground.verified}/${ground.total} 条` : ""}</span></div>
    <div class="facet-row"><span class="muted">分层</span><span>${chapters.length ? `${chapters.length} 章` : "单层"}</span>
      <span class="faint tiny">${sections.length} 节</span></div>
    ${cov && cov.missing?.length ? `<details class="mt-2">
      <summary class="tiny">还有 ${cov.missing.length} 节没被拆出知识点</summary>
      <ul class="missing-list">${cov.missing.slice(0, 8).map(m => `<li><code>${escapeHTML(m.id)}</code> ${escapeHTML(truncate(m.title || "", 22))} <span class="faint">${(m.chars || 0).toLocaleString()} 字</span></li>`).join("")}</ul>
      ${cov.missing.length > 8 ? `<p class="faint tiny">其余 ${cov.missing.length - 8} 节未列出。</p>` : ""}
      <p class="faint tiny">没覆盖不等于讲得差，只是这部分材料还没进你的计划；把覆盖档调到「高」再编译一次通常能补上。</p>
    </details>` : ""}
    ${gaps.length ? `<p class="faint tiny mt-2">这份拆解没出过的类型：${gaps.map(g => escapeHTML(KIND_GAP_LABEL[g] || g)).join("、")}。类型越全，练到的问法才越接近考卷 —— 只有概念时，"为什么"和"怎么做"仍没练过。</p>` : ""}
    ${(block.lint || []).length ? `<p class="faint tiny mt-2">质检提示：${(block.lint || []).map(escapeHTML).join("、")}</p>` : ""}
    ${chapters.length > 1 ? `<div class="divider"></div>${chapterRows}` : ""}
  </div>`;
}

function defaultSide(data) {
  const goal = data.goal || {};
  const s = data.summary || {};
  const structure = (data.structure || []).map(structureCard).join("");
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
  ${structure}
  <div class="card map-side-card">
    <div class="side-label">怎么用这张图</div>
    <div class="rule-list">
      <div class="rule"><span class="n">1</span><span>先看颜色：灰=未开始，金=学习中，绿=已烧开，橙=需复习。</span></div>
      <div class="rule"><span class="n">2</span><span>沿着线走：前置关系是从左到右，不要跳着啃。</span></div>
      <div class="rule"><span class="n">3</span><span>点开一个节点，直接开始预习、苏格拉底或费曼。</span></div>
    </div>
  </div>
  ${(data.duplicates || []).length ? `<div class="card map-side-card dup-card">
    <div class="side-label">疑似重复 ${data.duplicates.length} 对${data.dupEngine === "semantic" ? " · 语义 + 字面" : " · 只看字面"}</div>
    <p class="faint tiny">合并不是删除：被吸收的单元不再单独出现，内容与复习卡仍在原处可访问；合错了可在被吸收单元的页面上撤销（关系边会按记录回滚）。分数只提候选，字面以外都要模型签字、你再点一次确认。</p>
    ${data.duplicates.slice(0, 5).map((d, i) => dupRow(d, i)).join("")}
    ${data.judged ? `<p class="faint tiny mt-2">其中 ${data.judged} 对分数落在中间带，已请模型判过。</p>` : ""}
  </div>` : ""}`;
}

const DUP_BASIS = {
  lexical: "字面近乎一致", model: "模型判定是同一件事",
  user: "你写明理由后合并", none: "",
};

function dupSignal(d) {
  const parts = [`字面 ${Math.round((d.lexical || 0) * 100)}%`];
  if (d.semantic !== null && d.semantic !== undefined) {
    parts.push(`语义 ${Math.round(d.semantic * 100)}%`);
  }
  return parts.join(" · ");
}

function dupRow(d, i) {
  const allowed = d.allowed !== false;
  const pending = !!d.pending_judgement;
  const why = allowed
    ? (DUP_BASIS[d.basis] || "可以合并") + (d.model_reason ? `：${d.model_reason}` : "")
    : pending
      ? `等模型签字：${d.decision_reason || "语义分只能提候选"}`
      : `不能合并：${d.decision_reason || "分数不够"}`;
  return `
      <div class="dup-row${allowed ? "" : " blocked"}">
        <span class="dup-titles"><strong>${escapeHTML(d.titles?.[0] || "")}</strong><span class="faint"> ≈ </span><strong>${escapeHTML(d.titles?.[1] || "")}</strong>
          <span class="faint tiny">${escapeHTML(dupSignal(d))}</span></span>
        <span class="dup-why faint tiny">${escapeHTML(why)}</span>
        <button class="link-btn${allowed ? "" : " warn"}" type="button" data-merge="${escapeAttr(d.merge)}" data-keep="${escapeAttr(d.keep)}" data-row="${i}">${allowed ? "并入前者" : pending ? "判定并并入" : "坚持合并"}</button>
      </div>`;
}

function nodeSide(n, secTitles) {
  const st = statusMeta[n.status] || statusMeta.new;
  const facets = n.facets || {};
  const labels = { recall: "回忆", explain: "解释", apply: "应用", transfer: "迁移" };
  const kindLabel = { concept: "概念", principle: "原理", procedure: "方法", fact: "事实", meta: "策略" }[n.point_kind];
  const section = n.section ? String(n.section).split("#")[0] : "";
  return `<div class="card map-side-card">
    <div class="between"><span class="badge ${n.status === "boiled" ? "badge-teal" : n.status === "rusting" ? "badge-ember" : n.status === "learning" ? "badge-gold" : "badge-muted"}">${st.label}</span><span class="faint tiny">T ${Math.round(n.heat || 0)}° · ${n.due_count ? `${n.due_count} 张待复习` : `${n.estimated_minutes || 45} 分钟`}</span></div>
    <div class="map-side-title mt-3">${escapeHTML(n.title)}</div>
    <div class="map-side-essence">${escapeHTML(n.essence || "")}</div>
    ${kindLabel || section ? `<div class="node-kind-row mt-2">
      ${kindLabel ? `<span class="badge badge-violet">${kindLabel}</span>` : ""}
      ${section ? `<span class="faint tiny">出自 <code>${escapeHTML(section)}</code> ${escapeHTML(secTitles?.[section] || "")}</span>` : `<span class="badge badge-muted">无登记表</span>`}
    </div>` : ""}
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
  let data, dup;
  try {
    // 并行：候选第一次要联网算嵌入，串行等就是把两个延迟加起来
    [data, dup] = await Promise.all([
      ctx.api.get("/api/map"),
      ctx.api.get("/api/units/duplicates").catch(() => null),
    ]);
  } catch (e) {
    if (e.status === 404) { ctx.navigate("#/onboarding", { replace: true }); return; }
    throw e;
  }
  data.duplicates = (dup && dup.pairs) || [];
  data.dupEngine = (dup && dup.engine) || "rules";
  data.judged = (dup && dup.judged) || 0;
  const nodes = data.nodes || [];
  const edges = data.edges || [];
  const secTitles = {};
  for (const block of data.structure || []) {
    for (const s of block.sections || []) secTitles[s.id] = s.title;
  }
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
  let chapters = null;

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
    side.innerHTML = nodeSide(node, secTitles);
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
    chapters = null;
    el.querySelectorAll("[data-filter]").forEach(b => b.classList.toggle("active", b.dataset.filter === f));
    side.querySelectorAll("[data-sections]").forEach(b => b.classList.remove("active"));
    applyVisibility();
  }

  function applyVisibility() {
    const inChapter = (node) => !chapters
      || chapters.includes((node.dataset.section || "").split("#")[0]);
    const statusOk = (node) => filter === "all" || node.dataset.status === filter;
    svg.querySelectorAll(".map-node-wrap").forEach(node => {
      const show = statusOk(node) && inChapter(node);
      node.style.opacity = show ? "1" : "0.12";
      node.style.pointerEvents = show ? "auto" : "none";
    });
    const byId = new Map(nodes.map(n => [n.id, n]));
    svg.querySelectorAll(".map-edge").forEach(edge => {
      const s = byId.get(edge.dataset.source), t = byId.get(edge.dataset.target);
      const show = !s || !t || (filter === "all" || s.status === filter || t.status === filter);
      edge.setAttribute("opacity", show ? (selectedId ? ".35" : ".7") : "0.05");
    });
  }

  // 只看某一章：章是小节登记表给的，不是模型随口说的
  function showChapter(btn) {
    const ids = (btn.dataset.sections || "").split(",").filter(Boolean);
    const same = chapters && chapters.join() === ids.join();
    side.querySelectorAll("[data-sections]").forEach(b => b.classList.remove("active"));
    if (same || !ids.length) { chapters = null; applyVisibility(); return; }
    chapters = ids;
    btn.classList.add("active");
    applyVisibility();
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
    if (soc) { ctx.startSocratic(soc.dataset.startSoc); return; }
    const chap = e.target.closest("[data-sections]");
    if (chap) showChapter(chap);
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
  el.querySelectorAll("[data-merge]").forEach(btn => btn.addEventListener("click", async (e) => {
    const target = e.currentTarget;
    const away = target.dataset.merge;
    const keep = target.dataset.keep;
    const pair = (data.duplicates || [])[Number(target.dataset.row)] || {};
    if (!away || !keep) return;
    if (pair.pending_judgement) {
      // 排在判定上限之外的对：点下去先请模型判一次，同意继续再POST（服务端会带上判定）
      const sure = await confirmDialog({
        title: "这一对还没请模型判过",
        message: `字面 ${Math.round((pair.lexical || 0) * 100)}%、语义 ${Math.round((pair.semantic || 0) * 100)}%。`
          + "继续会先请模型判一次是否同一件事；模型不同意就不会改动任何数据。",
        confirmLabel: "请模型判定", danger: true,
      });
      if (sure) doMerge(away, keep, { confirmed: true }, target);
      return;
    }
    if (pair.allowed === false) { askOverride(pair, target); return; }   // 拒绝不等于封死：人可以署名坚持
    if (pair.allowed !== false && pair.basis !== "lexical") {
      // 模型对中间带的判定两次能给出相反答案，所以它批准时必须让人再点头一次
      const sure = await confirmDialog({
        title: "这一对靠模型签字，不是靠分数",
        message: `${pair.model_reason ? `模型的理由：${pair.model_reason}。` : "点下去会先请模型判一次。"}`
          + `语义 ${Math.round((pair.semantic || 0) * 100)}%、字面 ${Math.round((pair.lexical || 0) * 100)}%——`
          + "真重复与最危险的假对只差几个点，分数分不开；合并会重连前置关系且当前不可撤销。",
        confirmLabel: "确认合并", danger: true,
      });
      if (!sure) return;
      doMerge(away, keep, { confirmed: true }, target);   // 人点过头才允许非字面合并
      return;
    }
    doMerge(away, keep, {}, target);
  }));

  function askOverride(pair, btn) {
    const box = document.createElement("div");
    box.innerHTML = `
      <p class="faint tiny">${escapeHTML(pair.decision_reason || "分数不够")}</p>
      <p class="tiny mt-2">分数和模型都不认为这俩是同一件事。真要坚持，请写下理由——理由会写进这个单元的合并记录，日后看得见的就是这句话。</p>
      <textarea class="textarea mt-2" id="merge-why" rows="2" placeholder="例如：这两条在我的材料里就是同一个概念的两种译名"></textarea>`;
    const modal = ctx.openModal({
      title: "坚持合并",
      subtitle: `${pair.titles?.[0] || ""} → ${pair.titles?.[1] || ""}`,
      body: box,
      onOpen: () => box.querySelector("#merge-why")?.focus(),
      actions: [
        { label: "取消", kind: "btn-ghost" },
        { label: "写明理由并合并", kind: "btn-danger", onClick: () => {
            const why = (box.querySelector("#merge-why").value || "").trim();
            if (why.length < 4) { toast("理由至少写 4 个字，不然这次合并说不清是谁的决定", "info"); return false; }
            modal.close();
            doMerge(pair.merge, pair.keep, { reason: why, override: true, confirmed: true }, btn);
          } },
      ],
    });
  }

  async function doMerge(away, keep, extra, btn) {
    btn.disabled = true;
    const label = btn.innerHTML;
    btn.innerHTML = "合并中…";
    try {
      const res = await ctx.api.post(`/api/units/${encodeURIComponent(away)}/merge`,
                                     { into: keep, ...extra });
      toast(`已把「${res.from_title}」并入「${res.into_title}」（${DUP_BASIS[res.basis] || res.basis || "?"}）；`
            + `${res.cards_kept} 张复习卡保留。`, "success", 5200);
      await ctx.refreshView();
    } catch (err) {
      toast(err.message || "合并失败", "error", 5200);
      btn.disabled = false;
      btn.innerHTML = label;
    }
  }
  ctx.cleanup(() => { /* listeners are on removed DOM nodes */ });
}
