/* 版本与更新内容：侧边栏徽标 + 「这一版改了什么」浮层，数据来自 CHANGELOG.md。 */
import { escapeHTML, icon, openModal } from "./utils.js";

const SEEN_KEY = "boil_seen_release";
const GROUP_ORDER = ["新增", "改进", "修复", "已知边界"];

function groupBadge(title) {
  const t = String(title || "");
  if (t.includes("修复")) return "badge-teal";
  if (t.includes("已知") || t.includes("边界")) return "badge-gold";
  if (GROUP_ORDER.includes(t)) return "badge-ember";
  return "badge-muted";
}

function groupsOf(entry) {
  if (entry.groups && entry.groups.length) return entry.groups;
  return entry.items.length ? [{ title: "更新", items: entry.items }] : [];
}

export function isUnseen(info) {
  if (!info || !info.version) return false;
  if (!(info.releases || []).some(r => !r.unreleased && r.version === info.version)) return false;
  return localStorage.getItem(SEEN_KEY) !== info.version;
}

export function markSeen(info) {
  if (info && info.version) {
    try { localStorage.setItem(SEEN_KEY, info.version); } catch { /* 隐私模式下写不进去，不影响功能 */ }
  }
}

export function versionLabel(info, me) {
  const v = (info && info.version) || (me && me.version) || "";
  return v ? `v${v}` : "版本未知";
}

function buildLine(info) {
  const bits = [];
  if (info.build) bits.push(`构建 ${info.build}`);
  if (info.ref) bits.push(info.ref.replace(/^refs\/(heads|tags)\//, ""));
  bits.push(`数据格式 v${info.schema_version || "?"}`);
  bits.push(info.source_checkout ? "源码检出" : "安装副本（无 git 信息）");
  return bits.join(" · ");
}

function entryHTML(entry, isCurrent) {
  const groups = groupsOf(entry);
  const counts = groups.map(g => `${g.title} ${g.items.length}`).join(" · ");
  const head = `
    <summary class="rel-sum-row">
      <span class="rel-ver${isCurrent ? " current" : ""}">${entry.unreleased ? "未发布" : escapeHTML(entry.version)}</span>
      ${isCurrent ? `<span class="badge badge-teal">当前版本</span>` : ""}
      <span class="rel-date">${escapeHTML(entry.date || "")}</span>
      <span class="rel-count">${escapeHTML(counts)}</span>
      <span class="rel-caret">${icon("chevron", 14)}</span>
    </summary>`;
  const body = `
    ${entry.summary ? `<p class="rel-summary">${escapeHTML(entry.summary)}</p>` : ""}
    <div class="rel-groups">
      ${groups.map(g => `
        <div class="rel-group">
          <span class="badge ${groupBadge(g.title)}">${escapeHTML(g.title)}</span>
          <ul class="rel-list">
            ${g.items.map(it => `<li>${escapeHTML(it)}</li>`).join("")}
          </ul>
        </div>`).join("")}
    </div>`;
  return `<details class="rel-entry${isCurrent ? " current" : ""}"${isCurrent || entry.unreleased ? " open" : ""}>${head}${body}</details>`;
}

export function releaseHTML(info) {
  const entries = (info && info.releases) || [];
  const real = entries.filter(e => !e.unreleased);
  const pending = (info && info.unreleased) || [];
  const issues = (info && info.changelog_issues) || [];
  if (!real.length && !pending.length) {
    return `<div class="callout"><strong>没有读到更新日志</strong><br>
      服务端应有一份 <code>${escapeHTML(info && info.changelog_source || "CHANGELOG.md")}</code>；
      当前版本是 v${escapeHTML(info && info.version || "?")}，但更新内容还没写进去。</div>`;
  }
  return `
    <div class="rel-meta">
      <div class="between">
        <span class="rel-now">v${escapeHTML(info.version || "")}</span>
        <span class="rel-date">${escapeHTML(info.released_at || "")} 发布</span>
      </div>
      <div class="rel-build">${escapeHTML(buildLine(info))}</div>
    </div>
    ${issues.length ? `<div class="callout gold rel-issues">${icon("alert", 13)} 更新日志有 ${issues.length} 处格式问题：${escapeHTML(issues[0])}</div>` : ""}
    ${pending.length ? `
      <div class="rel-pending">
        <div class="side-label">下一版正在攒（还没发布）</div>
        <ul class="rel-list mt-2">${pending.map(it => `<li>${escapeHTML(it)}</li>`).join("")}</ul>
      </div>` : ""}
    <div class="rel-timeline">
      ${real.map(e => entryHTML(e, e.version === info.version)).join("")}
    </div>
    <p class="faint tiny mt-3">更新内容来自仓库里的 <code>${escapeHTML(info.changelog_source || "CHANGELOG.md")}</code>，
      写给学习者看：讲清多了什么能力、修了什么问题，不复述代码改动。</p>`;
}

export function openReleaseModal(ctx) {
  const info = ctx.state.releases || {};
  markSeen(info);
  document.getElementById("release-dot")?.remove();
  openModal({
    title: "版本与更新内容",
    subtitle: `${info.app || "Boil"} · 你正在用的这一版`,
    body: `<div class="rel-scroll">${releaseHTML(info)}</div>`,
    wide: true,
    actions: [{ label: "关闭", kind: "btn-ghost" }],
  });
}

export async function loadReleases(api) {
  try {
    const info = await api.get("/api/releases", { timeout: 8000 });
    return info && info.version ? info : null;
  } catch {
    return null;  // 版本信息读不到不该挡住页面启动
  }
}
