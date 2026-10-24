/* Boil UI utilities: escaping, markdown, icons, charts, modal, toast. */

export const qs = (sel, root = document) => root.querySelector(sel);
export const qsa = (sel, root = document) => [...root.querySelectorAll(sel)];

export function uid(prefix = "u") {
  return `${prefix}_${Math.random().toString(36).slice(2, 9)}`;
}

export function clamp(n, low, high) {
  return Math.max(low, Math.min(high, n));
}

export function escapeHTML(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

export function escapeAttr(value) {
  return escapeHTML(value).replaceAll("`", "&#96;");
}

export function truncate(text, n = 120) {
  const s = String(text ?? "").replace(/\s+/g, " ").trim();
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

export function formatDate(value, opts = {}) {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short", day: "numeric", ...opts,
  }).format(d);
}

export function formatDateTime(value) {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  }).format(d);
}

export function relativeTime(value) {
  if (!value) return "";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "";
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} 天前`;
  return formatDate(value);
}

export function daysUntil(value) {
  if (!value) return null;
  const target = new Date(value + (String(value).length === 10 ? "T23:59:59" : ""));
  const now = new Date();
  return Math.ceil((target - now) / 86400000);
}

export function pct(value, digits = 0) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "0%";
  return `${(n * 100).toFixed(digits)}%`;
}

export function formatMinutes(minutes) {
  const n = Number(minutes) || 0;
  if (n < 60) return `${n} 分钟`;
  const h = Math.floor(n / 60);
  const m = n % 60;
  return m ? `${h} 小时 ${m} 分` : `${h} 小时`;
}

export function scoreColor(score) {
  if (score >= 0.8) return "var(--teal)";
  if (score >= 0.55) return "var(--gold)";
  if (score > 0) return "var(--ember)";
  return "var(--faint)";
}

export const statusMeta = {
  new: { label: "未开始", className: "new", color: "var(--faint)" },
  learning: { label: "学习中", className: "learning", color: "var(--gold)" },
  boiled: { label: "已烧开", className: "boiled", color: "var(--teal)" },
  rusting: { label: "需复习", className: "rusting", color: "var(--ember)" },
};

export const kindMeta = {
  recall: { label: "回忆", className: "recall" },
  explain: { label: "解释", className: "explain" },
  apply: { label: "应用", className: "apply" },
  boundary: { label: "边界", className: "boundary" },
  transfer: { label: "迁移", className: "transfer" },
  contrast: { label: "对比", className: "contrast" },
};

export function eventMeta(event) {
  const type = event?.type || "";
  const map = {
    "goal.created": { icon: "target", title: "创建了学习目标", tone: "ember" },
    "plan.built": { icon: "map", title: "重排了学习路径", tone: "teal" },
    "pack.installed": { icon: "library", title: "安装了内容包", tone: "violet" },
    "socratic.started": { icon: "chat", title: "开始苏格拉底对话", tone: "ember" },
    "socratic.completed": { icon: "spark", title: "完成苏格拉底对话", tone: "teal" },
    "feynman.evaluated": { icon: "teach", title: "完成一次费曼讲解", tone: "gold" },
    "review.rated": { icon: "review", title: "复习了一张卡", tone: "teal" },
    "data.reset": { icon: "refresh", title: "重置了数据", tone: "danger" },
  };
  return map[type] || { icon: "dot", title: type || "学习活动", tone: "muted" };
}

/* ---------- icons ---------- */
const ICONS = {
  today: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4"/>',
  map: '<circle cx="5" cy="6" r="2.2"/><circle cx="19" cy="6" r="2.2"/><circle cx="12" cy="18" r="2.2"/><path d="M7.2 6h9.6M6.2 8l4.3 8.1M17.8 8l-4.3 8.1"/>',
  review: '<rect x="3" y="6" width="13" height="14" rx="2.5"/><path d="M7 3h11a2 2 0 0 1 2 2v12"/><path d="M7 11h5M7 15h3"/>',
  library: '<path d="M4 19V7a3 3 0 0 1 3-3h10a3 3 0 0 1 3 3v12"/><path d="M4 19a3 3 0 0 1 3-3h13"/><path d="M8 8h7"/>',
  insights: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
  settings: '<circle cx="12" cy="12" r="3.2"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06-2.47 2.47-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1.03 1.56V21h-3.5v-.44A1.7 1.7 0 0 0 9.84 19a1.7 1.7 0 0 0-1.87.34l-.06.06-2.47-2.47.06-.06A1.7 1.7 0 0 0 5.84 15a1.7 1.7 0 0 0-1.56-1.03H4v-3.5h.28A1.7 1.7 0 0 0 5.84 9a1.7 1.7 0 0 0-.34-1.87l-.06-.06L7.91 4.6l.06.06A1.7 1.7 0 0 0 9.84 5a1.7 1.7 0 0 0 1.03-1.56V3h3.5v.44A1.7 1.7 0 0 0 15.4 5a1.7 1.7 0 0 0 1.87-.34l.06-.06 2.47 2.47-.06.06A1.7 1.7 0 0 0 19.4 9a1.7 1.7 0 0 0 1.56 1.03H21v3.5h-.04A1.7 1.7 0 0 0 19.4 15Z"/>',
  flame: '<path d="M12 22c4 0 7-2.8 7-6.6 0-3.1-1.7-5.5-4-7.7-.6-.6-1.2-1.5-1.5-2.7-.2-.8-1.2-1-1.6-.3-1.1 2-1 4.2.2 6.1-1.4-.4-2.4-1.5-3-2.8-.3-.7-1.2-.8-1.5-.1C6.4 10 5 12.5 5 15.4 5 19.2 8 22 12 22Z"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  spark: '<path d="M12 3l1.6 5.4L19 10l-5.4 1.6L12 17l-1.6-5.4L5 10l5.4-1.6L12 3Z"/><path d="M19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8L19 16Z"/>',
  brain: '<path d="M9.5 4A3.5 3.5 0 0 0 6 7.5v.3A3.2 3.2 0 0 0 4 11a3.2 3.2 0 0 0 1.5 2.7A3.3 3.3 0 0 0 9 19h1V4H9.5ZM14.5 4A3.5 3.5 0 0 1 18 7.5v.3a3.2 3.2 0 0 1 2 3.2 3.2 3.2 0 0 1-1.5 2.7A3.3 3.3 0 0 1 15 19h-1V4h.5Z"/>',
  chat: '<path d="M21 12a8 8 0 0 1-8 8H7l-4 2V12a8 8 0 0 1 8-8h2a8 8 0 0 1 8 8Z"/><path d="M8 12h.01M12 12h.01M16 12h.01"/>',
  teach: '<circle cx="9" cy="8" r="3"/><path d="M3 20a6 6 0 0 1 12 0"/><path d="M16 5h5M18.5 2.5v5"/><path d="M16 11l2 2 4-4"/>',
  check: '<path d="M4 12l5 5L20 6"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7v5l3 2"/>',
  play: '<path d="M8 5l11 7-11 7V5Z"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4"/>',
  moon: '<path d="M20 14.5A8.5 8.5 0 0 1 9.5 4 8.5 8.5 0 1 0 20 14.5Z"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.6-3.6"/>',
  refresh: '<path d="M20 11a8 8 0 1 0-2.3 6.7"/><path d="M20 4v7h-7"/>',
  download: '<path d="M12 3v12M7 10l5 5 5-5"/><path d="M4 21h16"/>',
  upload: '<path d="M12 21V9M7 14l5-5 5 5"/><path d="M4 3h16"/>',
  book: '<path d="M4 5a2 2 0 0 1 2-2h13v18H6a2 2 0 0 1-2-2V5Z"/><path d="M4 17h15M8 7h7"/>',
  target: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r="1"/>',
  graph: '<path d="M4 19V5M4 19h16"/><path d="M7 15l3-4 3 2 4-6"/><circle cx="7" cy="15" r="1.4"/><circle cx="10" cy="11" r="1.4"/><circle cx="13" cy="13" r="1.4"/><circle cx="17" cy="7" r="1.4"/>',
  wand: '<path d="M4 20L16 8"/><path d="M14 4l1 3 3 1-3 1-1 3-1-3-3-1 3-1 1-3Z"/><path d="M19 15l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7.7-2Z"/>',
  alert: '<path d="M12 3l9 17H3L12 3Z"/><path d="M12 9v5M12 17h.01"/>',
  chevron: '<path d="M6 9l6 6 6-6"/>',
  chevronRight: '<path d="M9 6l6 6-6 6"/>',
  chevronLeft: '<path d="M15 6l-6 6 6 6"/>',
  send: '<path d="M21 3L10 14"/><path d="M21 3l-7 18-4-7-7-4 18-7Z"/>',
  hint: '<path d="M9 18h6"/><path d="M10 22h4"/><path d="M8.5 14.5A6 6 0 1 1 15.5 14.5C14.6 15.2 14 16 14 18h-4c0-2-.6-2.8-1.5-3.5Z"/>',
  trophy: '<path d="M8 4h8v5a4 4 0 0 1-8 0V4Z"/><path d="M8 5H5a3 3 0 0 0 3 3M16 5h3a3 3 0 0 1-3 3"/><path d="M12 13v4M9 21h6M10 17h4"/>',
  cpu: '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/><rect x="10" y="10" width="4" height="4"/>',
  shield: '<path d="M12 3l8 3v6c0 4.5-3.4 7.6-8 9-4.6-1.4-8-4.5-8-9V6l8-3Z"/><path d="M9 12l2 2 4-4"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.5.5l2-2a5 5 0 0 0-7-7l-1.5 1.5"/><path d="M14 11a5 5 0 0 0-7.5-.5l-2 2a5 5 0 0 0 7 7l1.5-1.5"/>',
  calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4M16 3v4M3 10h18"/>',
  zap: '<path d="M13 2L4 14h7l-1 8 9-12h-7l1-8Z"/>',
  eye: '<path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z"/><circle cx="12" cy="12" r="2.6"/>',
  eyeOff: '<path d="M3 3l18 18"/><path d="M10.6 6.2A10.8 10.8 0 0 1 12 6c6.5 0 10 6 10 6a17 17 0 0 1-3 3.5M6.2 6.8A17.6 17.6 0 0 0 2 12s3.5 6 10 6c1.2 0 2.3-.2 3.3-.6"/>',
  dot: '<circle cx="12" cy="12" r="3"/>',
  volume: '<path d="M4 9v6h4l5 4V5L8 9H4Z"/><path d="M16 8a5 5 0 0 1 0 8M19 5a9 9 0 0 1 0 14"/>',
  more: '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
  trash: '<path d="M4 7h16M10 3h4M7 7l1 14h8l1-14"/><path d="M10 11v6M14 11v6"/>',
  edit: '<path d="M4 20h4L20 8l-4-4L4 16v4Z"/><path d="M14 6l4 4"/>',
  external: '<path d="M14 3h7v7M21 3l-9 9"/><path d="M10 5H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-5"/>',
  filter: '<path d="M4 5h16l-6 7v6l-4 2v-8L4 5Z"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13"/><circle cx="4" cy="6" r="1"/><circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/>',
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  compass: '<circle cx="12" cy="12" r="9"/><path d="M15.5 8.5l-2 5-5 2 2-5 5-2Z"/>',
};

export function icon(name, size = 18, extra = "") {
  const body = ICONS[name] || ICONS.dot;
  return `<svg class="icon ${extra}" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
}

/* ---------- markdown ---------- */
function safeUrl(url) {
  const u = String(url || "").trim();
  if (/^(https?:\/\/|mailto:|#|\/)/i.test(u)) return u;
  return "#";
}

export function renderMarkdown(text) {
  let src = String(text ?? "").replace(/\r\n/g, "\n");
  const codeBlocks = [];
  src = src.replace(/```([a-zA-Z0-9_-]*)\n?([\s\S]*?)```/g, (_, lang, code) => {
    const i = codeBlocks.length;
    codeBlocks.push(`<pre><code class="lang-${escapeAttr(lang)}">${escapeHTML(code.replace(/\n$/, ""))}</code></pre>`);
    return `\u0000CODEBLOCK${i}\u0000`;
  });
  src = escapeHTML(src);
  // inline code
  const inline = [];
  src = src.replace(/`([^`\n]+)`/g, (_, code) => {
    const i = inline.length;
    inline.push(`<code>${code}</code>`);
    return `\u0000INLINE${i}\u0000`;
  });
  // headings：正文里的 `#` 是"这篇材料内部"的层级，页面标题已经由外壳的 h1 提供了，
  // 所以整体下移两级 —— 否则一个视图里会出现两个 h1，读屏的"按标题跳转"就废了。
  src = src.replace(/^######\s+(.+)$/gm, '<h6>$1</h6>')
    .replace(/^#####\s+(.+)$/gm, '<h6>$1</h6>')
    .replace(/^####\s+(.+)$/gm, '<h5>$1</h5>')
    .replace(/^###\s+(.+)$/gm, '<h4>$1</h4>')
    .replace(/^##\s+(.+)$/gm, '<h3>$1</h3>')
    .replace(/^#\s+(.+)$/gm, '<h3>$1</h3>');
  // blockquote
  src = src.replace(/^&gt;\s?(.*)$/gm, '<blockquote>$1</blockquote>');
  // bold/italic
  src = src.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[\s(])\*([^*\n]+)\*/g, '$1<em>$2</em>')
    .replace(/(^|[\s(])_([^_\n]+)_/g, '$1<em>$2</em>');
  // links
  src = src.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_, label, url) =>
    `<a href="${escapeAttr(safeUrl(url))}" target="_blank" rel="noopener noreferrer">${label}</a>`);
  // lists
  const lines = src.split("\n");
  let out = [];
  let listType = null;
  const closeList = () => {
    if (listType) {
      out.push(`</${listType}>`);
      listType = null;
    }
  };
  for (const line of lines) {
    const ul = line.match(/^\s*[-*•]\s+(.+)$/);
    const ol = line.match(/^\s*\d+[.)、]\s+(.+)$/);
    if (ul || ol) {
      const type = ul ? "ul" : "ol";
      if (listType !== type) {
        closeList();
        listType = type;
        out.push(`<${type}>`);
      }
      out.push(`<li>${(ul || ol)[1]}</li>`);
    } else if (!line.trim()) {
      closeList();
      out.push("");
    } else {
      closeList();
      out.push(line);
    }
  }
  closeList();
  src = out.join("\n");
  // paragraphs
  const blocks = src.split(/\n{2,}/).map((block) => {
    const t = block.trim();
    if (!t) return "";
    if (/^<(h[1-6]|ul|ol|pre|blockquote|table|div)/.test(t)) return t;
    return `<p>${t.replace(/\n/g, "<br>")}</p>`;
  }).filter(Boolean);
  let html = blocks.join("\n");
  // restore inline code / code blocks
  html = html.replace(/\u0000INLINE(\d+)\u0000/g, (_, i) => inline[Number(i)] || "");
  html = html.replace(/\u0000CODEBLOCK(\d+)\u0000/g, (_, i) => codeBlocks[Number(i)] || "");
  return html;
}

export function prose(text) {
  return `<div class="prose">${renderMarkdown(text)}</div>`;
}

export function renderMath(root = document.body) {
  if (!root || typeof window.renderMathInElement !== "function") return;
  try {
    window.renderMathInElement(root, {
      delimiters: [
        { left: "$$", right: "$$", display: true },
        { left: "\\[", right: "\\]", display: true },
        { left: "$", right: "$", display: false },
        { left: "\\(", right: "\\)", display: false },
      ],
      throwOnError: false,
      ignoredTags: ["script", "noscript", "style", "textarea", "pre", "code"],
    });
  } catch (e) {
    console.warn("KaTeX render failed", e);
  }
}

export function streamingText(text) {
  return escapeHTML(text).replace(/\n/g, "<br>");
}

/* ---------- charts ---------- */
export function ringSVG(percent, { size = 180, stroke = 9, color = "var(--ember)", track = "rgba(255,255,255,0.09)" } = {}) {
  const p = clamp(Number(percent) || 0, 0, 100);
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const offset = c * (1 - p / 100);
  return `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" aria-label="${p}%">
    <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="${track}" stroke-width="${stroke}"/>
    <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="${color}" stroke-width="${stroke}"
      stroke-linecap="round" stroke-dasharray="${c}" stroke-dashoffset="${offset}"
      style="transition: stroke-dashoffset 1s cubic-bezier(.22,1,.36,1)"/>
  </svg>`;
}

export function radarSVG(values, labels, { size = 230, color = "var(--ember)" } = {}) {
  const cx = size / 2, cy = size / 2 + 4;
  const radius = size * 0.31;
  const n = values.length;
  const angle = (i) => -Math.PI / 2 + (i * 2 * Math.PI) / n;
  const point = (i, r) => [cx + Math.cos(angle(i)) * r, cy + Math.sin(angle(i)) * r];
  const grid = [0.25, 0.5, 0.75, 1].map((level) => {
    const pts = values.map((_, i) => point(i, radius * level).map(v => v.toFixed(1)).join(",")).join(" ");
    return `<polygon points="${pts}" fill="none" stroke="rgba(255,255,255,0.08)" stroke-width="1"/>`;
  }).join("");
  const axes = values.map((_, i) => {
    const [x, y] = point(i, radius);
    return `<line x1="${cx}" y1="${cy}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" stroke="rgba(255,255,255,0.07)" stroke-width="1"/>`;
  }).join("");
  const pts = values.map((v, i) => point(i, radius * clamp(Number(v) || 0, 0, 1)).map(x => x.toFixed(1)).join(",")).join(" ");
  const labelsHTML = labels.map((label, i) => {
    const [x, y] = point(i, radius + 25);
    const anchor = x < cx - 12 ? "end" : x > cx + 12 ? "start" : "middle";
    return `<text x="${x.toFixed(1)}" y="${(y + 4).toFixed(1)}" fill="var(--muted)" font-size="11" text-anchor="${anchor}" font-family="var(--font-body)">${escapeHTML(label)}</text>`;
  }).join("");
  const dots = values.map((v, i) => {
    const [x, y] = point(i, radius * clamp(Number(v) || 0, 0, 1));
    return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3" fill="${color}" stroke="var(--panel-solid)" stroke-width="1.5"/>`;
  }).join("");
  return `<svg width="100%" viewBox="-20 -18 ${size + 40} ${size + 50}" class="radar" role="img" aria-label="掌握雷达">
    ${grid}${axes}
    <polygon points="${pts}" fill="${color}" fill-opacity="0.16" stroke="${color}" stroke-width="2" stroke-linejoin="round"/>
    ${dots}${labelsHTML}
  </svg>`;
}

export function lineChartSVG(points, { width = 440, height = 190, color = "var(--teal)", valueMax = 1 } = {}) {
  if (!points || points.length < 2) {
    return `<div class="empty" style="padding:28px 10px"><p>数据还不够。完成几次复习后，这里会出现保留率曲线。</p></div>`;
  }
  const padL = 30, padR = 14, padT = 16, padB = 28;
  const w = width - padL - padR, h = height - padT - padB;
  const max = valueMax || Math.max(...points.map(p => p.value || 0), 0.01);
  const x = (i) => padL + (i / (points.length - 1)) * w;
  const y = (v) => padT + h - (clamp(v, 0, max) / max) * h;
  const path = points.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ");
  const area = `${path} L${x(points.length - 1).toFixed(1)},${padT + h} L${padL},${padT + h} Z`;
  const grid = [0, 0.25, 0.5, 0.75, 1].map(l => {
    const yy = padT + h - l * h;
    return `<line x1="${padL}" y1="${yy}" x2="${width - padR}" y2="${yy}" stroke="rgba(255,255,255,0.06)"/>
      <text x="${padL - 7}" y="${yy + 3}" fill="var(--faint)" font-size="8" text-anchor="end">${Math.round(l * 100)}%</text>`;
  }).join("");
  const dots = points.map((p, i) => `<circle cx="${x(i).toFixed(1)}" cy="${y(p.value).toFixed(1)}" r="2.5" fill="${color}" stroke="var(--panel-solid)" stroke-width="1.5"/>`).join("");
  const labels = points.map((p, i) => {
    if (points.length > 7 && i % Math.ceil(points.length / 6) !== 0 && i !== points.length - 1) return "";
    return `<text x="${x(i).toFixed(1)}" y="${height - 8}" fill="var(--faint)" font-size="8" text-anchor="middle">${escapeHTML(p.label || "")}</text>`;
  }).join("");
  const gid = uid("lg");
  return `<svg viewBox="0 0 ${width} ${height}" class="chart-svg" role="img" aria-label="趋势图">
    <defs><linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="${color}" stop-opacity=".28"/><stop offset="1" stop-color="${color}" stop-opacity="0"/>
    </linearGradient></defs>
    ${grid}
    <path d="${area}" fill="url(#${gid})"/>
    <path d="${path}" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
    ${dots}${labels}
  </svg>`;
}

export function barChartSVG(points, { width = 420, height = 160, color = "var(--ember)" } = {}) {
  if (!points || !points.length) return "";
  const padL = 8, padR = 8, padT = 14, padB = 26;
  const w = width - padL - padR, h = height - padT - padB;
  const max = Math.max(...points.map(p => Number(p.value) || 0), 1);
  const gap = 10;
  const bw = (w - gap * (points.length - 1)) / points.length;
  const bars = points.map((p, i) => {
    const v = Number(p.value) || 0;
    const bh = Math.max(3, (v / max) * h);
    const x = padL + i * (bw + gap);
    const y = padT + h - bh;
    return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${bh.toFixed(1)}" rx="4" fill="${color}" opacity="${v ? 0.85 : 0.18}"/>
      <text x="${(x + bw / 2).toFixed(1)}" y="${(y - 5).toFixed(1)}" fill="var(--muted)" font-size="8" text-anchor="middle">${v || ""}</text>
      <text x="${(x + bw / 2).toFixed(1)}" y="${height - 8}" fill="var(--faint)" font-size="8" text-anchor="middle">${escapeHTML(p.label || "")}</text>`;
  }).join("");
  return `<svg viewBox="0 0 ${width} ${height}" class="chart-svg" role="img" aria-label="柱状图">${bars}</svg>`;
}

export function heatmapHTML(days, max = 60) {
  if (!days || !days.length) return `<div class="heatmap"></div>`;
  const cells = days.map(d => {
    const m = Number(d.minutes) || 0;
    const level = m <= 0 ? 0 : m < max * 0.25 ? 1 : m < max * 0.5 ? 2 : m < max * 0.8 ? 3 : 4;
    return `<div class="heat-cell ${level ? "l" + level : ""}" title="${escapeAttr(d.date)} · ${m} 分钟"></div>`;
  }).join("");
  return `<div class="heatmap">${cells}</div>
    <div class="heat-legend"><span>少</span><i></i><i class="l1"></i><i class="l2"></i><i class="l3"></i><i class="l4"></i><span>多</span></div>`;
}

/* ---------- toast / modal ---------- */
export function toast(message, type = "info", timeout = 3600) {
  const root = document.getElementById("toasts");
  if (!root) return;
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  const iconName = type === "success" ? "check" : type === "error" ? "alert" : "spark";
  el.innerHTML = `<span class="toast-icon">${icon(iconName, 15)}</span><div>${escapeHTML(message)}</div>`;
  root.appendChild(el);
  const remove = () => {
    el.style.opacity = "0";
    el.style.transform = "translateY(8px)";
    setTimeout(() => el.remove(), 200);
  };
  el.addEventListener("click", remove);
  setTimeout(remove, timeout);
}

export function openModal({ title = "", subtitle = "", body = "", actions = [], wide = false, closable = true, onOpen = null } = {}) {
  const root = document.getElementById("modal-root");
  const opener = document.activeElement;
  const backdrop = document.createElement("div");
  backdrop.className = "modal-backdrop";
  const modal = document.createElement("div");
  modal.className = `modal${wide ? " wide" : ""}`;
  modal.setAttribute("role", "dialog");
  modal.setAttribute("aria-modal", "true");
  modal.innerHTML = `
    <div class="modal-head">
      <div><h3>${escapeHTML(title)}</h3>${subtitle ? `<p>${escapeHTML(subtitle)}</p>` : ""}</div>
      ${closable ? `<button class="icon-btn small" data-close aria-label="关闭">${icon("close", 15)}</button>` : ""}
    </div>
    <div class="modal-body"></div>
    <div class="modal-foot"></div>`;
  const bodyEl = modal.querySelector(".modal-body");
  if (body instanceof Node) bodyEl.appendChild(body);
  else bodyEl.innerHTML = body;
  const foot = modal.querySelector(".modal-foot");
  const FOCUSABLE = "a[href],button:not([disabled]),input:not([type=hidden]):not([disabled]),"
    + "select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex='-1'])";
  const items = () => [...modal.querySelectorAll(FOCUSABLE)]
    .filter((el) => el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const close = () => {
    if (backdrop.dataset.closing) return;
    backdrop.dataset.closing = "1";
    backdrop.style.opacity = "0";
    modal.style.transform = "translateY(8px) scale(.99)";
    setTimeout(() => backdrop.remove(), 160);
    document.removeEventListener("keydown", onKey);
    if (opener && opener.isConnected) opener.focus();   // 键盘用户不该被丢回页面顶端
  };
  const onKey = (e) => {
    if (e.key === "Escape" && closable) { close(); return; }
    if (e.key !== "Tab") return;
    // 焦点陷阱：弹窗开着时 Tab 不该跑到背景内容上 —— 背景还在页面上，读屏和键盘用户
    // 会掉进一个看不见也出不来的地方。
    const stops = items();
    if (!stops.length) { e.preventDefault(); modal.focus(); return; }
    const first = stops[0];
    const last = stops[stops.length - 1];
    const active = document.activeElement;
    if (!modal.contains(active)) { e.preventDefault(); first.focus(); return; }
    if (e.shiftKey && active === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && active === last) { e.preventDefault(); first.focus(); }
  };
  actions.forEach((a) => {
    const btn = document.createElement("button");
    btn.className = `btn ${a.kind || "btn-ghost"}`;
    btn.innerHTML = a.icon ? `${icon(a.icon, 15)} ${escapeHTML(a.label)}` : escapeHTML(a.label);
    btn.disabled = !!a.disabled;
    btn.addEventListener("click", async () => {
      if (a.onClick) {
        const keep = await a.onClick({ close, modal, body: bodyEl });
        if (keep === false) return;
      }
      if (a.close !== false) close();
    });
    foot.appendChild(btn);
  });
  if (!actions.length) foot.remove();
  backdrop.appendChild(modal);
  backdrop.addEventListener("click", (e) => {
    if (e.target === backdrop && closable) close();
  });
  modal.querySelector("[data-close]")?.addEventListener("click", close);
  document.addEventListener("keydown", onKey);
  root.appendChild(backdrop);
  modal.tabIndex = -1;
  // 先把焦点放进弹窗，再把 onOpen 的机会留给它自己（搜索框就是这么抢到焦点的）
  const stops = items();
  if (stops.length && !modal.contains(document.activeElement)) stops[0].focus();
  setTimeout(() => onOpen?.({ close, modal, body: bodyEl }), 0);
  return { close, modal, body: bodyEl, root: backdrop };
}

/** 浮层（划词菜单、灵感气泡、划词面板、模态框）打开时，页面级快捷键要让路。 */
export function overlayOpen() {
  if (document.querySelector(".modal-backdrop")) return true;
  if (document.querySelector(".agent-panel:not([hidden])")) return true;   // 助手面板开着时别抢键盘
  return [...document.querySelectorAll(".selection-menu, .note-popover, .sel-panel")]
    .some(x => x.style.display !== "none");
}

export function confirmDialog({ title = "确认", message = "", confirmLabel = "确认", danger = false } = {}) {
  return new Promise((resolve) => {
    let settled = false;
    const m = openModal({
      title,
      body: `<div class="callout ${danger ? "ember" : ""}">${escapeHTML(message)}</div>`,
      actions: [
        { label: "取消", kind: "btn-ghost", onClick: () => { settled = true; resolve(false); } },
        { label: confirmLabel, kind: danger ? "btn-danger" : "btn-primary", onClick: () => { settled = true; resolve(true); } },
      ],
      onOpen: ({ close }) => {
        const orig = close;
        // 用户点背景/ESC 也要 resolve
        const observer = new MutationObserver(() => {
          if (!document.body.contains(m.root) && !settled) { settled = true; resolve(false); observer.disconnect(); }
        });
        observer.observe(document.body, { childList: true });
      },
    });
  });
}

export function debounce(fn, ms = 220) {
  let t = null;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

export function autoResize(el) {
  if (!el) return;
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 130) + "px";
}

export function scrollToTop() {
  window.scrollTo({ top: 0, behavior: "smooth" });
}

/* AI 可用性三态：未配置 = 入口不渲染；可用 = 渲染；连续失败 = 熔断隐藏。
   核心学习流程（复习、苏格拉底、费曼、洞察）永远不依赖这里。 */
const AI_FAIL_LIMIT = 2;

export function aiMode(ctx) {
  const s = ctx?.state || {};
  return s.dashboard?.llm?.mode || s.me?.llm?.mode || "demo";
}

export function aiBroken(ctx) {
  return ((ctx?.state?.aiFails) || 0) >= AI_FAIL_LIMIT;
}

export function aiEnabled(ctx) {
  return aiMode(ctx) === "live" && !aiBroken(ctx);
}

export function aiSuccess(ctx) {
  if (ctx?.state) ctx.state.aiFails = 0;
}

export function aiFailure(ctx) {
  if (!ctx?.state) return;
  ctx.state.aiFails = (ctx.state.aiFails || 0) + 1;
  if (ctx.state.aiFails === AI_FAIL_LIMIT) {
    toast("AI 暂时不可用，已临时隐藏 AI 入口；烧水的主流程不受影响。", "info", 5200);
  }
}
