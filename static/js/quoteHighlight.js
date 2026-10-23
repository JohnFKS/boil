/* 引文高亮：把灵感笔记存的 quote 在正文里出现的位置标出来。
   存的是文字快照而不是字符偏移，所以材料重排、重新安装都不会失效。 */

const SKIP = "code, pre, .katex, .note-hl, [data-no-highlight]";

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

function findMatches(text, quotes, limit) {
  const found = [];
  for (const q of quotes) {
    const needle = (q.text || "").trim();
    if (needle.length < 4) continue;
    let from = 0;
    for (let guard = 0; guard < 40; guard += 1) {
      const at = text.indexOf(needle, from);
      if (at < 0) break;
      found.push({ start: at, end: at + needle.length, title: q.title || "" });
      from = at + needle.length;
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

/** 在 root 内高亮所有 quotes（[{text, title}]）出现的位置，返回标记处数。 */
export function highlightQuotes(root, quotes, { limit = 20 } = {}) {
  const list = (quotes || []).filter(q => q && q.text);
  if (!root || !list.length) return 0;
  const first = collect(root);
  const matches = findMatches(first.text, list, limit);
  if (!matches.length) return 0;
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
  return marked;
}
