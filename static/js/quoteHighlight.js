/* 引文高亮：把灵感笔记存的 quote 在正文里出现的位置标出来。
   存的是文字快照而不是字符偏移，所以材料重排、重新安装都不会失效。
   含公式的选区另存一个「去公式可见文本」锚点：KaTeX 渲染出来的 DOM 文本
   与 selection.toString() 对不上，不存锚点就永远高亮不到。 */

const SKIP = "code, pre, .katex, .note-hl, [data-no-highlight]";
const TEX_ANNOTATION = "annotation[encoding='application/x-tex']";

function katexTeX(node) {
  const a = node.querySelector(TEX_ANNOTATION);
  return (a && a.textContent ? a.textContent : "").trim();
}

/** 选区的可读文本：KaTeX 的 MathML/双份字形换成 $TeX$，人能读、也能再渲染。 */
export function cleanRangeText(range) {
  const frag = range.cloneContents();
  const math = frag.querySelectorAll(".katex");
  math.forEach(k => {
    const tex = katexTeX(k);
    k.replaceWith(document.createTextNode(tex ? `$${tex}$` : ""));
  });
  const text = (math.length ? frag.textContent : range.toString())
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
  return { text, hasMath: math.length > 0 };
}

/** 容器里的可读全文（公式保留为 $TeX$），给「追问」的上下文与锚点定位共用。 */
export function readableText(root) {
  if (!root) return "";
  const clone = root.cloneNode(true);
  clone.querySelectorAll(".katex").forEach(k => {
    const tex = katexTeX(k);
    k.replaceWith(document.createTextNode(tex ? `$${tex}$` : ""));
  });
  return clone.textContent || "";
}

function firstInside(range, node, len) {
  let lo = 0;
  let hi = len;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (range.comparePoint(node, mid) === 0) hi = mid;
    else lo = mid + 1;
  }
  return lo;
}

function lastInside(range, node, len) {
  let lo = 0;
  let hi = len;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (range.comparePoint(node, mid) === 0) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}

/** 选区在「去公式可见文本」里的位置 → {search, occurrence}，与 collect() 同一坐标系。 */
export function anchorForRange(range, container) {
  if (!range || !container) return null;
  const { nodes, text } = collect(container);
  if (!nodes.length || !text) return null;
  let start = null;
  let end = null;
  for (const entry of nodes) {
    if (!range.intersectsNode(entry.node)) continue;
    const len = entry.node.nodeValue.length;
    const atStart = range.comparePoint(entry.node, 0);
    const atEnd = range.comparePoint(entry.node, len);
    const from = atStart === 0 ? 0 : firstInside(range, entry.node, len);
    const to = atEnd === 0 ? len : lastInside(range, entry.node, len);
    if (to <= from) continue;
    if (start === null) start = entry.start + from;
    end = entry.start + to;
  }
  if (start === null || end === null || end - start < 4) return null;
  const search = text.slice(start, end);
  let occurrence = 0;
  let at = text.indexOf(search);
  while (at >= 0 && at < start) {
    occurrence += 1;
    at = text.indexOf(search, at + 1);
  }
  return { search, occurrence };
}

function collect(root) {
  const nodes = [];
  let text = "";
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const parent = node.parentElement;
      if (!parent || parent.closest(SKIP)) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  while (walker.nextNode()) {
    const node = walker.currentNode;
    nodes.push({ node, start: text.length, end: text.length + node.nodeValue.length });
    text += node.nodeValue;
  }
  return { nodes, text };
}

const escapeRe = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/**
 * 找引文的位置。先按原样找；找不到再容忍空白差异。
 *
 * 浏览器复制跨块的文字时会在中间塞换行（选中两个 `<li>` 得到 "甲\n乙"），
 * 而这里的坐标轴是把文本节点直接拼起来的，两块之间什么都没有 —— 于是原样匹配永远落空，
 * 用户存了灵感却再也看不到高亮。把空白换成 `\s*` 两种情况都能命中。
 */
function locate(text, needle, from = 0) {
  const at = text.indexOf(needle, from);
  if (at >= 0) return { start: at, length: needle.length };
  const parts = needle.split(/\s+/).filter(Boolean);
  if (parts.length < 2) return { start: -1, length: 0 };
  const re = new RegExp(parts.map(escapeRe).join("\\s*"), "g");
  re.lastIndex = from;
  const match = re.exec(text);
  return match ? { start: match.index, length: match[0].length } : { start: -1, length: 0 };
}

function nthIndex(text, needle, n) {
  let found = locate(text, needle, 0);
  for (let i = 0; i < n && found.start >= 0; i += 1) {
    found = locate(text, needle, found.start + Math.max(1, found.length));
  }
  return found.start;
}

function findMatches(text, quotes, limit) {
  const found = [];
  for (const [qi, q] of quotes.entries()) {
    const anchored = typeof q.search === "string" && q.search.trim().length >= 4;
    const needle = anchored ? q.search : (q.text || "").trim();
    if (needle.trim().length < 4) continue;
    if (anchored) {
      // 有锚点就只认那一处：同一段文字出现多次时不会把整页都点亮
      let at = nthIndex(text, needle, Math.max(0, Number(q.occurrence) || 0));
      let length = at >= 0 ? locate(text, needle, at).length : 0;
      if (at < 0) {
        const hit = (q.text || "").trim();        // 材料改过：退回引文原文
        const back = locate(text, hit, 0);
        at = back.start;
        length = back.length;
      }
      if (at >= 0 && length && needle.trim().length >= 4) {
        found.push({ start: at, end: at + length, title: q.title || "", qi });
      }
      continue;
    }
    let from = 0;
    for (let guard = 0; guard < 40; guard += 1) {
      const hit = locate(text, needle, from);
      if (hit.start < 0) break;
      found.push({ start: hit.start, end: hit.start + hit.length, title: q.title || "", qi });
      from = hit.start + Math.max(1, hit.length);
    }
  }
  found.sort((a, b) => a.start - b.start);
  const kept = [];
  let cursor = -1;
  for (const m of found) {
    if (m.start < cursor) continue;
    kept.push(m);
    cursor = m.end;
    if (kept.length >= limit) break;
  }
  return kept;
}

function wrapRange(entry, from, to, title) {
  const range = document.createRange();
  range.setStart(entry.node, from);
  range.setEnd(entry.node, to);
  const mark = document.createElement("mark");
  mark.className = "note-hl";
  if (title) mark.title = title;
  range.surroundContents(mark);
}

/** 在 root 内高亮 quotes 出现的位置，返回 {marks, quotes}（段数 / 命中的灵感条数）。 */
export function highlightQuotes(root, quotes, { limit = 20 } = {}) {
  const list = (quotes || []).filter(q => q && q.text);
  if (!root || !list.length) return { marks: 0, quotes: 0 };
  const first = collect(root);
  const matches = findMatches(first.text, list, limit);
  if (!matches.length) return { marks: 0, quotes: 0 };
  let marked = 0;
  for (let i = matches.length - 1; i >= 0; i -= 1) {
    const match = matches[i];
    const again = collect(root);
    for (const entry of again.nodes) {
      if (entry.end <= match.start || entry.start >= match.end) continue;
      const from = Math.max(0, match.start - entry.start);
      const to = Math.min(entry.node.nodeValue.length, match.end - entry.start);
      if (to <= from) continue;
      wrapRange(entry, from, to, match.title);
      marked += 1;
    }
  }
  return { marks: marked, quotes: new Set(matches.map(m => m.qi)).size };
}
