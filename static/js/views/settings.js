/* Settings: model connection, learning contract, data ownership. */
import {
  escapeHTML, escapeAttr, icon, toast, openModal, confirmDialog, formatMinutes,
} from "../utils.js";

function providerChips(providers) {
  return `<div class="provider-grid">${providers.map((p, i) => `<button class="provider-chip" data-provider="${i}">${escapeHTML(p.name)}</button>`).join("")}</div>`;
}

export async function render(el, ctx, params) {
  const cfg = await ctx.api.get("/api/settings/llm");
  const goals = await ctx.api.get("/api/goals");
  const goal = goals.active_goal;
  const health = await ctx.api.get("/api/health");
  const mode = cfg.enabled ? "live" : "demo";

  el.innerHTML = `
    <div class="page-head">
      <div>
        <h2>系统设置</h2>
        <p>模型、学习契约与数据都归你控制。默认本地优先，密钥只留在这台机器。</p>
      </div>
      <div class="row wrap">
        <span class="badge ${mode === "live" ? "badge-teal" : "badge-gold"}">${mode === "live" ? "真实模型已连接" : "演示模式"}</span>
        <span class="badge badge-muted">v${escapeHTML(health.version || "1.0.0")}</span>
      </div>
    </div>
    <div class="settings-grid">
      <div class="settings-main">
        <div class="card settings-card">
          <div class="between">
            <div><h3>模型连接</h3><div class="sub">任意 OpenAI 兼容端点。不配置也能用本地演示引擎完整跑通。</div></div>
            <span class="badge ${mode === "live" ? "badge-teal" : "badge-gold"}">${mode === "live" ? "LIVE" : "DEMO"}</span>
          </div>
          <div class="llm-status ${mode === "live" ? "live" : ""} mt-3">
            <span class="dot"></span>
            <span>${mode === "live" ? `正在使用 <strong>${escapeHTML(cfg.model)}</strong>` : "当前使用本地确定性引擎：追问、评分、编译都能跑，但不做语义判断。"}</span>
          </div>
          ${cfg.forced_off ? `<div class="error-box">服务端设置了 BOIL_LLM_OFF=1，所有调用会强制走演示模式。</div>` : ""}
          ${cfg.last_error ? `<div class="error-box"><strong>上次调用失败：</strong>${escapeHTML(cfg.last_error.message || "")}<br>${escapeHTML((cfg.last_error.detail || "").slice(0, 320))}</div>` : ""}
          <div class="mt-4">
            <div class="side-label">快速填充</div>
            <div class="mt-2">${providerChips(cfg.providers || [])}</div>
          </div>
          <div class="form-grid mt-4">
            <div class="field full"><label>Base URL</label><input class="input" id="llm-base" value="${escapeAttr(cfg.base_url || "")}" placeholder="https://api.openai.com/v1"></div>
            <div class="field full"><label>API Key</label><input class="input" id="llm-key" type="password" autocomplete="off" placeholder="${cfg.api_key_set ? `已保存 ${escapeHTML(cfg.api_key_masked)} · 留空保持不变` : "sk-..."}"><span class="hint">密钥写入 data/llm.json（0600），不会返回给浏览器，也不会进入导出包。</span></div>
            <div class="field"><label>模型</label><input class="input" id="llm-model" value="${escapeAttr(cfg.model || "")}" placeholder="gpt-4o-mini"></div>
            <div class="field"><label>深度模型（苏格拉底/费曼）</label><input class="input" id="llm-deep" value="${escapeAttr(cfg.deep_model || "")}" placeholder="留空则与模型相同"></div>
            <div class="field"><label>温度 <span id="temp-label">${cfg.temperature}</span></label><input class="slider" id="llm-temp" type="range" min="0" max="1.5" step="0.1" value="${cfg.temperature}"></div>
            <div class="field"><label>最大输出 tokens（可选）</label><input class="input" id="llm-tokens" type="number" min="64" max="32000" value="${cfg.max_tokens || ""}" placeholder="留空由服务端决定"></div>
            <div class="field"><label>超时（秒）</label><input class="input" id="llm-timeout" type="number" min="10" max="600" value="${cfg.timeout || 120}"></div>
          </div>
          <div class="row mt-4" style="flex-wrap:wrap">
            <button class="btn btn-primary" id="save-llm">${icon("check", 15)} 保存连接</button>
            <button class="btn btn-ghost" id="test-llm">${icon("zap", 15)} 测试连接</button>
            <button class="btn btn-ghost" id="clear-llm">回到演示模式</button>
            <span class="faint tiny">测试会发送一个极短的 ping；成功后状态会自动刷新。</span>
          </div>
          <div id="llm-result"></div>
        </div>

        <div class="card settings-card">
          <div class="between"><div><h3>学习契约</h3><div class="sub">修改每天可投入的时间或截止日期，系统会重排剩余路径。</div></div><span class="badge badge-ember">计划是活的</span></div>
          ${goal ? `<div class="form-grid mt-4">
            <div class="field full"><label>当前目标</label><input class="input" id="goal-title" value="${escapeAttr(goal.title)}"></div>
            <div class="field"><label>每天可投入 <strong id="goal-minutes-label">${goal.daily_minutes} 分钟</strong></label><input class="slider" id="goal-minutes" type="range" min="15" max="120" step="5" value="${goal.daily_minutes}"></div>
            <div class="field"><label>截止日期</label><input class="input" id="goal-deadline" type="date" value="${escapeAttr(goal.deadline || "")}"></div>
            <div class="field full"><span class="hint">重排只针对尚未烧开的单元；已烧开的单元不会回到新学队列，但仍会按 FSRS 安排复习。</span></div>
          </div>
          <div class="row mt-3">
            <button class="btn btn-teal" id="rebuild-plan">${icon("refresh", 15)} 保存并重排</button>
            <a class="btn btn-ghost" href="#/map">先看当前路径</a>
          </div>
          <div id="plan-result"></div>` : `<div class="callout mt-3">还没有学习目标。<a href="#/onboarding" style="color:var(--ember)">创建一个</a></div>`}
        </div>

        <div class="card settings-card danger-zone">
          <h3>数据</h3>
          <div class="sub">导出完整学习数据（JSON），或清空本机演示数据。</div>
          <div class="row mt-3" style="flex-wrap:wrap">
            <button class="btn btn-ghost" id="export-data">${icon("download", 15)} 导出全部数据</button>
            <button class="btn btn-danger" id="reset-data">${icon("trash", 15)} 清空并重置</button>
          </div>
          <div class="faint tiny mt-2">导出包含目标、单元、卡片、复习日志、对话与解释，不包含 API Key。重置不可撤销。</div>
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
          <div class="facet-row"><span class="muted">版本</span><span>Boil ${escapeHTML(health.version || "")}</span><span></span></div>
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

  el.querySelectorAll("[data-provider]").forEach(btn => btn.addEventListener("click", () => {
    const p = (cfg.providers || [])[Number(btn.dataset.provider)];
    if (!p) return;
    if (p.base_url) el.querySelector("#llm-base").value = p.base_url;
    if (p.model) el.querySelector("#llm-model").value = p.model;
    el.querySelector("#llm-deep").value = p.deep_model || p.model || "";
    el.querySelectorAll("[data-provider]").forEach(x => x.classList.toggle("active", x === btn));
    toast(`已填入 ${p.name} 的默认端点，保存后生效。`, "info", 3000);
  }));

  function collectLLM() {
    const payload = {
      base_url: el.querySelector("#llm-base").value.trim(),
      model: el.querySelector("#llm-model").value.trim(),
      deep_model: el.querySelector("#llm-deep").value.trim(),
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
