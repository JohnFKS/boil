/* Settings: model connection, learning contract, data ownership. */
import {
  escapeHTML, escapeAttr, icon, toast, openModal, confirmDialog, formatMinutes,
} from "../utils.js";
import { isUnseen, markSeen, openReleaseModal, releaseHTML, versionLabel } from "../releases.js";

function providerChips(providers) {
  return `<div class="provider-grid">${providers.map((p, i) => `<button class="provider-chip" data-provider="${i}">${escapeHTML(p.name)}</button>`).join("")}</div>`;
}

const CODE_LABELS = {
  dns: "域名解析失败", refused: "连接被拒绝", timeout: "超时", tls: "证书/TLS 失败",
  proxy: "代理异常", network: "网络不通", http_error: "网关返回错误状态",
  empty_content: "模型返回空正文", stream: "流式连接中断", json: "返回不是合法 JSON",
  junk_output: "模型只回了占位内容", not_configured: "没有配置模型",
};

function diagHTML(cfg) {
  const err = cfg.last_error;
  if (!err) {
    return `<div class="diag-ok">${icon("check", 13)} 最近一次模型调用没有留下错误记录。</div>`;
  }
  const rows = [
    ["分类", CODE_LABELS[err.code] || err.code || "未知"],
    ["时间", err.at || ""],
    ["地址", err.base_url || cfg.base_url || ""],
    ["模型", err.model || cfg.model || ""],
    ["Key", err.api_key_masked || (cfg.api_key_set ? cfg.api_key_masked : "未设置")],
  ].filter(r => r[1]);
  const detail = String(err.detail || "").slice(0, 1200);
  const plain = [`模型诊断`, ...rows.map(r => `${r[0]}: ${r[1]}`), `信息: ${err.message || ""}`,
                 `建议: ${err.hint || ""}`, `返回内容: ${detail}`].join("\n");
  return `<details class="diag-box" open>
    <summary><span class="diag-dot"></span>上次模型调用失败：${escapeHTML(err.message || "未知错误")}</summary>
    <div class="diag-body">
      <dl class="diag-grid">${rows.map(r => `<dt>${escapeHTML(r[0])}</dt><dd>${escapeHTML(r[1])}</dd>`).join("")}</dl>
      ${err.hint ? `<div class="callout ember"><strong>怎么办</strong><br>${escapeHTML(err.hint)}</div>` : ""}
      ${detail ? `<pre class="diag-detail">${escapeHTML(detail)}</pre>` : ""}
      <div class="row mt-2" style="gap:8px;flex-wrap:wrap">
        <button class="btn btn-ghost btn-sm" type="button" data-diag-copy>${icon("list", 13)} 复制诊断</button>
        <button class="btn btn-ghost btn-sm" type="button" data-diag-retest>${icon("zap", 13)} 立即重试连接</button>
      </div>
    </div>
    <textarea class="diag-plain" id="diag-plain" readonly>${escapeHTML(plain)}</textarea>
  </details>`;
}

export async function render(el, ctx, params) {
  const cfg = await ctx.api.get("/api/settings/llm");
  const emb = await ctx.api.get("/api/settings/embeddings");
  const goals = await ctx.api.get("/api/goals");
  const goal = goals.active_goal;
  const health = await ctx.api.get("/api/health");
  const mode = cfg.enabled ? "live" : "demo";
  if (!ctx.state.releases) {
    try {
      ctx.state.releases = await ctx.api.get("/api/releases", { timeout: 8000 });
    } catch {
      ctx.state.releases = null;  // 读不到更新日志不该让设置页打不开
    }
  }
  const info = ctx.state.releases;

  el.innerHTML = `
    <div class="page-head">
      <div>
        <h2>系统设置</h2>
        <p>模型、学习契约与数据都归你控制。默认本地优先，密钥只留在这台机器。</p>
      </div>
      <div class="row wrap">
        <span class="badge ${mode === "live" ? "badge-teal" : "badge-gold"}">${mode === "live" ? "真实模型已连接" : "演示模式"}</span>
        <button class="badge badge-muted release-head" type="button" id="release-head">${escapeHTML(versionLabel(info, health))} · 更新内容</button>
      </div>
    </div>
    <nav class="settings-jump" aria-label="设置分区">
      <span class="settings-jump-label">跳到</span>
      <button class="jump-chip active" data-jump="settings-llm">模型连接</button>
      <button class="jump-chip" data-jump="settings-embeddings">语义检索</button>
      <button class="jump-chip" data-jump="settings-goal">学习契约</button>
      <button class="jump-chip" data-jump="settings-data">数据与备份</button>
      <button class="jump-chip" data-jump="settings-releases">版本与更新</button>
    </nav>
    <div class="settings-grid">
      <div class="settings-main">
        <div class="card settings-card" id="settings-llm">
          <div class="between">
            <div><h3>模型连接</h3><div class="sub">任意 OpenAI 兼容端点。不配置也能用本地演示引擎完整跑通。</div></div>
            <span class="badge ${mode === "live" ? "badge-teal" : "badge-gold"}">${mode === "live" ? "LIVE" : "DEMO"}</span>
          </div>
          <div class="llm-status ${mode === "live" ? "live" : ""} mt-3">
            <span class="dot"></span>
            <span>${mode === "live" ? `正在使用 <strong>${escapeHTML(cfg.model)}</strong>` : "当前使用本地确定性引擎：追问、评分、编译都能跑，但不做语义判断。"}</span>
          </div>
          ${cfg.forced_off ? `<div class="error-box">服务端设置了 BOIL_LLM_OFF=1，所有调用会强制走演示模式。</div>` : ""}
          ${diagHTML(cfg)}
          <div class="mt-4">
            <div class="side-label">快速填充</div>
            <div class="mt-2">${providerChips(cfg.providers || [])}</div>
          </div>
          <div class="form-grid mt-4">
            <div class="field full"><label for="llm-base">Base URL</label><input class="input" id="llm-base" value="${escapeAttr(cfg.base_url || "")}" placeholder="https://api.openai.com/v1"></div>
            <div class="field full"><label for="llm-key">API Key</label><input class="input" id="llm-key" type="password" autocomplete="off" placeholder="${cfg.api_key_set ? `已保存 ${escapeHTML(cfg.api_key_masked)} · 留空保持不变` : "sk-..."}"><span class="hint">密钥写入 data/llm.json（0600），不会返回给浏览器，也不会进入导出包。</span></div>
            <div class="field"><label for="llm-model">模型</label><input class="input" id="llm-model" value="${escapeAttr(cfg.model || "")}" placeholder="gpt-4o-mini"></div>
            <div class="field"><label for="llm-deep">深度模型（苏格拉底/费曼）</label><input class="input" id="llm-deep" value="${escapeAttr(cfg.deep_model || "")}" placeholder="留空则与模型相同"></div>
            <div class="field"><label for="llm-auth-header">认证头名称</label><input class="input" id="llm-auth-header" value="${escapeAttr(cfg.auth_header || "Authorization")}" placeholder="Authorization"><span class="hint">Dots 使用 <code>api-key</code>。</span></div>
            <div class="field"><label for="llm-auth-prefix">认证前缀</label><input class="input" id="llm-auth-prefix" value="${escapeAttr(cfg.auth_prefix ?? "Bearer ")}" placeholder="Bearer "><span class="hint">OpenAI 为 <code>Bearer </code>（含空格）；Dots 留空。</span></div>
            <div class="field"><label for="llm-temp">温度 <span id="temp-label">${cfg.temperature}</span></label><input class="slider" id="llm-temp" type="range" min="0" max="1.5" step="0.1" value="${cfg.temperature}"></div>
            <div class="field"><label for="llm-tokens">最大输出 tokens（可选）</label><input class="input" id="llm-tokens" type="number" min="64" max="32000" value="${cfg.max_tokens || ""}" placeholder="留空由服务端决定"></div>
            <div class="field"><label for="llm-timeout">超时（秒）</label><input class="input" id="llm-timeout" type="number" min="10" max="600" value="${cfg.timeout || 120}"></div>
            <div class="field full"><label for="llm-extra">高级请求参数 JSON（可选）</label><textarea class="textarea" id="llm-extra" placeholder='{}'>${escapeHTML(JSON.stringify(cfg.extra_body || {}, null, 2))}</textarea><span class="hint">会合并进请求体。Dots 可设置 <code>{"chat_template_kwargs":{"enable_thinking":true}}</code> 开启深度思考；默认预设关闭以降低延迟。</span></div>
          </div>
          <div class="field full mt-3 cache-row">
            <label class="cache-toggle">
              <input type="checkbox" id="llm-cache-enabled" ${cfg.cache_enabled !== false ? "checked" : ""}>
              <span>结构化调用缓存</span>
            </label>
            <span class="hint">编译、大纲、速记、洞察解读这类结构化调用会按「模型 + 提示词 + 参数」做内容寻址缓存：同样材料重跑不再花 token，结果也不会每次漂移。流式对话不缓存。
              当前命中 ${cfg.cache?.hits || 0} 次 · 真实调用 ${cfg.cache?.calls || 0} 次 · 缓存 ${cfg.cache_entries || 0} 条。
              <button class="link-btn" type="button" id="clear-cache">清空缓存</button></span>
          </div>
          <div class="row mt-4" style="flex-wrap:wrap">
            <button class="btn btn-primary" id="save-llm">${icon("check", 15)} 保存连接</button>
            <button class="btn btn-ghost" id="test-llm">${icon("zap", 15)} 测试连接</button>
            <button class="btn btn-ghost" id="clear-llm">回到演示模式</button>
            <span class="faint tiny">测试会发送一个极短的 ping；成功后状态会自动刷新。</span>
          </div>
          <div id="llm-result"></div>
        </div>

        <div class="card settings-card" id="settings-embeddings">
          <div class="between">
            <div><h3>语义检索（Embedding）</h3><div class="sub">有标题的 Markdown 仍按结构解析；无标题材料、语义搜索与相关单元使用向量。</div></div>
            <span class="badge ${emb.mode === "cloud" ? "badge-teal" : emb.mode === "fallback" ? "badge-ember" : "badge-gold"}">${emb.mode === "cloud" ? "云端" : emb.mode === "fallback" ? "降级本地" : "本地"}</span>
          </div>
          <div class="llm-status ${emb.mode === "cloud" ? "live" : ""} mt-3">
            <span class="dot"></span>
            <span>${emb.configured ? `配置：<strong>${escapeHTML(emb.model)}</strong>${emb.dim ? ` · ${emb.dim} 维` : ""}${emb.mode === "fallback" ? " · 云端失败，已降级本地哈希向量" : ""}` : "未配置云端 Embedding，使用本地哈希向量（离线、零依赖）。"}</span>
          </div>
          ${emb.last_error ? `<div class="error-box"><strong>上次 Embedding 调用失败：</strong>${escapeHTML(emb.last_error)}</div>` : ""}
          <div class="callout teal mt-3" style="font-size:11px">推荐：硅基流动 <code>BAAI/bge-m3</code>（1024 维，中文效果好）。不配置也能用；失败会自动回退本地，并在上方显示原因。</div>
          <div class="form-grid mt-3">
            <div class="field full"><label for="emb-base">Embedding Base URL</label><input class="input" id="emb-base" value="${escapeAttr(emb.base_url || "")}" placeholder="https://api.siliconflow.cn/v1"></div>
            <div class="field"><label for="emb-model">Embedding 模型</label><input class="input" id="emb-model" value="${escapeAttr(emb.model || "")}" placeholder="BAAI/bge-m3"></div>
            <div class="field"><label for="emb-key">Embedding API Key</label><input class="input" id="emb-key" type="password" autocomplete="off" placeholder="${emb.api_key_set ? `已保存 ${escapeHTML(emb.api_key_masked)} · 留空保持不变` : "sk-..."}"><span class="hint">单独保存在 data/embedding.json（0600），不会进入前端/导出/仓库。</span></div>
            <div class="field"><label for="emb-timeout">超时（秒）</label><input class="input" id="emb-timeout" type="number" min="5" max="120" value="${emb.timeout || 30}"></div>
          </div>
          <div class="row mt-3" style="flex-wrap:wrap">
            <button class="btn btn-primary" id="save-emb">${icon("check", 15)} 保存 Embedding</button>
            <button class="btn btn-ghost" id="test-emb">${icon("zap", 15)} 测一次语义检索</button>
            <button class="btn btn-ghost" id="clear-emb">回到本地向量</button>
          </div>
          <div id="emb-result"></div>
          <div class="faint tiny mt-2">${escapeHTML(emb.suggest?.note || "")}</div>
        </div>

        <div class="card settings-card" id="settings-goal">
          <div class="between"><div><h3>学习契约</h3><div class="sub">修改每天可投入的时间或截止日期，系统会重排剩余路径。</div></div><span class="badge badge-ember">计划是活的</span></div>
          ${goal ? `<div class="form-grid mt-4">
            <div class="field full"><label for="goal-title">当前目标</label><input class="input" id="goal-title" value="${escapeAttr(goal.title)}"></div>
            <div class="field"><label for="goal-minutes">每天可投入 <strong id="goal-minutes-label">${goal.daily_minutes} 分钟</strong></label><input class="slider" id="goal-minutes" type="range" min="15" max="120" step="5" value="${goal.daily_minutes}"></div>
            <div class="field"><label for="goal-deadline">截止日期</label><input class="input" id="goal-deadline" type="date" value="${escapeAttr(goal.deadline || "")}"></div>
            <div class="field full"><span class="hint">重排只针对尚未烧开的单元；已烧开的单元不会回到新学队列，但仍会按 FSRS 安排复习。</span></div>
          </div>
          <div class="row mt-3">
            <button class="btn btn-teal" id="rebuild-plan">${icon("refresh", 15)} 保存并重排</button>
            <a class="btn btn-ghost" href="#/map">先看当前路径</a>
          </div>
          <div id="plan-result"></div>` : `<div class="callout mt-3">还没有学习目标。<a href="#/onboarding" style="color:var(--ember)">创建一个</a></div>`}
        </div>

        <div class="card settings-card danger-zone" id="settings-data">
          <h3>数据与备份</h3>
          <div class="sub">导出完整学习数据（JSON / Anki），或清空本机演示数据。</div>
          <div class="row mt-3" style="flex-wrap:wrap">
            <button class="btn btn-ghost" id="export-data">${icon("download", 15)} 导出全部数据</button>
            <button class="btn btn-ghost" id="export-anki">${icon("review", 15)} 导出 Anki 卡片</button>
            <button class="btn btn-ghost" id="import-data">${icon("upload", 15)} 导入备份</button>
            <button class="btn btn-danger" id="reset-data">${icon("trash", 15)} 清空并重置</button>
            <input type="file" id="import-file" accept=".json,application/json" hidden>
          </div>
          <div class="faint tiny mt-2">JSON 导出包含目标、单元、卡片、复习日志、对话、解释、火候记录与速记，不包含 API Key。导入支持合并或替换；替换不可撤销。</div>
          <div class="faint tiny mt-2">Anki 导出为制表符分隔文本：Anki → 文件 → 导入 → 选择 boil-anki.txt，字段含正面、背面与标签。</div>
        </div>

        <div class="card settings-card" id="settings-releases">
          <h3>版本与更新</h3>
          <div class="sub">这一版到底改了什么，写在仓库的 CHANGELOG.md 里；网页直接读那份文件，只讲多了什么能力和修了什么问题。</div>
          ${info ? releaseHTML(info) : `<div class="callout gold mt-3">服务端没读到更新日志，只能确认版本是 ${escapeHTML(versionLabel(null, health))}。</div>`}
        </div>
      </div>

      <aside class="settings-side">
        <div class="card settings-card">
          <div class="side-label">这条数据链</div>
          <div class="rule-list">
            <div class="rule"><span class="n">1</span><span>你的学习数据默认留在本机 data/boil.db。</span></div>
            <div class="rule"><span class="n">2</span><span>只有配置了模型端点，文本才会发往该端点。</span></div>
            <div class="rule"><span class="n">3</span><span>不训练你的数据，不出售数据，不用你的内容做营销。</span></div>
          </div>
          <div class="divider"></div>
          <div class="facet-row"><span class="muted">模式</span><span>${mode === "live" ? "真实模型" : "本地演示"}</span><span></span></div>
          <div class="facet-row"><span class="muted">数据目录</span><span class="mono" style="font-size:10px">${escapeHTML(health.data_dir || "")}</span><span></span></div>
          <div class="facet-row"><span class="muted">版本</span><span>${escapeHTML(versionLabel(info, health))}${info && info.build ? ` · ${escapeHTML(info.build)}` : ""}</span><span></span></div>
        </div>
        <div class="card settings-card">
          <div class="side-label">快捷键</div>
          <div class="rule-list">
            <div class="rule shortcut"><span class="n">⌘K</span><span>全局搜索</span></div>
            <div class="rule shortcut"><span class="n">空格</span><span>复习卡翻面</span></div>
            <div class="rule shortcut"><span class="n">1–4</span><span>复习评分</span></div>
            <div class="rule shortcut"><span class="n">Enter</span><span>发送苏格拉底回答</span></div>
          </div>
        </div>
        <div class="card settings-card">
          <div class="side-label">关于 Boil</div>
          <div class="about-line"><span>定位</span><strong>开源 AI 学习 OS</strong></div>
          <div class="about-line"><span>后端</span><strong>Python 标准库</strong></div>
          <div class="about-line"><span>前端</span><strong>自研 SPA</strong></div>
          <div class="about-line"><span>调度</span><strong>FSRS-5</strong></div>
          <div class="about-line"><span>许可证</span><strong>Apache-2.0</strong></div>
          <p class="faint tiny mt-2">把知识烧开，而不是看过。AI 负责准备，你负责烧开。</p>
        </div>
      </aside>
    </div>`;

  const temp = el.querySelector("#llm-temp");
  const tempLabel = el.querySelector("#temp-label");
  temp?.addEventListener("input", () => { tempLabel.textContent = temp.value; });

  const jumpButtons = [...el.querySelectorAll("[data-jump]")];
  const jumpTargets = jumpButtons
    .map(btn => document.getElementById(btn.dataset.jump))
    .filter(Boolean);
  if (el._settingsCleanup) el._settingsCleanup();
  let pinnedJump = null;
  let pinnedUntil = 0;
  const updateJump = () => {
    if (!el.isConnected) {
      window.removeEventListener("scroll", updateJump);
      return;
    }
    // 点了「数据与备份」就该亮着它：平滑滚动途中滚动侦测会把它抢成最后一节
    if (pinnedJump && Date.now() < pinnedUntil) return;
    const atBottom = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 12;
    let current = jumpTargets[0];
    if (atBottom) {
      current = jumpTargets[jumpTargets.length - 1];
    } else {
      for (const target of jumpTargets) {
        if (target.getBoundingClientRect().top <= 160) current = target;
      }
    }
    if (!current) return;
    jumpButtons.forEach(b => b.classList.toggle("active", b.dataset.jump === current.id));
  };
  jumpButtons.forEach(btn => btn.addEventListener("click", () => {
    document.getElementById(btn.dataset.jump)?.scrollIntoView({ behavior: "smooth", block: "start" });
    jumpButtons.forEach(b => b.classList.toggle("active", b === btn));
    pinnedJump = btn;
    pinnedUntil = Date.now() + 1600;
  }));
  window.addEventListener("scroll", updateJump, { passive: true });
  el._settingsCleanup = () => window.removeEventListener("scroll", updateJump);
  updateJump();

  el.querySelectorAll("[data-provider]").forEach(btn => btn.addEventListener("click", () => {
    const p = (cfg.providers || [])[Number(btn.dataset.provider)];
    if (!p) return;
    if (p.base_url) el.querySelector("#llm-base").value = p.base_url;
    if (p.model) el.querySelector("#llm-model").value = p.model;
    el.querySelector("#llm-deep").value = p.deep_model || p.model || "";
    el.querySelector("#llm-auth-header").value = p.auth_header || "Authorization";
    el.querySelector("#llm-auth-prefix").value = p.auth_prefix !== undefined ? p.auth_prefix : "Bearer ";
    el.querySelector("#llm-extra").value = JSON.stringify(p.extra_body || {}, null, 2);
    el.querySelectorAll("[data-provider]").forEach(x => x.classList.toggle("active", x === btn));
    toast(`已填入 ${p.name} 的默认端点，保存后生效。`, "info", 3000);
  }));

  function collectLLM() {
    const extraText = el.querySelector("#llm-extra").value.trim();
    let extraBody = {};
    if (extraText) {
      try {
        extraBody = JSON.parse(extraText);
      } catch {
        throw new Error("高级请求参数必须是合法 JSON");
      }
      if (!extraBody || Array.isArray(extraBody) || typeof extraBody !== "object") {
        throw new Error("高级请求参数必须是 JSON 对象");
      }
    }
    const payload = {
      base_url: el.querySelector("#llm-base").value.trim(),
      model: el.querySelector("#llm-model").value.trim(),
      cache_enabled: !!el.querySelector("#llm-cache-enabled")?.checked,
      deep_model: el.querySelector("#llm-deep").value.trim(),
      auth_header: el.querySelector("#llm-auth-header").value.trim() || "Authorization",
      auth_prefix: el.querySelector("#llm-auth-prefix").value,
      extra_body: extraBody,
      temperature: Number(el.querySelector("#llm-temp").value),
      max_tokens: el.querySelector("#llm-tokens").value ? Number(el.querySelector("#llm-tokens").value) : null,
      timeout: Number(el.querySelector("#llm-timeout").value) || 120,
    };
    const key = el.querySelector("#llm-key").value.trim();
    if (key) payload.api_key = key;
    return payload;
  }

  el.querySelector("#save-llm")?.addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true; btn.innerHTML = "保存中…";
    try {
      await ctx.api.post("/api/settings/llm", collectLLM());
      toast("连接配置已保存。", "success");
      await ctx.refreshView();
    } catch (err) {
      toast(err.message || "保存失败", "error");
    } finally {
      btn.disabled = false; btn.innerHTML = `${icon("check", 15)} 保存连接`;
    }
  });
  el.querySelector("#clear-cache")?.addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      const res = await ctx.api.post("/api/settings/llm/cache/clear", {});
      toast(`已清空缓存（删除 ${res.removed || 0} 条）。`, "success");
      await render(el, ctx, params);
    } catch (err) {
      toast(err.message || "清空失败", "error");
      btn.disabled = false;
    }
  });

  el.querySelector("[data-diag-copy]")?.addEventListener("click", async (e) => {
    const src = el.querySelector("#diag-plain");
    if (!src) return;
    try {
      await navigator.clipboard.writeText(src.value);
      toast("诊断信息已复制，可以直接贴给协作者。", "success", 2600);
    } catch {
      src.select();
      toast("浏览器拒绝了剪贴板权限；已选中文本，可以手动复制。", "info", 4200);
    }
    e.currentTarget.disabled = false;
  });

  el.querySelector("[data-diag-retest]")?.addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true; btn.innerHTML = "重试中…";
    try {
      const info = await ctx.api.post("/api/settings/llm/test", {});
      toast(`连接正常（${info.latency_ms || 0}ms）。`, "success");
      await render(el, ctx, params);
    } catch (err) {
      toast(`还是失败：${err.message || "未知错误"}`, "error", 5200);
      await render(el, ctx, params);
    }
  });

  el.querySelector("#test-llm")?.addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    const box = el.querySelector("#llm-result");
    btn.disabled = true; btn.innerHTML = "测试中…";
    box.innerHTML = "";
    try {
      const res = await ctx.api.post("/api/settings/llm/test");
      box.innerHTML = `<div class="success-box">连接成功 · ${res.latency_ms} ms · ${escapeHTML(res.model)} · 回复：${escapeHTML(res.reply || "")}</div>`;
      toast("真实模型连接正常。", "success");
    } catch (err) {
      box.innerHTML = `<div class="error-box">${escapeHTML(err.message || "连接失败")}${err.detail ? `<br>${escapeHTML(String(err.detail).slice(0, 300))}` : ""}</div>`;
      toast(err.message || "连接失败", "error");
    } finally {
      btn.disabled = false; btn.innerHTML = `${icon("zap", 15)} 测试连接`;
    }
  });
  el.querySelector("#clear-llm")?.addEventListener("click", async () => {
    try {
      await ctx.api.post("/api/settings/llm", { api_key: "" });
      toast("已切回演示模式。", "success");
      await ctx.refreshView();
    } catch (e) {
      toast(e.message || "操作失败", "error");
    }
  });

  const minutes = el.querySelector("#goal-minutes");
  const minutesLabel = el.querySelector("#goal-minutes-label");
  minutes?.addEventListener("input", () => { minutesLabel.textContent = `${minutes.value} 分钟`; });
  el.querySelector("#rebuild-plan")?.addEventListener("click", async (e) => {
    if (!goal) return;
    const btn = e.currentTarget;
    btn.disabled = true; btn.innerHTML = "重排中…";
    try {
      const data = await ctx.api.post("/api/plan/rebuild", {
        title: el.querySelector("#goal-title").value.trim(),
        daily_minutes: Number(el.querySelector("#goal-minutes").value),
        deadline: el.querySelector("#goal-deadline").value,
      });
      ctx.state.dashboard = data;
      el.querySelector("#plan-result").innerHTML = `<div class="success-box">路径已重排：${escapeHTML(data.plan?.strategy?.summary || "已更新")}</div>`;
      toast("路径已重排。", "success");
    } catch (err) {
      el.querySelector("#plan-result").innerHTML = `<div class="error-box">${escapeHTML(err.message || "重排失败")}</div>`;
    } finally {
      btn.disabled = false; btn.innerHTML = `${icon("refresh", 15)} 保存并重排`;
    }
  });

  el.querySelector("#export-data")?.addEventListener("click", async () => {
    try {
      await ctx.api.download("/api/export", "boil-export.json");
      toast("导出已开始。", "success");
    } catch (e) {
      toast(e.message || "导出失败", "error");
    }
  });
  el.querySelector("#export-anki")?.addEventListener("click", async () => {
    try {
      await ctx.api.download("/api/export/anki", "boil-anki.txt");
      toast("Anki 卡片已导出。", "success");
    } catch (e) {
      toast(e.message || "Anki 导出失败", "error");
    }
  });
  el.querySelector("#import-data")?.addEventListener("click", () => {
    el.querySelector("#import-file")?.click();
  });
  el.querySelector("#import-file")?.addEventListener("change", async (event) => {
    const input = event.currentTarget;
    const file = input.files?.[0];
    input.value = "";
    if (!file) return;
    if (file.size > 12 * 1024 * 1024) return toast("备份文件不能超过 12 MB", "error");
    let data;
    try {
      data = JSON.parse(await file.text());
    } catch {
      return toast("备份不是合法 JSON", "error");
    }
    const src = data?.data && typeof data.data === "object" ? data.data : data;
    if (!src || (!Array.isArray(src.goals) && !Array.isArray(src.units))) {
      return toast("这不是 Boil 备份文件", "error");
    }
    const counts = {
      goals: (src.goals || []).length,
      units: (src.units || []).length,
      cards: (src.cards || []).length,
      sessions: (src.sessions || []).length,
      records: (src.boil_records || []).length,
    };
    const body = `<div class="callout teal"><strong>${escapeHTML(file.name)}</strong><br>${counts.goals} 个目标 · ${counts.units} 个单元 · ${counts.cards} 张卡 · ${counts.sessions} 个会话 · ${counts.records} 条火候记录</div>
      <div class="callout mt-2"><strong>合并</strong>：保留现有数据，按 ID 更新/插入备份内容。<br><strong>替换</strong>：先清空本机当前数据，再导入备份；不可撤销。</div>
      <div id="import-error"></div>`;
    openModal({
      title: "导入 Boil 备份",
      subtitle: "先确认数据规模，再选择合并或替换。",
      body, wide: true,
      actions: [
        { label: "取消", kind: "btn-ghost" },
        {
          label: "合并导入", kind: "btn-primary", close: false,
          onClick: async ({ close }) => {
            try {
              await doImport(data, "merge");
              close();
              return false;
            } catch (e) {
              document.getElementById("import-error").innerHTML = `<div class="error-box">${escapeHTML(e.message || "导入失败")}</div>`;
              return false;
            }
          },
        },
        {
          label: "替换导入", kind: "btn-danger", close: false,
          onClick: async ({ close }) => {
            const ok = await confirmDialog({
              title: "替换全部数据？",
              message: "会先清空当前目标、单元、卡片、会话、火候记录和速记，再导入备份。建议先导出当前数据。",
              confirmLabel: "清空并替换", danger: true,
            });
            if (!ok) return false;
            try {
              await doImport(data, "replace");
              close();
              return false;
            } catch (e) {
              document.getElementById("import-error").innerHTML = `<div class="error-box">${escapeHTML(e.message || "导入失败")}</div>`;
              return false;
            }
          },
        },
      ],
    });
  });

  async function doImport(data, mode) {
    const res = await ctx.api.post("/api/import", {
      data, mode, confirm: mode === "replace" ? "IMPORT" : "",
    });
    toast(`导入完成：${res.goals} 个目标 / ${res.units} 个单元 / ${res.cards} 张卡`
      + (res.snapshot ? `；导入前的库已快照为 ${res.snapshot}` : "；快照失败，建议先手动导出"),
      res.snapshot ? "success" : "info", 7200);
    await ctx.reloadAll();
    ctx.navigate("#/dashboard");
  }

  el.querySelector("#save-emb")?.addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true; btn.innerHTML = "保存中…";
    try {
      const payload = {
        base_url: el.querySelector("#emb-base").value.trim(),
        model: el.querySelector("#emb-model").value.trim(),
        timeout: Number(el.querySelector("#emb-timeout").value) || 30,
      };
      const key = el.querySelector("#emb-key").value.trim();
      if (key) payload.api_key = key;
      await ctx.api.post("/api/settings/embeddings", payload);
      toast("Embedding 配置已保存。", "success");
      await ctx.refreshView();
    } catch (err) {
      toast(err.message || "保存失败", "error");
    } finally {
      btn.disabled = false; btn.innerHTML = `${icon("check", 15)} 保存 Embedding`;
    }
  });
  el.querySelector("#test-emb")?.addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    const box = el.querySelector("#emb-result");
    btn.disabled = true; btn.innerHTML = "测试中…";
    box.innerHTML = "";
    try {
      const res = await ctx.api.post("/api/settings/embeddings/test");
      box.innerHTML = `<div class="success-box">语义检索连接成功 · ${res.ms}ms · ${escapeHTML(res.model)} · ${res.dim} 维</div>`;
      toast("Embedding 连接正常。", "success");
    } catch (err) {
      box.innerHTML = `<div class="error-box">${escapeHTML(err.message || "连接失败")}${err.detail ? `<br>${escapeHTML(String(err.detail).slice(0, 300))}` : ""}</div>`;
      toast(err.message || "连接失败", "error");
    } finally {
      btn.disabled = false; btn.innerHTML = `${icon("zap", 15)} 测一次语义检索`;
    }
  });
  el.querySelector("#clear-emb")?.addEventListener("click", async () => {
    try {
      await ctx.api.post("/api/settings/embeddings", { api_key: "" });
      toast("已切回本地哈希向量。", "success");
      await ctx.refreshView();
    } catch (e) {
      toast(e.message || "操作失败", "error");
    }
  });

  el.querySelector("#release-head")?.addEventListener("click", () => openReleaseModal(ctx));
  if (info && isUnseen(info)) markSeen(info);  // 更新内容已经在这页摊开了

  el.querySelector("#reset-data")?.addEventListener("click", async () => {
    const ok = await confirmDialog({
      title: "清空全部数据？",
      message: "这会删除所有目标、单元、复习记录、对话与掌握度，且不可撤销。建议先导出备份。",
      confirmLabel: "清空并重置",
      danger: true,
    });
    if (!ok) return;
    try {
      await ctx.api.post("/api/reset", { confirm: "RESET" });
      toast("已重置。", "success");
      apiResetAndReload(ctx);
    } catch (e) {
      toast(e.message || "重置失败", "error");
    }
  });
}

function apiResetAndReload(ctx) {
  ctx.state.dashboard = null;
  ctx.state.me = null;
  location.hash = "#/onboarding";
  setTimeout(() => location.reload(), 200);
}
