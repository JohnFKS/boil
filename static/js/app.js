/* Boil SPA: shell, router, global state. */
import * as api from "./api.js";
import {
  escapeHTML, escapeAttr, icon, qs, qsa, toast, openModal, renderMath,
  debounce, formatDate, daysUntil, pct, truncate, statusMeta, uid, overlayOpen,
} from "./utils.js";
import * as onboardingView from "./views/onboarding.js";
import * as dashboardView from "./views/dashboard.js";
import * as mapView from "./views/map.js";
import * as unitView from "./views/unit.js";
import * as socraticView from "./views/socratic.js";
import * as feynmanView from "./views/feynman.js";
import * as reviewView from "./views/review.js";
import * as libraryView from "./views/library.js";
import * as insightsView from "./views/insights.js";
import * as notesView from "./views/notes.js";
import * as settingsView from "./views/settings.js";
import { initSelectionMenu } from "./selectionMenu.js";
import { initFocus } from "./focus.js";
import { isUnseen, loadReleases, openReleaseModal, versionLabel } from "./releases.js";

const THEME_KEY = "boil_theme";
const state = {
  me: null,
  dashboard: null,
  catalog: null,
  theme: localStorage.getItem(THEME_KEY) || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"),
  route: "dashboard",
  params: {},
  loading: true,
  aiFails: 0,
  releases: null,
};

let cleanupFn = null;
let eventAbort = null;

const PAGE_META = {
  dashboard: { eyebrow: "TODAY", title: "今日" },
  map: { eyebrow: "LEARNING GRAPH", title: "知识地图" },
  unit: { eyebrow: "KNOWLEDGE UNIT", title: "知识单元" },
  socratic: { eyebrow: "SOCRATIC DIALOGUE", title: "苏格拉底对话" },
  feynman: { eyebrow: "FEYNMAN CHALLENGE", title: "费曼挑战" },
  review: { eyebrow: "SPACED REPETITION", title: "复习" },
  library: { eyebrow: "LEARNING PACKS", title: "内容库" },
  insights: { eyebrow: "LEARNING EVIDENCE", title: "洞察" },
  notes: { eyebrow: "INSPIRATION", title: "灵感库" },
  settings: { eyebrow: "SYSTEM", title: "设置" },
};

function applyTheme(theme) {
  state.theme = theme;
  document.documentElement.dataset.theme = theme;
  localStorage.setItem(THEME_KEY, theme);
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", theme === "light" ? "#f2ede3" : "#080b10");
}

function parseHash() {
  const raw = (location.hash || "#/dashboard").replace(/^#\/?/, "");
  const [pathPart, queryPart] = raw.split("?");
  const parts = pathPart.split("/").filter(Boolean);
  const name = parts[0] || "dashboard";
  const params = { query: new URLSearchParams(queryPart || "") };
  if (name === "unit" || name === "feynman") params.unitId = parts[1] || "";
  if (name === "socratic") params.sessionId = parts[1] || "";
  return { name, params };
}

function navigate(hash, { replace = false } = {}) {
  const target = hash.startsWith("#") ? hash : `#/${hash.replace(/^\/?/, "")}`;
  if (replace) {
    history.replaceState(null, "", target);
    renderRoute();
  } else if (location.hash === target) {
    renderRoute();
  } else {
    location.hash = target;
  }
}

async function loadCore() {
  state.me = await api.get("/api/me", { timeout: 15000 });
  if (!state.releases) state.releases = await loadReleases(api);
  if (state.me?.has_goal || state.me?.goals?.length) {
    state.dashboard = await api.get("/api/dashboard", { timeout: 15000 });
  } else {
    state.dashboard = null;
  }
}

async function loadCatalog(force = false) {
  if (state.catalog && !force) return state.catalog;
  state.catalog = await api.get("/api/packs");
  return state.catalog;
}

async function refreshDashboard() {
  state.dashboard = await api.get("/api/dashboard");
  return state.dashboard;
}

async function refreshMe() {
  state.me = await api.get("/api/me");
  return state.me;
}

async function startSocratic(unitId, adviceId = null) {
  try {
    const payload = { unit_id: unitId };
    if (adviceId) payload.advice_id = adviceId;
    const res = await api.post("/api/socratic/start", payload);
    navigate(`#/socratic/${res.session.id}`);
  } catch (e) {
    toast(e.message || "无法开始对话", "error");
  }
}

function navItems() {
  const d = state.dashboard;
  const due = d?.counts?.due || 0;
  return [
    { key: "dashboard", icon: "today", label: "今日" },
    { key: "map", icon: "map", label: "地图" },
    { key: "review", icon: "review", label: "复习", badge: due || null },
    { key: "library", icon: "library", label: "内容库" },
    { key: "insights", icon: "insights", label: "洞察" },
    { key: "notes", icon: "spark", label: "灵感库" },
    { key: "settings", icon: "settings", label: "设置" },
  ];
}

function sidebarHTML() {
  const goal = state.dashboard?.goal;
  const counts = state.dashboard?.counts;
  const progress = state.dashboard?.plan?.progress;
  const percent = progress?.percent || 0;
  const name = state.me?.user?.name || "学习者";
  const mode = state.dashboard?.llm?.mode || state.me?.llm?.mode || "demo";
  return `
    <aside class="sidebar" id="sidebar">
      <a class="brand" href="#/dashboard" aria-label="Boil 首页">
        <span class="brand-mark">
          <span class="kettle">
            <span class="steam s1"></span><span class="steam s2"></span><span class="steam s3"></span>
            <span class="kettle-handle"></span><span class="kettle-spout"></span><span class="kettle-body"></span><span class="kettle-base"></span>
          </span>
        </span>
        <span class="brand-text"><strong>BOIL</strong><small>烧水 · 学习 OS</small></span>
      </a>
      ${goal ? `
        <button class="goal-card" data-goal-card>
          <div class="goal-top">
            <span class="goal-eyebrow">当前目标</span>
            <span class="goal-orb" style="--p:${percent}"><span>${Math.round(percent)}%</span></span>
          </div>
          <div class="goal-title">${escapeHTML(goal.title)}</div>
          <div class="goal-meta">
            <span>${counts?.boiled || 0}/${counts?.units || 0} 已烧开</span>
            <span>${goal.deadline ? `${daysUntil(goal.deadline)} 天后` : "自定节奏"}</span>
          </div>
        </button>` : `
        <button class="goal-card" data-new-goal>
          <div class="goal-top"><span class="goal-eyebrow">还没有目标</span><span class="goal-orb" style="--p:0"><span>0%</span></span></div>
          <div class="goal-title">烧开第一壶水</div>
          <div class="goal-meta"><span>选择一个内容包开始</span><span>→</span></div>
        </button>`}
      <nav class="nav" aria-label="主导航">
        <div class="nav-label">学习</div>
        ${navItems().map(item => `
          <a class="nav-item${state.route === item.key ? " active" : ""}" href="#/${item.key}" data-nav="${item.key}">
            <span class="nav-icon">${icon(item.icon, 17)}</span>
            <span>${item.label}</span>
            ${item.badge ? `<span class="nav-badge">${item.badge}</span>` : ""}
          </a>`).join("")}
      </nav>
      <div class="sidebar-foot">
        ${releaseChipHTML()}
        <div class="mode-pill ${mode === "live" ? "" : "demo"}">
          <span class="mode-dot"></span>
          <span>${mode === "live" ? "真实模型已连接" : "演示模式 · 本地引擎"}</span>
        </div>
        <div class="user-chip">
          <span class="avatar">${escapeHTML((name || "学").slice(0, 1))}</span>
          <span><span class="name">${escapeHTML(name)}</span><span class="sub">数据留在本机</span></span>
          <a class="icon-btn small" href="#/settings" aria-label="设置" style="margin-left:auto">${icon("settings", 14)}</a>
        </div>
      </div>
    </aside>
    <div class="sidebar-backdrop" id="sidebar-backdrop"></div>`;
}

function releaseChipHTML() {
  const info = state.releases;
  const unseen = isUnseen(info);
  return `
    <button class="release-chip" type="button" id="release-chip"
      title="${unseen ? "这一版更新了内容，点开看" : "查看版本与更新内容"}">
      ${icon("spark", 12)}
      <span>${escapeHTML(versionLabel(info, state.me))}</span>
      ${info && info.build ? `<span class="release-build">${escapeHTML(info.build)}</span>` : ""}
      ${unseen ? `<span class="release-dot" id="release-dot"></span>` : ""}
      <span class="release-hint">更新内容</span>
    </button>`;
}

function shellHTML() {
  const meta = PAGE_META[state.route] || { eyebrow: "BOIL", title: "学习" };
  return `
    <div class="app-shell">
      ${sidebarHTML()}
      <div class="main">
        <header class="topbar">
          <div class="topbar-left">
            <button class="icon-btn menu-btn" id="menu-btn" aria-label="打开菜单">${icon("menu", 18)}</button>
            <div>
              <div class="eyebrow">${meta.eyebrow}</div>
              <h1 class="page-title" id="page-title">${escapeHTML(meta.title)}</h1>
            </div>
          </div>
          <div class="topbar-right">
            <button class="btn btn-ghost btn-sm focus-btn" id="focus-btn" title="番茄钟：点击开始一次专注">${icon("clock", 14)}<span id="focus-label">专注</span></button>
            <button class="search-btn" id="search-btn" aria-label="搜索">${icon("search", 16)}<span>搜索单元或内容包</span><span class="kbd">⌘K</span></button>
            <button class="icon-btn" id="theme-btn" aria-label="切换主题">${icon(state.theme === "light" ? "moon" : "sun", 17)}</button>
            <a class="btn btn-primary btn-sm" href="#/onboarding" data-new-goal>${icon("plus", 14)} 新目标</a>
          </div>
        </header>
        <main class="view" id="view"></main>
      </div>
    </div>
    <nav class="mobile-nav" aria-label="移动导航">
      ${navItems().slice(0, 6).map(item => `
        <a href="#/${item.key}" class="${state.route === item.key ? "active" : ""}">
          <span class="nav-icon">${icon(item.icon, 17)}</span><span>${item.label}</span>
        </a>`).join("")}
    </nav>`;
}

function bindShell() {
  qs("#menu-btn")?.addEventListener("click", () => {
    qs("#sidebar")?.classList.add("open");
    qs("#sidebar-backdrop")?.classList.add("open");
  });
  const closeMenu = () => {
    qs("#sidebar")?.classList.remove("open");
    qs("#sidebar-backdrop")?.classList.remove("open");
  };
  qs("#sidebar-backdrop")?.addEventListener("click", closeMenu);
  qsa("[data-nav]").forEach(a => a.addEventListener("click", closeMenu));
  qs("[data-goal-card]")?.addEventListener("click", () => navigate("#/map"));
  qsa("[data-new-goal]").forEach(el => el.addEventListener("click", (e) => {
    e.preventDefault();
    navigate("#/onboarding");
  }));
  qs("#theme-btn")?.addEventListener("click", () => {
    applyTheme(state.theme === "light" ? "dark" : "light");
    qs("#theme-btn").innerHTML = icon(state.theme === "light" ? "moon" : "sun", 17);
  });
  qs("#search-btn")?.addEventListener("click", openSearch);
  qs("#release-chip")?.addEventListener("click", () => openReleaseModal(ctx));
  document.addEventListener("keydown", globalKeyHandler);
}

function globalKeyHandler(e) {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
    e.preventDefault();
    openSearch();
    return;
  }
  // 「/」= 搜索：输入框里打字、或已经有浮层时不能抢键
  if (e.key === "/" && !e.metaKey && !e.ctrlKey && !e.altKey) {
    const tag = (e.target?.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select" || e.target?.isContentEditable) return;
    if (overlayOpen()) return;
    e.preventDefault();
    openSearch();
  }
}

function detachGlobal() {
  document.removeEventListener("keydown", globalKeyHandler);
  if (cleanupFn) {
    try { cleanupFn(); } catch (e) { console.warn(e); }
    cleanupFn = null;
  }
  if (eventAbort) {
    eventAbort.abort();
    eventAbort = null;
  }
}

async function renderRoute() {
  const { name, params } = parseHash();
  state.route = name;
  state.params = params;
  detachGlobal();
  api.abortStream?.();
  const app = document.getElementById("app");
  if (!app) return;

  if (name === "onboarding") {
    document.title = "开始 · Boil";
    app.innerHTML = `<div id="view"></div>`;
    const view = document.getElementById("view");
    try {
      await onboardingView.render(view, ctx, params);
      renderMath(view);
    } catch (e) {
      console.error(e);
      view.innerHTML = errorHTML(e);
    }
    return;
  }

  if (!state.dashboard && (!state.me?.goals?.length)) {
    navigate("#/onboarding", { replace: true });
    return;
  }

  app.innerHTML = shellHTML();
  bindShell();
  const view = document.getElementById("view");
  const module = {
    dashboard: dashboardView, map: mapView, unit: unitView, socratic: socraticView,
    feynman: feynmanView, review: reviewView, library: libraryView,
    insights: insightsView, notes: notesView, settings: settingsView,
  }[name];
  if (!module) {
    view.innerHTML = `<div class="empty"><div class="empty-icon">${icon("compass", 22)}</div><h3>找不到这个页面</h3><p>回到今日，继续烧水。</p><a class="btn btn-primary mt-3" href="#/dashboard">回到今日</a></div>`;
    return;
  }
  try {
    await module.render(view, ctx, params);
    renderMath(view);
  } catch (e) {
    console.error(e);
    view.innerHTML = errorHTML(e);
    view.querySelector("[data-reload]")?.addEventListener("click", () => location.reload());
  }
  window.scrollTo({ top: 0, behavior: "instant" });
}

function errorHTML(e) {
  const message = e?.message || "页面加载失败";
  return `<div class="empty" style="padding-top:12vh">
    <div class="empty-icon">${icon("alert", 22)}</div>
    <h3>出了点问题</h3>
    <p>${escapeHTML(message)}</p>
    <div class="row" style="justify-content:center;margin-top:16px">
      <button class="btn btn-primary" data-reload>重新加载</button>
      <a class="btn btn-ghost" href="#/dashboard">回到今日</a>
    </div>
  </div>`;
}

function setPageTitle(eyebrow, title) {
  const e = qs(".eyebrow");
  const t = qs("#page-title");
  if (e && eyebrow) e.textContent = eyebrow;
  if (t && title) {
    t.textContent = title;
    document.title = `${title} · Boil`;
  }
}

async function openSearch() {
  const wrap = document.createElement("div");
  wrap.innerHTML = `
    <div class="search-box">${icon("search", 18)}<input id="global-search" placeholder="搜索知识单元、内容包…" autocomplete="off"><span class="kbd">ESC</span></div>
    <div class="search-results" id="search-results">
      <div class="search-group-label">快捷入口</div>
      ${[
        { href: "#/dashboard", title: "今日闭环", sub: "查看今天的复习与下一步" },
        { href: "#/review", title: "开始复习", sub: "对抗遗忘，先还卡" },
        { href: "#/map", title: "知识地图", sub: "看整个目标的结构" },
        { href: "#/library", title: "内容库", sub: "换一个内容包或从材料创建" },
      ].map(x => `<button class="search-item" data-href="${x.href}"><span class="title">${x.title}</span><span class="sub">${x.sub}</span></button>`).join("")}
    </div>`;
  const modal = openModal({ title: "搜索", body: wrap, wide: false, closable: true });
  const input = wrap.querySelector("#global-search");
  const results = wrap.querySelector("#search-results");
  input.focus();
  modal.modal.querySelector(".modal-head h3").style.display = "none";
  const go = (href) => { modal.close(); navigate(href); };
  wrap.addEventListener("click", (e) => {
    const item = e.target.closest("[data-href]");
    if (item) go(item.dataset.href);
  });
  const search = debounce(async (q) => {
    if (!q.trim()) return;
    try {
      const data = await api.get(`/api/search?q=${encodeURIComponent(q)}`);
      const units = data.units || [];
      const packs = data.packs || [];
      results.innerHTML = `
        ${units.length ? `<div class="search-group-label">知识单元</div>${units.map(u => `
          <button class="search-item" data-href="#/unit/${escapeAttr(u.id)}">
            <span class="title">${escapeHTML(u.title)}</span>
            <span class="sub">${escapeHTML(truncate(u.essence || "", 60))}</span>
          </button>`).join("")}` : ""}
        ${packs.length ? `<div class="search-group-label">内容包</div>${packs.map(p => `
          <button class="search-item" data-href="#/library">
            <span class="title">${escapeHTML(p.name)}</span>
            <span class="sub">${p.unit_count} 个单元 · ${escapeHTML(p.subject || "")}</span>
          </button>`).join("")}` : ""}
        ${!units.length && !packs.length ? `<div class="empty" style="padding:30px"><p>没有找到「${escapeHTML(q)}」</p></div>` : ""}`;
    } catch (e) {
      results.innerHTML = `<div class="empty" style="padding:24px"><p>${escapeHTML(e.message)}</p></div>`;
    }
  }, 220);
  input.addEventListener("input", () => search(input.value));
}

const ctx = {
  api,
  state,
  icon,
  toast,
  openModal,
  escapeHTML,
  escapeAttr,
  renderMath,
  truncate,
  formatDate,
  pct,
  navigate,
  setPageTitle,
  loadCatalog,
  refreshDashboard,
  refreshMe,
  startSocratic,
  cleanup(fn) { cleanupFn = fn; },
  async reloadAll() {
    await loadCore();
    await loadCatalog(true);
    await renderRoute();
  },
  async refreshView() {
    await refreshDashboard();
    await renderRoute();
  },
  openSearch,
};

function bootErrorHTML(e) {
  return `<div class="boot" style="padding:24px">
    <div class="brand-mark"><span class="kettle"><span class="steam s1"></span><span class="steam s2"></span><span class="steam s3"></span><span class="kettle-handle"></span><span class="kettle-spout"></span><span class="kettle-body"></span><span class="kettle-base"></span></span></div>
    <h1 style="font-family:var(--font-display);font-size:30px">未能连接到 Boil</h1>
    <p style="max-width:460px">${escapeHTML(e?.message || "服务没有响应")}<br>请确认 <code>python3 run.py</code> 仍在运行，或检查访问令牌。</p>
    <div class="row" style="justify-content:center;margin-top:8px">
      <button class="btn btn-primary" id="boot-retry">重试</button>
      <button class="btn btn-ghost" id="boot-token">输入访问令牌</button>
    </div>
    <p class="faint tiny" style="margin-top:4px">数据默认保存在本机 data/；不会因为刷新丢失。</p>
  </div>`;
}

async function boot() {
  applyTheme(state.theme);
  const app = document.getElementById("app");
  const watchdog = setTimeout(() => {
    if (app && app.querySelector(".boot") && !state.me) {
      window.__boilBootReady?.();
      app.innerHTML = bootErrorHTML(new Error("启动超时（15 秒）"));
      bindBootError();
    }
  }, 15000);
  try {
    await loadCore();
  } catch (e) {
    clearTimeout(watchdog);
    window.__boilBootReady?.();
    if (e.status === 401) {
      askToken();
      return;
    }
    app.innerHTML = bootErrorHTML(e);
    bindBootError();
    return;
  }
  clearTimeout(watchdog);
  initSelectionMenu(ctx);
  initFocus(ctx);
  try {
    await renderRoute();
    window.__boilBootReady?.();
  } catch (e) {
    console.error(e);
    window.__boilBootReady?.();
    app.innerHTML = bootErrorHTML(e);
    bindBootError();
  }
}

function bindBootError() {
  document.getElementById("boot-retry")?.addEventListener("click", () => boot());
  document.getElementById("boot-token")?.addEventListener("click", askToken);
}

function askToken() {
  const body = document.createElement("div");
  body.innerHTML = `<div class="field"><label>访问令牌</label><input class="input" id="token-input" type="password" placeholder="BOIL_TOKEN" autocomplete="off"><span class="hint">服务端设置了 BOIL_TOKEN。输入后只保存在本机浏览器。</span></div>`;
  const modal = openModal({
    title: "需要访问令牌",
    subtitle: "这个 Boil 实例开启了远程访问保护。",
    body,
    closable: false,
    actions: [{
      label: "进入", kind: "btn-primary", close: false,
      onClick: async () => {
        const token = body.querySelector("#token-input").value.trim();
        if (!token) return false;
        api.setToken(token);
        modal.close();
        try {
          await loadCore();
          await renderRoute();
        } catch (e) {
          toast(e.message || "令牌无效", "error");
          askToken();
        }
        return false;
      },
    }],
  });
}

api.onUnauthorized(() => {
  api.setToken("");
  askToken();
});

window.addEventListener("hashchange", renderRoute);
boot();
