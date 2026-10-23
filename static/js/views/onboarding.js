/* Onboarding: choose a pack or compile your own material, then create a goal. */
import { aiFailure, aiSuccess, escapeHTML, icon, toast } from "../utils.js";

const LEVELS = [
  ["beginner", "刚刚起步"],
  ["intermediate", "有一些基础"],
  ["advanced", "想查漏补缺"],
];

function defaultDate(days = 56) {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

const MATERIAL_EXTENSIONS = new Set(["md", "markdown", "json", "txt"]);
const MAX_FILE_BYTES = 2 * 1024 * 1024;
const MAX_TOTAL_CHARS = 180000;
const MAX_FILES = 20;

function fileExtension(name) {
  return String(name || "").split(".").pop().toLowerCase();
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function flattenJSON(value, depth = 0) {
  if (depth > 6) return "…";
  if (value == null) return "无";
  if (typeof value !== "object") return String(value);
  const pad = "  ".repeat(depth);
  if (Array.isArray(value)) {
    if (!value.length) return `${pad}（空）`;
    return value.map((item, i) => {
      if (item && typeof item === "object") {
        return `${pad}- 条目 ${i + 1}\n${flattenJSON(item, depth + 1)}`;
      }
      return `${pad}- ${String(item)}`;
    }).join("\n");
  }
  return Object.entries(value).map(([key, item]) => {
    if (item && typeof item === "object") {
      return `${pad}${key}：\n${flattenJSON(item, depth + 1)}`;
    }
    return `${pad}${key}：${item == null ? "无" : String(item)}`;
  }).join("\n");
}

export async function render(el, ctx, params) {
  window.scrollTo({ top: 0, behavior: "instant" });
  const catalog = await ctx.api.get("/api/packs");
  const packs = catalog.packs || [];
  const queryPack = params.query?.get("pack") || "";
  let selected = packs.find(p => p.id === queryPack) || packs.find(p => p.id === catalog.default_pack) || packs[0];
  let mode = selected ? "pack" : "material";
  let step = 1;
  let materialPack = null;
  let busy = false;
  let files = [];
  let compileQuality = null;
  let compileWarnings = [];
  let draft = {
    name: ctx.state.me?.user?.name || "",
    goalTitle: "",
    materialTitle: "",
    subject: "",
    text: "",
    deadline: defaultDate(),
    daily: 40,
    level: "beginner",
  };

  el.innerHTML = `
    <div class="onboarding">
      <section class="onboarding-hero">
        <a class="brand" href="#/dashboard">
          <span class="brand-mark">
            <span class="kettle">
              <span class="steam s1"></span><span class="steam s2"></span><span class="steam s3"></span>
              <span class="kettle-handle"></span><span class="kettle-spout"></span><span class="kettle-body"></span><span class="kettle-base"></span>
            </span>
          </span>
          <span class="brand-text"><strong>BOIL</strong><small>烧水 · 学习 OS</small></span>
        </a>
        <div>
          <h1>把知识<br><em>烧开</em>，而不是看过。</h1>
          <p class="lead">AI 负责准备：规划路径、预消化材料、追问、评分。你只负责一件事——真正把它学会。</p>
          <div class="quote">“慢就是快。一个单元没有烧开，就不急着进入下一个。”</div>
        </div>
        <div class="foot">LOCAL-FIRST · OPEN SOURCE · MODEL-AGNOSTIC</div>
      </section>
      <section class="onboarding-panel">
        <div class="step-dots"><span class="step-dot active" data-dot="1"></span><span class="step-dot" data-dot="2"></span><span class="step-dot" data-dot="3"></span></div>
        <div id="onboarding-body"></div>
      </section>
    </div>`;

  const body = el.querySelector("#onboarding-body");

  function updateDots() {
    el.querySelectorAll("[data-dot]").forEach(d => d.classList.toggle("active", Number(d.dataset.dot) <= step));
  }

  function fileListHTML() {
    if (!files.length) {
      return `<div class="file-empty">还没有选择文件；也可以直接在下方粘贴文本。</div>`;
    }
    return files.map((f, i) => `
      <div class="file-row">
        <span class="file-kind">${escapeHTML(f.kind)}</span>
        <span class="file-name" title="${escapeHTML(f.name)}">${escapeHTML(f.name)}</span>
        <span class="file-meta">${formatBytes(f.size)} · ${f.chars.toLocaleString()} 字符</span>
        <button class="file-remove" type="button" data-file-remove="${i}" aria-label="移除 ${escapeHTML(f.name)}">×</button>
      </div>`).join("");
  }

  function refreshFiles() {
    const list = body.querySelector("#ob-file-list");
    if (list) list.innerHTML = fileListHTML();
    const summary = body.querySelector("#ob-file-summary");
    if (summary) {
      summary.textContent = files.length
        ? `${files.length} 个文件 · ${files.reduce((n, f) => n + f.chars, 0).toLocaleString()} 字符`
        : "可多选";
    }
  }

  function renderStep1() {
    step = 1; updateDots();
    body.innerHTML = `
      <div class="onboarding-step active">
        <h2>你想先烧开哪一壶水？</h2>
        <p class="sub">选一个内容包，或把你自己的材料编译成学习闭环。之后随时可以换。</p>
        <div class="pack-grid">
          ${packs.map(p => `
            <button class="pack-card ${mode === "pack" && selected?.id === p.id ? "selected" : ""}" data-pack="${escapeHTML(p.id)}">
              <span class="pack-cover ${escapeHTML(p.cover || "ember")}">${escapeHTML((p.name || "?").slice(0, 1))}</span>
              <span class="pack-card-body">
                <span class="pack-card-title">${escapeHTML(p.name)}</span>
                <span class="pack-card-sub">${escapeHTML(p.description || "")}</span>
                <span class="pack-card-meta">
                  <span class="badge badge-muted">${p.unit_count} 个单元</span>
                  <span class="badge badge-muted">${Math.round((p.estimated_minutes || 0) / 60)} 小时</span>
                  ${p.installed ? `<span class="badge badge-teal">已安装</span>` : ""}
                </span>
              </span>
              ${mode === "pack" && selected?.id === p.id ? `<span class="badge badge-ember">已选</span>` : ""}
            </button>`).join("")}
          <button class="pack-card ${mode === "material" ? "selected" : ""}" data-material>
            <span class="pack-cover steam">✎</span>
            <span class="pack-card-body">
              <span class="pack-card-title">用我的材料创建</span>
              <span class="pack-card-sub">粘贴讲义、笔记或教材片段，编译成知识单元、探针与复习卡。</span>
              <span class="pack-card-meta"><span class="badge badge-muted">PDF 文本 / Markdown / 纯文本</span><span class="badge badge-muted">本地优先</span></span>
            </span>
            ${mode === "material" ? `<span class="badge badge-ember">已选</span>` : ""}
          </button>
        </div>
        <div class="onboarding-actions">
          <button class="btn btn-ghost" data-skip ${ctx.state.me?.goals?.length ? "" : "disabled"}>回到今日</button>
          <button class="btn btn-primary" data-next>下一步 ${icon("arrow", 15)}</button>
        </div>
      </div>`;
    body.querySelectorAll("[data-pack]").forEach(btn => btn.addEventListener("click", () => {
      mode = "pack"; selected = packs.find(p => p.id === btn.dataset.pack); materialPack = null; renderStep1();
    }));
    body.querySelector("[data-material]")?.addEventListener("click", () => { mode = "material"; materialPack = null; renderStep2(); });
    body.querySelector("[data-next]")?.addEventListener("click", () => {
      if (mode === "pack" && !selected) return toast("先选一个内容包", "error");
      renderStep2();
    });
    body.querySelector("[data-skip]")?.addEventListener("click", () => ctx.navigate("#/dashboard"));
  }

  function renderStep2() {
    step = 2; updateDots();
    const title = mode === "material" ? (materialPack?.name || draft.materialTitle || "我的学习材料") : (selected?.name || "我的学习目标");
    body.innerHTML = `
      <div class="onboarding-step active">
        <h2>${mode === "material" ? "把材料交给编译器" : "设定你的学习契约"}</h2>
        <p class="sub">${mode === "material" ? "先编译成可学习的单元，再决定目标与节奏。" : "目标、截止日期与每天可投入的时间，会决定路径的密度。"}</p>
        <div class="form-grid mt-4">
          <div class="field"><label>你的称呼</label><input class="input" id="ob-name" value="${escapeHTML(draft.name)}" placeholder="学习者"></div>
          <div class="field"><label>目标名称</label><input class="input" id="ob-title" value="${escapeHTML(draft.goalTitle || title)}" placeholder="例如：掌握线性代数期末"></div>
          ${mode === "material" ? `
            <div class="field full">
              <label>材料标题 <button class="btn btn-ghost btn-sm" id="ob-suggest" type="button">${icon("wand", 13)} AI 生成信息</button></label>
              <input class="input" id="ob-material-title" value="${escapeHTML(materialPack?.name || draft.materialTitle)}" placeholder="例如：第 3 章 导数与微分">
              <span class="hint" id="ob-suggest-hint"></span>
            </div>
            <div class="field"><label>学科 / 主题</label><input class="input" id="ob-subject" value="${escapeHTML(materialPack?.subject || draft.subject)}" placeholder="例如：高等数学"></div>
            <div class="field full">
              <label>材料文件（可多选）</label>
              <div class="file-drop" id="ob-drop" role="button" tabindex="0" aria-label="选择 Markdown、JSON 或 TXT 材料文件">
                <span class="file-drop-icon">${icon("upload", 18)}</span>
                <span class="file-drop-copy"><strong>拖入或选择 .md / .json / .txt</strong><span>多份文件会合并到同一个主题；单文件不超过 2 MB</span></span>
                <span class="badge badge-muted" id="ob-file-summary">${files.length ? `${files.length} 个文件` : "可多选"}</span>
                <input type="file" id="ob-files" accept=".md,.markdown,.json,.txt" multiple hidden>
              </div>
              <div class="file-list" id="ob-file-list">${fileListHTML()}</div>
              <span class="hint">JSON 会按键值结构铺平成可读文本；文件与下方粘贴文本合计不超过 ${MAX_TOTAL_CHARS.toLocaleString()} 字符。</span>
            </div>
            <div class="field full"><label>材料内容</label><textarea class="textarea tall" id="ob-text" placeholder="也可以把补充讲义、笔记或教材片段粘贴到这里。">${escapeHTML(draft.text)}</textarea><span class="hint">演示模式会在本地切分、出题、生成卡片；配置模型后会做更细的结构抽取。</span></div>
          ` : ""}
          <div class="field"><label>截止日期</label><input class="input" type="date" id="ob-deadline" value="${escapeHTML(draft.deadline)}"></div>
          <div class="field"><label>每天可投入 <strong id="ob-minutes-label">${draft.daily} 分钟</strong></label><input class="slider" id="ob-minutes" type="range" min="15" max="120" step="5" value="${draft.daily}"><span class="hint">时间不够时，系统会生成“最少必要路径”，并明确暂存哪些单元。</span></div>
          <div class="field full"><label>当前基础</label><div class="choice-row">${LEVELS.map(([v, l]) => `<button class="choice ${v === draft.level ? "active" : ""}" data-level="${v}">${l}</button>`).join("")}</div></div>
        </div>
        <div class="preview-box" id="ob-preview">
          <strong>${mode === "material" ? "下一步会先编译，不会立刻开始。" : `将安装「${escapeHTML(selected.name)}」的 ${selected.unit_count} 个单元。`}</strong><br>
          ${mode === "material" ? "编译完成后，你可以预览单元再开始。" : `预计 ${Math.round((selected.estimated_minutes || 0) / 60)} 小时的内容量，按每天 40 分钟拆成小步；系统会先保留复习时间，再排新内容。`}
        </div>
        <div class="onboarding-actions">
          <button class="btn btn-ghost" data-back>上一步</button>
          <button class="btn btn-primary" data-submit>${mode === "material" ? "编译并预览" : "生成学习路径"} ${icon("arrow", 15)}</button>
        </div>
      </div>`;
    body.querySelectorAll("[data-level]").forEach(b => b.addEventListener("click", () => {
      draft.level = b.dataset.level;
      body.querySelectorAll("[data-level]").forEach(x => x.classList.toggle("active", x === b));
    }));
    const minutes = body.querySelector("#ob-minutes");
    const minutesLabel = body.querySelector("#ob-minutes-label");
    minutes?.addEventListener("input", () => { minutesLabel.textContent = `${minutes.value} 分钟`; });

    if (mode === "material") {
      const drop = body.querySelector("#ob-drop");
      const fileInput = body.querySelector("#ob-files");
      const fileList = body.querySelector("#ob-file-list");

      const addFiles = async (incoming) => {
        const chosen = [...(incoming || [])];
        if (!chosen.length) return;
        const errors = [];
        let added = 0;
        for (const file of chosen) {
          if (files.length >= MAX_FILES) {
            errors.push(`最多合并 ${MAX_FILES} 个文件`);
            break;
          }
          const ext = fileExtension(file.name);
          if (!MATERIAL_EXTENSIONS.has(ext)) {
            errors.push(`${file.name}：只支持 .md / .json / .txt`);
            continue;
          }
          if (file.size > MAX_FILE_BYTES) {
            errors.push(`${file.name}：超过 2 MB`);
            continue;
          }
          try {
            let content = await file.text();
            if (ext === "json") {
              try {
                content = flattenJSON(JSON.parse(content));
              } catch {
                throw new Error("JSON 解析失败");
              }
            }
            content = content.replace(/\r\n?/g, "\n").trim();
            if (!content) throw new Error("文件为空");
            const used = files.reduce((n, f) => n + f.chars, 0);
            if (used + content.length > MAX_TOTAL_CHARS) {
              throw new Error(`合并后超过 ${MAX_TOTAL_CHARS.toLocaleString()} 字符上限`);
            }
            files.push({ name: file.name, size: file.size, chars: content.length, kind: ext, text: content });
            added += 1;
          } catch (err) {
            errors.push(`${file.name}：${err.message || "读取失败"}`);
          }
        }
        if (added) {
          refreshFiles();
          const titleInput = body.querySelector("#ob-material-title");
          if (titleInput && !titleInput.value.trim()) {
            titleInput.value = files[0].name.replace(/\.[^.]+$/, "");
          }
          toast(`已加入 ${added} 个文件，将合并编译为同一个主题。`, "success", 3600);
        }
        if (errors.length) toast(errors[0], "error", 6000);
      };

      drop?.addEventListener("click", (event) => {
        if (event.target === fileInput) return;
        fileInput?.click();
      });
      drop?.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          fileInput?.click();
        }
      });
      ["dragenter", "dragover"].forEach(type => drop?.addEventListener(type, (event) => {
        event.preventDefault();
        if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
        drop.classList.add("dragging");
      }));
      ["dragleave", "drop"].forEach(type => drop?.addEventListener(type, (event) => {
        event.preventDefault();
        drop.classList.remove("dragging");
      }));
      drop?.addEventListener("drop", (event) => addFiles(event.dataTransfer?.files));
      fileInput?.addEventListener("change", () => {
        addFiles(fileInput.files);
        fileInput.value = "";
      });
      fileList?.addEventListener("click", (event) => {
        const btn = event.target.closest("[data-file-remove]");
        if (!btn) return;
        const index = Number(btn.dataset.fileRemove);
        if (!Number.isInteger(index)) return;
        files.splice(index, 1);
        refreshFiles();
      });

      // 粘贴清洗：网页/Word/PDF 复制来的项目符号、全角空格、\[..\] 公式会干扰切分
      const pasteTarget = body.querySelector("#ob-text");
      pasteTarget?.addEventListener("paste", (event) => {
        const pasted = event.clipboardData?.getData("text/plain") || "";
        if (!pasted.trim()) return;
        event.preventDefault();
        ctx.api.post("/api/material/fix", { text: pasted }).then((res) => {
          const value = res.changed ? res.text : pasted;
          const start = pasteTarget.selectionStart ?? pasteTarget.value.length;
          const end = pasteTarget.selectionEnd ?? pasteTarget.value.length;
          pasteTarget.value = pasteTarget.value.slice(0, start) + value + pasteTarget.value.slice(end);
          pasteTarget.selectionStart = pasteTarget.selectionEnd = start + value.length;
          pasteTarget.dispatchEvent(new Event("input", { bubbles: true }));
          if (res.changed && res.applied?.length) {
            toast(`已清洗粘贴内容：${res.applied.join("、")}。`, "success", 4600);
          }
        }).catch(() => {
          // 清洗接口失败不能挡住粘贴本身
          const start = pasteTarget.selectionStart ?? pasteTarget.value.length;
          const end = pasteTarget.selectionEnd ?? pasteTarget.value.length;
          pasteTarget.value = pasteTarget.value.slice(0, start) + pasted + pasteTarget.value.slice(end);
          pasteTarget.dispatchEvent(new Event("input", { bubbles: true }));
        });
      });

      body.querySelector("#ob-suggest")?.addEventListener("click", async (event) => {
        event.preventDefault();
        event.stopPropagation();
        const btn = event.currentTarget;
        const pasted = body.querySelector("#ob-text")?.value.trim() || "";
        if (!files.length && pasted.length < 60) {
          return toast("先上传材料文件，或粘贴至少 60 字文本", "error");
        }
        btn.disabled = true;
        const label = btn.innerHTML;
        btn.innerHTML = "分析中…";
        try {
          const res = await ctx.api.post("/api/packs/suggest", {
            text: pasted, documents: files.map(f => ({ name: f.name, text: f.text })),
          });
          const fillIfEmpty = (selector, value) => {
            const field = body.querySelector(selector);
            if (field && value && !field.value.trim()) field.value = value;
          };
          fillIfEmpty("#ob-material-title", res.title);
          fillIfEmpty("#ob-subject", res.subject);
          fillIfEmpty("#ob-title", res.goal_title);
          const hint = body.querySelector("#ob-suggest-hint");
          if (hint) hint.textContent = res.description || "";
          aiSuccess(ctx);
          if (res.source === "model") {
            toast("AI 已生成标题与主题，可直接修改。", "success", 4200);
          } else {
            toast("未配置模型，已按本地规则生成。", "info", 4200);
          }
        } catch (err) {
          aiFailure(ctx);
          toast(err.message || "生成失败", "error", 5000);
        } finally {
          btn.disabled = false;
          btn.innerHTML = label;
        }
      });
    }

    body.querySelector("[data-back]").addEventListener("click", renderStep1);
    body.querySelector("[data-submit]").addEventListener("click", async () => {
      if (busy) return;
      const name = body.querySelector("#ob-name").value.trim();
      const goalTitle = body.querySelector("#ob-title").value.trim();
      const deadline = body.querySelector("#ob-deadline").value;
      const daily = Number(minutes.value);
      if (!goalTitle) return toast("给这个目标起个名字", "error");
      draft = { ...draft, name, goalTitle, deadline, daily };
      busy = true;
      const btn = body.querySelector("[data-submit]");
      const old = btn.innerHTML;
      btn.disabled = true; btn.innerHTML = "正在准备…";
      try {
        if (mode === "material") {
          if (materialPack) {
            await createGoal({ name, pack_id: materialPack.id, goal_title: goalTitle, deadline, daily_minutes: daily, level: draft.level });
            return;
          }
          const pasted = body.querySelector("#ob-text").value.trim();
          const documents = files.map(f => ({ name: f.name, text: f.text }));
          const totalChars = files.reduce((n, f) => n + f.chars, 0) + pasted.length;
          if (!documents.length && !pasted) throw new Error("请先上传材料文件，或粘贴一段文本");
          if (totalChars < 60) throw new Error("材料太短，至少 60 个字符");
          if (totalChars > MAX_TOTAL_CHARS) throw new Error(`材料超过 ${MAX_TOTAL_CHARS.toLocaleString()} 字符上限`);
          const materialTitle = body.querySelector("#ob-material-title").value.trim() || goalTitle;
          const subject = body.querySelector("#ob-subject").value.trim();
          draft = { ...draft, materialTitle, subject, text: pasted };
          const res = await ctx.api.post("/api/packs/compile", {
            title: materialTitle, subject: subject, text: pasted, documents, use_llm: true,
          }, { timeout: 600000 });   // 分批编译串好几次模型调用，90s 默认超时必先炸
          materialPack = res.pack;
          compileQuality = res.quality || null;
          compileWarnings = res.warnings || [];
          if (compileWarnings.length) toast(compileWarnings[0], "warn", 7000);
          renderStep3();
          return;
        }
        await createGoal({ name, pack_id: selected.id, goal_title: goalTitle, deadline, daily_minutes: daily, level: draft.level });
      } catch (e) {
        toast(e.message || "创建失败", "error", 5000);
        btn.disabled = false; btn.innerHTML = old;
      } finally {
        busy = false;
      }
    });
  }

  function renderStep3() {
    step = 3; updateDots();
    const units = materialPack?.units || [];
    const duplicates = compileQuality?.near_duplicates || [];
    const qualityIssues = (compileQuality?.issues || []).filter(i => i.level === "warn");
    const digest = materialPack?.digest || {};
    const outline = materialPack?.outline || [];
    const phases = materialPack?.study_plan || [];
    const byModel = materialPack?.generated_by === "model";
    const sourceBadge = byModel
      ? `<span class="badge badge-teal">AI 分析</span>`
      : `<span class="badge badge-muted">本地规则</span>`;
    const minutesTotal = phases.reduce((n, p) => n + (Number(p.minutes) || 0), 0);
    body.innerHTML = `
      <div class="onboarding-step active">
        <h2>编译完成，先看一眼结构</h2>
        <p class="sub">标题、大纲与计划都是读完全部材料后生成的；没有把握的地方不会硬编。</p>
        <div class="preview-box">
          <div class="between"><strong>${escapeHTML(materialPack.name)}</strong><span class="badge badge-teal">${units.length} 个单元</span></div>
          ${digest.overview ? `
            <div class="digest-block mt-3">
              <div class="between"><strong>材料总览</strong>${sourceBadge}</div>
              <p>${escapeHTML(digest.overview)}</p>
              ${(digest.advice || []).length ? `<ul class="digest-advice">${digest.advice.map(a => `<li>${escapeHTML(a)}</li>`).join("")}</ul>` : ""}
            </div>` : ""}
        </div>
        ${outline.length ? `
          <div class="preview-box mt-3">
            <div class="between"><strong>学习大纲</strong>${sourceBadge}</div>
            <ol class="outline-list">
              ${outline.map(o => `
                <li>
                  <span class="outline-title">${escapeHTML(o.title)}</span>
                  ${o.summary ? `<span class="outline-summary">${escapeHTML(o.summary)}</span>` : ""}
                  ${(o.unit_titles || []).length ? `<span class="outline-units">${o.unit_titles.map(escapeHTML).join(" · ")}</span>` : ""}
                </li>`).join("")}
            </ol>
          </div>` : ""}
        ${phases.length ? `
          <div class="preview-box mt-3">
            <div class="between"><strong>阶段计划</strong><span class="hint">${minutesTotal ? `约 ${Math.round(minutesTotal / 60)} 小时内容量` : ""}</span></div>
            ${phases.map(p => `
              <div class="phase-row">
                <span class="phase-name">${escapeHTML(p.phase || "")}</span>
                <span class="phase-body">
                  <strong>${escapeHTML(p.focus || "")}</strong>
                  <span class="phase-meta">${(p.unit_titles || []).length} 个单元${p.minutes ? ` · ${p.minutes} 分钟` : ""}</span>
                </span>
              </div>`).join("")}
          </div>` : ""}
        ${qualityIssues.length || duplicates.length ? `
          <div class="preview-box mt-3">
            <div class="between"><strong>编译质检</strong><span class="badge ${compileQuality?.engine === "model" ? "badge-teal" : "badge-muted"}">${compileQuality?.engine === "model" ? "AI 复核" : "本地规则"}</span></div>
            ${qualityIssues.length ? `<ul class="digest-advice">${qualityIssues.map(i => `<li>[${escapeHTML(i.code)}] ${escapeHTML(i.detail || "")}</li>`).join("")}</ul>` : ""}
            ${compileWarnings.length ? `<ul class="digest-advice qa-warnings">${compileWarnings.map(w => `<li>${escapeHTML(w)}</li>`).join("")}</ul>` : ""}
            ${duplicates.length ? `<div class="qa-box">
              <p class="faint tiny">发现 ${duplicates.length} 对高度相似的单元。开始学习后可以在地图页合并——合并不是删除：被吸收的单元不再单独出现在地图上，但内容仍可通过归宿访问，复习卡也照常出现。</p>
              ${duplicates.map(d => `
                <div class="qa-item">
                  <span class="row" style="gap:6px;flex-wrap:wrap"><strong>${escapeHTML(d.titles?.[0] || "")}</strong><span class="faint">≈</span><strong>${escapeHTML(d.titles?.[1] || "")}</strong><span class="faint tiny">相似度 ${Math.round((d.similarity || 0) * 100)}%</span></span>
                </div>`).join("")}
            </div>` : ""}
          </div>` : ""}
        <details class="mt-3" open>
          <summary>全部 ${units.length} 个单元</summary>
          <div class="preview-units">
            ${units.map((u, i) => `<div class="preview-unit"><span class="n">${String(i + 1).padStart(2, "0")}</span><span>${escapeHTML(u.title)}</span></div>`).join("")}
          </div>
        </details>
        <div class="callout teal mt-3">大纲决定学习顺序，日期仍按你每天的可用时间计算；时间不够时系统会保留复习时间并明确暂存哪些单元。</div>
        <div class="onboarding-actions">
          <button class="btn btn-ghost" data-back>重新编辑</button>
          <button class="btn btn-primary" data-start>开始学习 ${icon("arrow", 15)}</button>
        </div>
      </div>`;
    body.querySelector("[data-back]").addEventListener("click", () => { materialPack = null; renderStep2(); });
    body.querySelector("[data-start]").addEventListener("click", async () => {
      await createGoal({
        name: draft.name,
        pack_id: materialPack.id,
        goal_title: draft.goalTitle || materialPack.name,
        deadline: draft.deadline,
        daily_minutes: draft.daily,
        level: draft.level,
      });
    });
  }

  async function createGoal(payload) {
    const btn = body.querySelector("[data-submit], [data-start]");
    if (btn) { btn.disabled = true; btn.innerHTML = "正在安装…"; }
    try {
      const result = await ctx.api.post("/api/onboarding", payload);
      ctx.state.dashboard = result;
      ctx.state.me = await ctx.api.get("/api/me");
      await ctx.loadCatalog(true);
      if (result.warnings?.length) toast(result.warnings[0], "info", 5000);
      toast("第一壶水已经架上了。", "success");
      ctx.navigate("#/dashboard");
    } catch (e) {
      toast(e.message || "创建目标失败", "error", 5000);
      if (btn) { btn.disabled = false; btn.innerHTML = "重试"; }
    }
  }

  renderStep1();
}
