/* Library: packs, material compiler, pack details. */
import {
  escapeHTML, icon, toast, openModal, truncate, formatMinutes, pct,
} from "../utils.js";

function packTile(pack) {
  return `<article class="card card-hover pack-tile">
    <div class="pack-tile-top ${escapeHTML(pack.cover || "ember")}">
      <span class="pack-mark">${escapeHTML((pack.subject || "PACK").toUpperCase())}</span>
      <span class="pack-name">${escapeHTML(pack.name)}</span>
    </div>
    <div class="pack-tile-body">
      <div class="pack-tile-desc">${escapeHTML(pack.description || "一个可学习、可测试的内容包。")}</div>
      <div class="chip-row">${(pack.tags || []).slice(0, 3).map(t => `<span class="tag">${escapeHTML(t)}</span>`).join("")}</div>
      <div class="pack-tile-meta">
        <span>${pack.unit_count} 个单元 · ${formatMinutes(pack.estimated_minutes || 0)}</span>
        <span>${escapeHTML(pack.license || "")}</span>
      </div>
      ${pack.quality ? `<div class="row wrap" style="gap:5px">
        <span class="badge ${pack.quality.atomic_rate >= 0.99 ? "badge-teal" : "badge-gold"}">原子率 ${pct(pack.quality.atomic_rate)}</span>
        <span class="badge badge-muted">探针覆盖 ${pct(pack.quality.probe_coverage)}</span>
        <span class="badge ${pack.quality.dag_ok ? "badge-teal" : "badge-danger"}">DAG ${pack.quality.dag_ok ? "✓" : "!"}</span>
      </div>` : ""}
      <div class="row" style="justify-content:space-between">
        <span class="badge ${pack.installed ? "badge-teal" : "badge-muted"}">${pack.installed ? "当前目标已安装" : "未安装"}</span>
        <span class="faint tiny">${pack.installed ? "继续学习" : "以此为起点"}</span>
      </div>
      <div class="pack-tile-actions">
        <button class="btn ${pack.installed ? "btn-teal" : "btn-primary"} btn-sm" data-start="${escapeHTML(pack.id)}">${icon(pack.installed ? "play" : "plus", 13)} ${pack.installed ? "继续学习" : "开始学习"}</button>
        <button class="btn btn-ghost btn-sm" data-detail="${escapeHTML(pack.id)}">${icon("eye", 13)} 详情</button>
      </div>
    </div>
  </article>`;
}

export async function render(el, ctx, params) {
  const catalog = await ctx.api.get("/api/packs");
  const packs = catalog.packs || [];
  el.innerHTML = `
    <div class="page-head">
      <div>
        <h2>学习内容包</h2>
        <p>内容包是 Boil 的更新单元：换一个包，就换一套知识地图与探针。你也可以把自己的材料编译成包。</p>
      </div>
      <div class="row wrap">
        <button class="btn btn-primary" data-compile>${icon("wand", 15)} 从材料创建</button>
        <button class="btn btn-ghost" data-import>${icon("upload", 15)} 导入 Pack</button>
      </div>
    </div>
    <div class="card library-hero">
      <div>
        <div class="hero-kicker"><span class="line"></span> LEARNING PACKS</div>
        <h2 style="font-family:var(--font-display);font-size:26px;margin-top:8px;font-weight:600">别从零开始搭知识体系。</h2>
        <p class="muted small mt-2" style="max-width:620px">一个 Pack 包含知识单元、本质、类比、边界、探针和复习卡。安装是幂等的，更新内容不会丢学习进度。</p>
      </div>
      <div class="row wrap">
        <span class="badge badge-ember">${packs.filter(p => p.installed).length} 个已安装</span>
        <span class="badge badge-muted">${packs.length} 个可用</span>
      </div>
    </div>
    <div class="library-grid">${packs.map(packTile).join("") || `<div class="empty"><p>内容库是空的。</p></div>`}</div>
    <div class="card card-pad mt-4">
      <div class="section-head"><div><div class="section-title">Pack 是怎么更新的</div><div class="section-sub">内容与内容包分离，升级不动学习证据</div></div>${icon("shield", 20)}</div>
      <div class="grid grid-3">
        <div class="callout teal"><strong>版本化</strong><br>每个 Pack 有 semver 与来源；同版本重复安装不会重置复习进度。</div>
        <div class="callout gold"><strong>可校验</strong><br>安装前校验单元 ID、探针、卡片与关系；失败整包拒绝，不会半导入。</div>
        <div class="callout ember"><strong>可替换</strong><br>新版本可以更新单元内容，保留卡片与掌握度；旧版本仍可追溯。</div>
      </div>
    </div>`;

  el.querySelectorAll("[data-start]").forEach(btn => btn.addEventListener("click", () => {
    const pack = packs.find(p => p.id === btn.dataset.start);
    if (!pack) return;
    if (pack.installed) ctx.navigate("#/map");
    else ctx.navigate(`#/onboarding?pack=${encodeURIComponent(pack.id)}`);
  }));
  el.querySelectorAll("[data-detail]").forEach(btn => btn.addEventListener("click", () => openDetail(btn.dataset.detail)));
  el.querySelector("[data-compile]")?.addEventListener("click", openCompiler);
  el.querySelector("[data-import]")?.addEventListener("click", openImporter);

  async function openDetail(packId) {
    try {
      const data = await ctx.api.get(`/api/packs/${encodeURIComponent(packId)}`);
      const pack = data;
      const units = pack.units || [];
      const body = `<div class="chip-row">${(pack.tags || []).map(t => `<span class="tag">${escapeHTML(t)}</span>`).join("")}</div>
        <div class="callout mt-3">${escapeHTML(pack.description || "")}</div>
        <div class="row wrap mt-3"><span class="badge badge-teal">${units.length} 个单元</span><span class="badge badge-muted">${formatMinutes(units.reduce((a, u) => a + (u.estimated_minutes || 45), 0))}</span><span class="badge badge-muted">${escapeHTML(pack.license || "")}</span></div>
        ${pack.quality ? `<div class="callout teal mt-3"><strong>质量门禁</strong> · 原子率 ${pct(pack.quality.atomic_rate)} · 探针覆盖 ${pct(pack.quality.probe_coverage)} · DAG ${pack.quality.dag_ok ? "无环" : "有环"}<br><span class="small">原子单元 ${pack.quality.atomic_units}/${pack.quality.total_units}；探针覆盖指 recall/explain/apply/boundary 四类齐全的单元比例。</span>${(pack.quality.issues || []).length ? `<br><span class="small muted">待修：${pack.quality.issues.map(escapeHTML).join("；")}</span>` : ""}</div>` : ""}
        <div class="preview-units mt-3" data-selectable data-source="library" style="max-height:320px">${units.map((u, i) => `<div class="preview-unit"><span class="n">${String(i + 1).padStart(2, "0")}</span><span><strong>${escapeHTML(u.title)}</strong><br><span class="faint">${escapeHTML(truncate(u.essence || "", 90))}</span></span></div>`).join("")}</div>
        ${pack.source?.system ? `<div class="source-line mt-3"><span class="badge badge-violet">${escapeHTML(pack.source.system)}</span><span>${escapeHTML(pack.source.kind || "")}</span><span class="faint">${escapeHTML(pack.source.note || "")}</span></div>` : ""}`;
      openModal({
        title: pack.name, subtitle: "Learning Pack · " + pack.version, body, wide: true,
        actions: [
          { label: "关闭", kind: "btn-ghost" },
          { label: "以此为起点", kind: "btn-primary", onClick: () => startPack(pack) },
        ],
      });
    } catch (e) {
      toast(e.message || "加载内容包失败", "error");
    }
  }

  function openCompiler() {
    const body = document.createElement("div");
    body.innerHTML = `<div class="form-grid">
      <div class="field full"><label>材料标题</label><input class="input" id="cp-title" placeholder="例如：第 3 章 导数与微分"></div>
      <div class="field"><label>学科 / 主题</label><input class="input" id="cp-subject" placeholder="例如：高等数学"></div>
      <div class="field"><label>目标名称</label><input class="input" id="cp-goal" placeholder="默认使用材料标题"></div>
      <div class="field full"><label>材料内容</label><textarea class="textarea tall" id="cp-text" placeholder="粘贴讲义、笔记或教材片段。至少 60 个字符。"></textarea><span class="hint">演示模式在本地切分、出题；配置模型后会做更细的结构抽取。不会上传到第三方，除非你配置了模型端点。</span></div>
    </div>
    <div id="cp-error"></div>`;
    const modal = openModal({
      title: "从材料创建内容包",
      subtitle: "内容编译 → 预览 → 安装为学习目标",
      body, wide: true,
      actions: [
        { label: "取消", kind: "btn-ghost" },
        {
          label: "编译并预览", kind: "btn-primary", close: false,
          onClick: async () => {
            const title = body.querySelector("#cp-title").value.trim();
            const subject = body.querySelector("#cp-subject").value.trim();
            const text = body.querySelector("#cp-text").value.trim();
            if (!title) return toast("给材料起个标题", "error"), false;
            if (text.length < 60) return toast("材料太短，至少 60 个字符", "error"), false;
            const btn = modal.modal.querySelector(".modal-foot .btn-primary");
            const old = btn.innerHTML;
            btn.disabled = true; btn.innerHTML = "正在编译…";
            try {
              const res = await ctx.api.post("/api/packs/compile", { title, subject, text, use_llm: true });
              if (res.warnings?.length) toast(res.warnings[0], "info", 5000);
              modal.close();
              openPackPreview(res.pack, { goalTitle: body.querySelector("#cp-goal").value.trim() || title });
            } catch (e) {
              body.querySelector("#cp-error").innerHTML = `<div class="error-box">${escapeHTML(e.message || "编译失败")}</div>`;
              toast(e.message || "编译失败", "error");
              btn.disabled = false; btn.innerHTML = old;
            }
            return false;
          },
        },
      ],
    });
  }

  function openImporter() {
    const body = document.createElement("div");
    body.innerHTML = `<div class="field"><label>Pack JSON</label><textarea class="textarea tall" id="imp-json" placeholder='{"schema_version":"1.0","id":"...","name":"...","version":"1.0.0","units":[...]}'></textarea><span class="hint">导入前会做完整校验；失败的包不会被安装。</span></div><div id="imp-error"></div>`;
    const modal = openModal({
      title: "导入 Learning Pack", subtitle: "JSON → 校验 → 预览 → 安装", body, wide: true,
      actions: [
        { label: "取消", kind: "btn-ghost" },
        {
          label: "校验并预览", kind: "btn-primary", close: false,
          onClick: async () => {
            try {
              const pack = JSON.parse(body.querySelector("#imp-json").value);
              const res = await ctx.api.post("/api/packs/import", { pack });
              modal.close();
              openPackPreview(res.pack, { goalTitle: res.pack.name });
            } catch (e) {
              body.querySelector("#imp-error").innerHTML = `<div class="error-box">${escapeHTML(e.message || "导入失败")}</div>`;
            }
            return false;
          },
        },
      ],
    });
  }

  function openPackPreview(pack, { goalTitle = "" } = {}) {
    const units = pack.units || [];
    const body = `<div class="callout teal">${escapeHTML(pack.description || "")}</div>
      <div class="row wrap mt-3"><span class="badge badge-teal">${units.length} 个单元</span><span class="badge badge-muted">${escapeHTML(pack.license || "user-provided")}</span><span class="badge badge-violet">${escapeHTML(pack.source?.system || "compiler")}</span></div>
      <div class="preview-units mt-3" data-selectable data-source="library" style="max-height:300px">${units.map((u, i) => `<div class="preview-unit"><span class="n">${String(i + 1).padStart(2, "0")}</span><span><strong>${escapeHTML(u.title)}</strong><br><span class="faint">${escapeHTML(truncate(u.essence || "", 100))}</span></span></div>`).join("")}</div>`;
    openModal({
      title: "预览内容包", subtitle: pack.name, body, wide: true,
      actions: [
        { label: "取消", kind: "btn-ghost" },
        {
          label: "安装并开始", kind: "btn-primary",
          onClick: async () => {
            await createGoal({ pack_id: pack.id, goal_title: goalTitle || pack.name, daily_minutes: 40, level: "beginner" });
          },
        },
      ],
    });
  }

  async function startPack(pack) {
    ctx.navigate(`#/onboarding?pack=${encodeURIComponent(pack.id)}`);
  }

  async function createGoal(payload) {
    try {
      const result = await ctx.api.post("/api/onboarding", payload);
      ctx.state.dashboard = result;
      ctx.state.me = await ctx.api.get("/api/me");
      await ctx.loadCatalog(true);
      toast("新目标已经架上了。", "success");
      ctx.navigate("#/dashboard");
    } catch (e) {
      toast(e.message || "创建目标失败", "error", 5000);
    }
  }
}
