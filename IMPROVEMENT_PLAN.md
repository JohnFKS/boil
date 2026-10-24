# Boil 改进计划（执行手册）

> 版本：1.1 · 2026-10-21（任务 A/B/C/D 已执行完成，见各节末尾的 ✅ 与「第 11 节 执行记录」）
> 本文档是**给执行模型（包括低阶模型）的分步执行手册**。请完整阅读「第 0 节 执行规范」后再动手。
> 来源：对 `mem`、`examgen`、`testlearnweb` 三个兄弟项目的深度研究 + Boil 现有代码核对。
> 所有「现状」小节里的 文件:行号 都已核对过，直接引用即可。

---

## 0. 执行规范（必读）

### 0.1 项目基本信息

- 仓库：`/workspace/testweb3/boil`（本地 git 仓库，远程 `https://github.com/JohnFKS/boil.git`）
- 后端：Python 3.10+ **纯标准库**（`ThreadingHTTPServer` + SQLite WAL + REST/SSE），禁止引入任何 pip 依赖
- 前端：`static/` 下**原生 ES Modules**，无构建链，无 CDN（KaTeX 已本地化）
- 启动：`python run.py`（默认端口 7860，数据在 `data/`）
- 测试：`python -m unittest discover -s tests -v`（当前 155 个全绿）
- JS 语法检查：`make check-js`
- 浏览器回归：需要**两个**服务实例——日常使用的 `python3 run.py`（7860）与验收用的 `BOIL_PORT=7861 python3 run.py`；然后 `python3 tools/browser_check.py`（CDP 驱动，默认打 7861，截图输出到 `screenshots/`）

### 0.2 硬性红线

1. **API Key 永远不出现在代码、日志、URL、导出文件、git 仓库**。Key 只存在于本地 `data/llm.json`、`data/embedding.json`（0600 权限，已被 .gitignore）。绝不要 `git add data/`。
2. **未经用户明确指示不得 `git commit` / `git push`**。
3. **不引入第三方依赖**（后端 pip、前端 npm/CDN 都不行）。
4. **演示模式（未配置 LLM）必须永远可用**：每个 AI 功能都要有本地规则回退，且回退内容要带 `source: "rules"` 标签，**不许把规则输出冒充 AI 输出**（借鉴 testlearn 的诚实原则）。
5. 前端渲染任何用户/模型文本一律过 `escapeHTML()`（`static/js/utils.js`），防止 XSS。CSP 已开 `script-src 'self'`，禁止 inline 事件处理器（onclick= 等）。
6. 每完成一个任务：跑一遍第 9 节的回归清单，全绿才算完成。

### 0.3 代码风格（与现状保持一致）

- Python：4 空格缩进；模块/函数顶部一行中文 docstring；无类型注解以外的花哨写法；错误用现有 `PackError`/`LLMError`/`ValueError` 体系。
- JS：ES Modules、`const` 优先、模板字符串拼 HTML、事件用 `addEventListener` + `data-*` 委托；清理函数挂在 `el._xxxCleanup` 上（参考 settings.js 的 `_settingsCleanup` 模式）。
- SQL：新表必须 `CREATE TABLE IF NOT EXISTS`，写进 `boil/db.py` 的 `SCHEMA_SQL`；对已存在的旧库加列用「PRAGMA table_info 检查 + ALTER TABLE」的幂等模式（见任务 C 的 `angle` 列）。
- API 路由：加到 `boil/server.py` 的 `ROUTES` 表（`("POST", re.compile(r"^/api/xxx$"), self.handler)`），handler 命名与现有保持一致。

### 0.4 执行顺序

按任务编号顺序执行：**A → B → C → D → E 批次 → F 批次**。
A/B/D 相互独立可单独执行；C 依赖 B（digest 字段在 B 中生成）。每个任务都是自包含的：包含目标、现状、改动点、验收标准。

---

## 1. 三项目深度对比清单

### 1.1 总览

| 维度 | Boil（本项目） | mem | examgen | testlearnweb |
|---|---|---|---|---|
| 定位 | AI 学习 OS（材料→编译→苏格拉底/费曼→FSRS 复习） | 本地单用户记忆工具（卡片+标注+口诀） | 考研数学「通法」归纳流水线（研究工具） | 商业产品 Zero 的逆向研究 + Python 复刻后端 |
| 后端 | Python 纯 stdlib + SQLite WAL | Flask + requests + SQLite | Node 纯 stdlib + JSONL 文件 | Python 纯 stdlib + JSON 文件 |
| 前端 | 原生 ES Modules SPA | 原生 JS + CDN 库 | 原生 JS SPA（单文件 3096 行） | Next.js 静态导出（React） |
| LLM 网关 | OpenAI 兼容，chat_json + 流式 + parse_json_loose | 重试编排 + 网关兼容技巧大全 | 双模式（真实/agent）+ 内容寻址缓存 | 流式 + 三态 thinking + 降级审计 |
| 复习调度 | FSRS-5 + 四维掌握度 + 热度 | FSRS-5 简化版（3 键） | 无 | FSRS（复刻） |
| 检索 | SiliconFlow 嵌入 + 本地哈希回退 | LIKE + 标签（无 FTS/向量） | bigram 检索注入 | 前端搜索 + 聊天记录搜索 |
| 数据安全 | CSP 等安全头 + 导出/导入 | 备份快照 + 恢复前快照 | 原子写 + 追加式决策日志 | 备份排除密钥 + 路径穿越防护 |

### 1.2 Boil 已领先的部分（不要倒退）

- SQLite WAL + 完整 review_log 状态迁移审计（与 mem 同级）
- 四维掌握度（回忆/解释/应用/迁移）+ 热度模型 —— 三个项目都没有
- 嵌入语义分组编译（mem/examgen 均无向量能力）
- 纯 stdlib + 零依赖（mem 有 Flask/requests）
- 前端零 CDN（mem 依赖 CDN）
- MCP server 已有 13 个工具

### 1.3 mem 可迁移清单（源码：`/workspace/testweb3/_work/mem/`）

| # | 功能 | 源位置 | 价值 | 批次 |
|---|---|---|---|---|
| m1 | 右键标注（选中→右键→存标注） | `memory_anchor/static/js/annotations.js:256-284` | 任务 D 的直接参考 | **P0（并入任务 D）** |
| m2 | 引文快照高亮（存 quote，渲染时高亮全部出现位置，无偏移量脆弱性） | `annotations.js:143-217` | 高 | P1（E2） |
| m3 | 复习先回忆再翻面（textarea + 「写不出来正好说明没懂」文案） | `static/js/review.js:529-548` | 高 | P1（E1） |
| m4 | ~~评分按钮显示真实下次间隔~~（Boil 已有：`srs/fsrs.py:265 preview_labels` + review.js:129-151） | `review.py:217-244` | 已具备 | — |
| m5 | 记忆口诀 AI 生成（严格任务边界 prompt：只给记忆钩子，禁止复述内容） | `app/ai.py:704-749` | 高 | P1（E3） |
| m6 | LLM 网关兼容技巧（finish_reason=length 自动 4 倍 max_tokens 重试；response_format 400 降级；「元回复」检测重试；垃圾字面量守卫） | `app/ai.py:366-514` | 高 | P1（E4） |
| m7 | 可选 AI 三态契约（未配置=入口完全隐藏不置灰；连续 2 次失败熔断） | `static/js/utils.js:460-496` | 高 | P1（E5） |
| m8 | 记忆健康度 = 全部已学卡当前可提取性均值 | `app/routes/stats.py:35-42` | 中 | P1（E6） |
| m9 | ~~365 天学习热力图~~（Boil 已有：services.py insights 的 heatmap + insights.js `heatmapHTML`） | `stats.py:72-109` | 已具备 | — |
| m10 | 错因分类标签（固定 6 类，复习时强制选择，持久化到卡+日志） | `review.js:806-856`、`utils.js:340-343` | 中 | P2（F1） |
| m11 | 复习队列侧栏（跳转/只复习选中/提前复习未到期） | `review.js:65-92, 196-309` | 中 | P2（F2） |
| m12 | 键盘复习（空格翻面、1/2/3 评分、全局快捷键） | `app.js:39-75` | 中 | P2（F3） |
| m13 | 备份恢复前先快照当前库 | `app/routes/backup.py:94-123` | 中 | P2（F4） |
| m14 | 粘贴格式清洗（网页/Word/PDF 复制的脏 markdown 归一化） | `app/markdown_fix.py` | 中 | P2（F5） |
| m15 | AI 诊断面板（网络异常分类 + HTTP 状态建议 + 掩码 key + 可复制诊断） | `app/ai.py:102-273` | 中 | P2（F6） |
| m16 | 幂等加列迁移（PRAGMA table_info + ALTER TABLE） | `app/db.py:142-165` | 已并入任务 C | — |

### 1.4 examgen 可迁移清单（源码：`/workspace/testweb3/_work/examgen/`）

examgen 是「只归纳不生成题」的知识挖掘流水线，它的**机器层**几乎全部可迁移：

| # | 功能 | 源位置 | 价值 | 批次 |
|---|---|---|---|---|
| e1 | **内容寻址 LLM 缓存**：`sha256(skill+model+messages)` 为键，重复调用零成本确定性重放 | `src/llm/agentProvider.js` | 极高（编译重跑免费） | P1（E7） |
| e2 | JSON 宽松解析（括号匹配提取 + 截断补全猜测 + 二次「修正器」LLM 调用） | `src/llm/index.js:60-131` | 高（Boil 已有 parse_json_loose，可补截断补全+修正器） | P1（并入 E4） |
| e3 | 代码强制决策层（模型提方案，阈值判断在代码里，冲突记录进日志） | `src/skills/essenceMatch.js:64-140` | 高 | P2（F7，单元合并） |
| e4 | LLM-as-judge 三维评审 + 证据注入 + 可执行补丁 + 无操作跳过 | `src/skills/qa.js:13-44`、`src/pipeline/orchestrator.js:292-384` | 高 | P2（F8，内容质检） |
| e5 | 确定性优先、LLM 兜底（bigram 预匹配 ≥0.45 先解决大部分，残差才进模型） | `src/skills/qa.js:47-55` | 高 | P2（F8） |
| e6 | 逐字锚点材料修复（LLM 给 8-30 字锚点，代码做切片手术） | `src/ingest/repair.js` | 中 | P2（F9） |
| e7 | merged-chain 合并语义（合并不删除，`merged_into` 链 + UI 横幅跳转） | `src/store/library.js`、`web/app.js` §12 | 中 | P2（F7） |
| e8 | 传输层健壮性（重试状态码集 + 指数退避 + AbortController 超时 + json_object 400 降级） | `src/llm/openai.js:53-95` | 高（并入 E4） | — |
| e9 | 检索式知识注入（只注入 top-5 讲义片段 + top-4 公式，不整章粘贴） | `src/ingest/markdown.js:49-77` | 中 | P2（F10） |
| e10 | 输出前校验器（id 集合相等、长度下限、数字污染检测） | `tools/validateAnswers.js` | 中 | P2（F8） |
| e11 | 真实数据锚点浏览器测试（断言从 API 现场取，不用 fixture） | `tools/browserCheck.js` | 已是 Boil 做法 | — |

### 1.5 testlearnweb 可迁移清单（源码：`/workspace/testweb3/_work/testlearnweb/`）

**重要澄清**：testlearnweb 没有真正的 `contextmenu` 右键事件；它的「右键菜单」其实是**划词工具条**（selectionchange 时浮出 6 个动作：标记/笔记/记忆/追问/批注/复制）。真正的右键标注在 mem 里（m1）。任务 D 会两者都做：**划词浮条为主（testlearn 交互）+ 右键菜单为辅（mem 交互）**。

| # | 功能 | 源位置 | 价值 | 批次 |
|---|---|---|---|---|
| t1 | 划词工具条（selectionchange 浮条 + 定位钳制 + 翻转 + 隐藏时机） | `frontend/_next/static/chunks/4142b3f3c7ba4160.js` 字节 22582-33000；文档 `HANDOVER.md` §9.17 (L998-1046) | 极高（任务 D 核心） | **P0（任务 D）** |
| t2 | AI 提炼记忆卡：预览→确认→入库（绝不静默保存） | `localbackend/handlers_learn.py:513-570` | 高 | P2（F11） |
| t3 | 引文快照 + 前后缀消歧的高亮重锚定 | 同 chunk 字节 69210 | 高 | P2（F12，与 m2 二选一先做 m2） |
| t4 | engine: "model"/"rules" 诚实标签 + llm_errors 贯穿 | `localbackend/agent.py` | 高（并入任务 C 原则） | — |
| t5 | LLM 网关：始终流式请求上游再拼回非流式形状（防空闲网关 504）；空内容 200 视为错误；thinking 三态与降级标记 | `localbackend/llm.py`、`ARCHITECTURE.md` §5 | 高 | P2（F13） |
| t6 | 追问侧线程（带 ±800 字符锚点上下文） | `localbackend/stream.py:164-241` | 中 | P2（F14） |
| t7 | SSE 重连（request_id + last_seq 缓冲重放，最多 2 次退避） | `localbackend/server.py`、BACKEND.md §三 | 中 | P2（F15） |
| t8 | 学习风格测试→人设→教学策略 | `engine.py` STYLE_Q、`agent.ai_persona` | 中 | P2（F16） |
| t9 | 学习计划 5 步流 + 软确认 | `engine.py`、PROMPTS.md §三 | 中（任务 B 已覆盖核心） | — |
| t10 | 番茄钟学习循环 | 前端 chunk studyCycle | 低 | P3 |
| t11 | 记忆差异化 UI（本轮改了什么 + 撤销） | `memory/{tid}/diff-state` | 低 | P3 |
| t12 | 移动端划词劫持（touch 后包 span 去原生菜单） | 同 t1 chunk | 低 | P3 |

### 1.6 明确不迁移的（避免执行模型跑偏）

- mem 的 Flask/CDN 技术栈、SiYuan 插件（Boil 不做外部笔记软件耦合）
- examgen 的题库业务层（它没有学习者侧）；JSONL 换掉 SQLite（Boil 的 SQLite 更好）
- testlearnweb 的 React 前端、邮件 OTP/JWT 认证、OSS 上传、计费/推荐（本地单用户不需要）
- 任何需要引入依赖才能实现的功能

---

## 2. 任务总览

| 任务 | 名称 | 优先级 | 规模 | 依赖 |
|---|---|---|---|---|
| A | 导入界面 AI 即时生成标题/学科/目标名 | P0 | 小（后端 1 函数 + 1 路由；前端 1 按钮） | 无 |
| B | 编译时 AI 生成大纲 + 阶段计划 + 材料总览 | P0 | 中（prompt + compiler + planner + step3 UI） | 无 |
| C | 全站内容 AI 合成 + engine 诚实标签 | P0 | 中（angle 列迁移 + 仪表盘/单元/洞察三处呈现） | B |
| D | 划词灵感笔记（选择浮条 + 右键菜单 + 灵感库页） | P0 | 大（新表 + 3 路由 + 新前端模块 + 7 个视图挂 data-selectable） | 无 |
| E | P1 迁移批次（回忆优先、口诀 AI、网关加固、缓存、健康度、AI 熔断、高亮重现） | P1 | ✅ 已完成 | A-D 之后按序 |
| F | P2 迁移批次（错因标签、队列侧栏、键盘、快照、粘贴清洗、诊断、合并链、质检、锚点、检索注入、做卡片、重连、风格测试） | P2 | ✅ 已完成（F12 可选未做） | E 之后 |

---

## 3. 任务 A：导入界面 AI 即时生成元信息

**用户需求**：在导入教材界面（onboarding step2 材料模式），AI 立即生成材料标题、学科、目标名称等信息——不用等编译。

### 3.1 现状

- 表单字段在 [static/js/views/onboarding.js:185-201](file:///workspace/testweb3/boil/static/js/views/onboarding.js#L185)：`#ob-name`（称呼）、`#ob-title`（目标名称）、`#ob-material-title`（材料标题）、`#ob-subject`（学科）、`#ob-drop`/`#ob-files`（文件）、`#ob-text`（粘贴文本）
- 目前只有本地规则回填：加文件后取第一个文件名填材料标题（onboarding.js:271-274）
- 编译接口 `POST /api/packs/compile`（server.py:61）→ `services.compile_preview`（services.py:808）→ `merge_material_documents`（services.py:778）
- LLM 工具：`llm.enabled()`（llm.py:172）、`llm.chat_json(messages, model, temperature, max_tokens)`（llm.py:489）、`llm.load_prompt(name)`（llm.py:496，读 `prompts/` 目录）

### 3.2 后端改动

**新增 `boil/prompts/meta_suggest.md`**（注意：`{text}` 占位符会被 replace）：

```markdown
下面是学习材料的开头部分。请分析后给出元信息。

材料：
---
{text}
---

只输出一个 JSON 对象，不要输出其它任何内容：
{
  "title": "材料标题，8-20 字，概括材料主题，不要书名号",
  "subject": "学科或主题，2-8 字，例如：高等数学 / 认知心理学",
  "goal_title": "适合作为学习目标名称的短语，8-20 字，动词开头，例如：掌握导数与微分",
  "description": "一句话（30 字以内）概括这份材料最值得学的是什么"
}
```

**`boil/services.py` 新增函数**（放在 `merge_material_documents` 之后）：

```python
def suggest_meta(text: str, documents: list[Any] | None = None) -> dict[str, Any]:
    """材料元信息即时生成：优先真实模型，失败回退本地规则。"""
    merged, docs = merge_material_documents(documents, text)
    if len(merged) < 60:
        raise ValueError("材料太短，至少 60 个字符")
    sample = merged[:6000]
    if llm.enabled():
        try:
            prompt = llm.load_prompt("meta_suggest.md").replace("{text}", sample)
            data = llm.chat_json([
                {"role": "system", "content": "你是学习材料的元信息提取器。只输出合法 JSON。"},
                {"role": "user", "content": prompt},
            ], model=llm.model_name(), temperature=0.3, max_tokens=500)
            def _s(v, limit):
                return re.sub(r"\s+", " ", str(v or "")).strip()[:limit]
            title = _s(data.get("title"), 40)
            if title:
                return {"title": title, "subject": _s(data.get("subject"), 20),
                        "goal_title": _s(data.get("goal_title"), 40),
                        "description": _s(data.get("description"), 80), "source": "model"}
        except Exception:
            pass
    # 本地规则回退：取第一个 markdown 标题或首句
    m = re.search(r"^#{1,4}\s+(.+)$", merged, re.M)
    first_line = (m.group(1) if m else merged.splitlines()[0]).strip()
    title = re.sub(r"^[\s>#*•\-]+", "", first_line)[:28] or "我的学习材料"
    return {"title": title, "subject": "", "goal_title": f"掌握{title[:16]}",
            "description": "", "source": "rules"}
```

**`boil/server.py`**：ROUTES 表加一行（放在 compile 旁边）：

```python
("POST", re.compile(r"^/api/packs/suggest$"), self.pack_suggest),
```

handler（**签名照抄 server.py:169 的 `compile_pack`：`(self, req, match, query, body)`，body 已被框架解析为 dict，校验失败抛 `ApiError(400, ...)`**）：

```python
def pack_suggest(self, req, match, query, body):
    if not isinstance(body, dict):
        raise ApiError(400, "请求体必须是 JSON 对象", "bad_request")
    text = str(body.get("text") or "")
    docs = body.get("documents")
    if docs is not None and not isinstance(docs, list):
        raise ApiError(400, "documents 必须是数组", "bad_request")
    return services.suggest_meta(text, docs)
```

（先读 server.py 里 `compile_pack` 的实际实现，模仿它的 body 读取与错误处理结构，上面的代码是示意。）

### 3.3 前端改动（onboarding.js renderStep2 的 material 分支）

1. 在「材料标题」label 所在 field 里加一个按钮（用已有的 `icon("wand", 14)` 图标）：

```html
<div class="field full">
  <label>材料标题 <button class="btn btn-ghost btn-xs" id="ob-suggest" type="button" title="AI 分析材料并生成标题、学科、目标名">✦ AI 生成信息</button></label>
  <input class="input" id="ob-material-title" ...>
</div>
```

（不用 emoji 的话直接文案「AI 生成信息」+ `icon("wand", 13)`；`btn-xs` 样式如果不存在就在 app.css 加一个小号变体。）

2. 绑定事件（在 renderStep2 的 material 分支里，与 drop 事件并列）：

```js
body.querySelector("#ob-suggest")?.addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  const pasted = body.querySelector("#ob-text").value.trim();
  if (!files.length && pasted.length < 60) return toast("先上传文件或粘贴至少 60 字文本", "error");
  btn.disabled = true; const old = btn.innerHTML; btn.innerHTML = "分析中…";
  try {
    const res = await ctx.api.post("/api/packs/suggest", {
      text: pasted, documents: files.map(f => ({ name: f.name, text: f.text })),
    });
    const fill = (sel, val) => { const el2 = body.querySelector(sel); if (el2 && val && !el2.value.trim()) el2.value = val; };
    fill("#ob-material-title", res.title); fill("#ob-subject", res.subject); fill("#ob-title", res.goal_title);
    const hint2 = body.querySelector("#ob-suggest-hint");
    if (hint2) hint2.textContent = res.description || "";
    toast(res.source === "model" ? "AI 已生成，可直接修改" : "未配置模型，已用本地规则生成", res.source === "model" ? "success" : "info");
  } catch (err) {
    toast(err.message || "生成失败", "error");
  } finally { btn.disabled = false; btn.innerHTML = old; }
});
```

3. 在 file-drop 下方 hint 附近加 `<div class="hint" id="ob-suggest-hint"></div>` 用来显示 description。
4. **空值才填**（上面 `fill` 的逻辑）：用户已手写的不要覆盖。

### 3.4 验收标准

1. `python -m unittest discover -s tests -v` 全绿；在 `tests/test_api.py` 加 `test_11_suggest_meta`：
   - `POST /api/packs/suggest` body `{"text": "（60 字以上的任意文本）"}` → 200，返回字段含 title/subject/goal_title/source，演示模式下 `source == "rules"`；
   - body `{"text": "短"}` → 400。
2. 起服务后打开 `#/onboarding` → 选「用我的材料创建」→ 粘贴一段文本 → 点「AI 生成信息」→ 材料/学科/目标名空字段被填上，出现 toast。
3. `make check-js` 通过。

### 3.5 已知坑

- `merge_material_documents` 会做 200k 上限校验和 `## 文件名` 合并——直接复用，别重写。
- api.js 的 `post` 默认 90s 超时，足够。
- 不要自动触发（不要 debounce 自动调用）：显式按钮可控且省 token，这是 v1 决策。

**✅ 完成于 2026-10-21**：`prompts/meta_suggest.md` + `services.suggest_meta` + `POST /api/packs/suggest` + onboarding「AI 生成信息」按钮全部落地。真实模型实测 1.1s 返回且 `source` 为 `model`，标题/学科/目标名正确回填，已填字段不被覆盖。测试：`test_11_suggest_meta`（rules 路径）+ `AiPathTest.test_suggest_meta_model_path` / `test_suggest_meta_falls_back_when_model_fails`（model 路径与异常回退）。

---

## 4. 任务 B：编译时 AI 生成大纲与计划

**用户需求**：编译时 AI 生成大纲（这份材料的学习路线结构）和计划（阶段划分），而不只是单元列表。

### 4.1 现状

- 编译主链：`compile_material`（compiler.py:283）→ prompt `prompts/compiler_extract.md` → `llm.chat_json` → `_normalize_llm_units`（compiler.py:209）→ `normalize_pack`（packs.py:209，`dict(pack)` 所以**新增顶层字段会原样保留**）
- 计划生成：`planner.build_plan(units, relations, daily_minutes, start, deadline, goal_id, version)`（planner.py:94），目前是纯本地拓扑排序 + 时间装箱，无 LLM
- onboarding 创建目标：services.py:97-166，`build_plan` 调用在 services.py:157
- 编译预览页 step3：onboarding.js:360-390，只显示单元标题列表
- `compile_preview`（services.py:808）返回 `{pack, warnings}`，pack 是完整 dict

### 4.2 后端改动

**1. 修改 `boil/prompts/compiler_extract.md`**：在要求的 JSON 输出结构里（保持现有 units 数组不变）**新增三个顶层字段**：

```
"outline": [ {"title": "第一部分标题", "summary": "一句话说明这部分为什么排在前面", "unit_titles": ["对应单元标题1", "单元标题2"]} ],
"study_plan": [ {"phase": "阶段名（如：地基→框架→精装修）", "unit_titles": [...], "focus": "这个阶段的核心目标，20 字内", "minutes": 240} ],
"digest": {"overview": "150 字以内的材料总览：这份材料讲什么、主线是什么、最适合怎么学", "advice": ["学习建议，3-5 条，每条 30 字内"]}
```

prompt 里同时要求：outline 覆盖全部单元、顺序即推荐学习顺序；study_plan 的 minutes 总和 ≈ 全部单元 estimated_minutes 之和。

**2. `boil/compiler.py`**：
- `_normalize_llm_units`：从 `data` 读取三个新字段并清洗（`outline` 每项 `{title, summary, unit_titles[]}`，cap 12 项；`study_plan` 每项 `{phase, unit_titles[], focus, minutes}`，cap 8 项；`digest.overview` ≤ 400 字、`advice` ≤ 5 条）。**关键**：`unit_titles` 与实际 units 的 title 不要求严格一致（模型可能微调措辞），但要尽力对应；清洗时把找不到对应的 unit_titles 条目原样保留（仅作展示）。挂到 pack 顶层 `outline` / `study_plan` / `digest`。
- `compile_heuristic`：本地路径也生成同构字段（outline = 单元顺序 + essence 首句做 summary；study_plan = 按每 3 个单元一个阶段；digest.overview = 前 2 个单元 essence 拼接），并加 `"source": "rules"` 标记；LLM 路径加 `"source": "model"`。这样两条路径输出形状一致。

**3. `boil/planner.py` `build_plan` 加可选参数** `ai_outline: list[dict] | None = None`：
- 若提供且非空：按 `ai_outline` 里的 unit_titles 顺序重排 units（找不到的排最后），阶段（phase）信息写进 plan 的 day 条目 `note` 字段或 plan 顶层 `phases`；
- 否则维持现有拓扑排序逻辑（**不要删现有逻辑**，它处理 relations 依赖）。
- plan dict 顶层加 `outline`（透传）与 `digest`（透传），`today_plan`/`progress` 不受影响。

**4. `boil/services.py` `onboarding`**（services.py:157 附近）：
- `planner.build_plan(..., ai_outline=pack.get("outline"))`；
- goal 的 `meta_json` 追加 `"ai_digest": pack.get("digest") or {}` 和 `"ai_outline": pack.get("outline") or []`（注意 meta_json 现在已有 warnings/pack_name/material 三个键，合并而不是覆盖）。

**5. `boil/services.py` `compile_preview`**：返回体加 `"outline": pack.get("outline") or []`、`"digest": pack.get("digest") or {}`（pack 里已有，这里是显式透传给前端用）。

**6. 安装持久化核对**：确认 `install_pack` 把 pack 存进 `packs.manifest_json` 后，`dashboard`/`unit_detail` 能取回 outline/digest。若 manifest_json 只存部分字段，把 outline/digest 一并存入（读 `boil/packs.py` 的 install 相关代码后决定）。目标 meta_json 里的 ai_digest 是仪表盘的主要数据源。

### 4.3 前端改动（onboarding.js renderStep3）

step3 预览升级为三块（在现有单元列表上方）：

1. **材料总览卡**：`digest.overview` 文本 + `digest.advice` 列表 + 角标（`source === "model"` 显示「AI 分析」badge，否则「本地规则」badge-teal/muted）。
2. **学习大纲**：outline 逐条显示 `title + summary`，右侧对应单元数 badge。
3. **阶段计划**：study_plan 逐条 `phase + focus + X 单元 · Y 分钟`。
4. 原单元列表保留在大纲下方（可折叠 `<details open>`）。

数据从 `materialPack.digest` / `materialPack.outline` / `materialPack.study_plan` 直接读（compile 响应的 pack 里就有）。

### 4.4 验收标准

1. 新测试 `test_12_compile_outline_plan`（tests/test_api.py）：
   - 演示模式 `POST /api/packs/compile` → pack 含非空 `outline`、`study_plan`、`digest.overview`，且 `digest.source == "rules"`；
   - `planner.build_plan(units, [], ai_outline=[{...倒序 unit_titles...}])` 返回的 plan 中单元顺序遵循 outline（写进 tests/test_planner.py 或就近）。
2. 浏览器：onboarding 材料模式编译后 step3 显示总览 + 大纲 + 阶段三块。
3. 创建目标后 `GET /api/dashboard` 响应里能拿到 goal 的 ai_digest（任务 C 会用到）。

### 4.5 已知坑

- `normalize_pack` 是 `dict(pack)` 起手（packs.py:210），顶层新字段会保留；但**units 的每项是白名单式 setdefault**，往 unit 上加字段要显式加（任务 C 的 angle 就是这种情况）。
- LLM 一次调用输出变长（8000 max_tokens 可能不够装 outline+plan）：把 `compile_material` 里的 `max_tokens` 提到 12000；若仍截断，parse_json_loose 的截断补全会兜底。
- study_plan 的 minutes 只是建议值，**真实排期仍由 planner 按每日分钟数计算**——AI 只决定顺序与阶段叙事，不做日期算术（这是 examgen「代码强制决策」原则：LLM 提方案，代码做计算）。

**✅ 完成于 2026-10-21**：`compiler_extract.md` 增加 `outline`/`study_plan`/`digest` 三个顶层字段；`compiler._normalize_plan_fields`（模型路径，带标题漂移容错）与 `_fallback_plan_fields`（本地路径，同构形状）两条路径输出一致；`planner.build_plan(ai_outline=…)` 只重写 `order_index`，前置依赖仍由拓扑排序裁决；goal meta 落 `ai_digest`/`ai_outline`/`ai_study_plan`/`ai_generated_by`；`compile_material` 的 `max_tokens` 提到 12000；step3 渲染总览+大纲+阶段+折叠单元清单。真实模型实测：5 条大纲、4 个阶段（基础原理→时间管理→进阶策略→生理支持）、badge「AI 分析」。测试：`test_12_compile_outline_plan` + 3 个 planner 排序测试 + 2 个 compiler 字段测试 + `AiPathTest.test_compile_material_model_path_carries_plan_fields`。

---

## 5. 任务 C：全站内容 AI 合成 + engine 诚实标签

**用户需求**：每一个呈现给用户的内容都必须是 AI 在分析完所有素材后生成的。

**落地解释**（与现状的差距）：单元内容已走 LLM 编译；缺的是（1）材料级的综合呈现（仪表盘总览），（2）单元级的「怎么学」导读，（3）洞察页的 AI 解读。同时立下「engine 标签」原则：所有 AI 生成内容都要标注来源，规则回退不冒充 AI。

### 5.1 现状

- dashboard（services.py:213）返回 goal（含 meta_json）+ advisor + next_units 等；前端 dashboard.js 渲染
- unit_detail（services.py:456）：units 表**没有 angle 列**（db.py:76-99），单元页无导读
- insights（services.py:645）：纯本地统计
- db.py 无迁移机制：`SCHEMA_SQL` + `CREATE TABLE IF NOT EXISTS`，旧库不会自动加列 → 需要引入幂等加列

### 5.2 数据库改动（boil/db.py）

1. `SCHEMA_SQL` 的 units 表加一列：`angle TEXT NOT NULL DEFAULT ''`（单元导读：AI 建议这个单元怎么学）。
2. 新增幂等迁移函数并在 `init_db()` 末尾调用（mem 的模式，db.py 参考 `_ensure_columns` 思路）：

```python
def _ensure_column(db, table: str, column: str, ddl: str) -> None:
    cols = {row[1] for row in db.query(f"PRAGMA table_info({table})")}
    if column not in cols:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
```

调用：`_ensure_column(db, "units", "angle", "angle TEXT NOT NULL DEFAULT ''")`。（先看 db.py 里 Database 类的实际方法名：`execute`/`query`，照现有调用方式写。）

3. `install_pack` 写 units 时带上 `angle`（packs.py 的 install 循环里加一列）；`unit_detail` 的 SELECT 加 angle；`_unit_card`（services.py:322）可带 angle 摘要（不必须）。

### 5.3 编译侧改动

- `prompts/compiler_extract.md` 的**每个 unit** 的 schema 加：`"angle": "给学习者的一句话导读：这个单元该怎么学、和材料主线的关系，30 字内"`。
- `compiler.py` `_normalize_llm_units`：`u["angle"] = str(raw.get("angle") or "")[:80]`；`compile_heuristic` 的 `_heuristic_unit`：angle = `f"先抓住{title[:12]}的本质，再验证边界。"`（规则回退）。
- packs.py `normalize_pack` 的 unit 白名单里加 `u.setdefault("angle", "")`。

### 5.4 三个呈现点

**1. 仪表盘「材料总览」卡（dashboard.js）**
- `services.dashboard`：goal 的 meta_json 解出 `ai_digest`，放进返回体 `digest`（含 overview/advice/source）；没有则 `{"overview": "", "source": "rules"}`。
- dashboard.js 在 advisor 卡下方渲染 digest 卡：标题「材料总览」+ source badge（AI 分析 / 本地规则）+ overview 段落 + advice 列表。空 overview 时整卡不渲染。

**2. 单元页「怎么学」导读（unit.js）**
- `unit_detail` 返回 unit.angle；unit.js 在 essence 区块顶部渲染一行带 spark 图标的导读（无则不渲染）。

**3. 洞察页「AI 解读」（insights.js）**
- 新路由 `("POST", re.compile(r"^/api/insights/synthesize$"), self.insights_synthesize)`。
- `services.insights_synthesize(db, user_id, goal_id)`：
  - 收集输入：`insights(db, user_id, goal_id)` 的统计（各 facet 均值、due 数、完成度）+ goal 的 ai_digest.overview + 最弱 3 个单元标题；
  - `llm.chat_json`（新 prompt `prompts/insights_synth.md`，含 `{stats}` `{overview}` `{weak_units}` 占位符）→ `{"summary": "80 字内本周综合解读", "suggestions": ["3 条可执行建议"], "risks": ["2 条风险提醒"]}`；
  - 回退规则：summary = 弱 facet 模板句，suggestions = 固定 3 条（复习到期卡/补最弱单元/做一次费曼），`source: "rules"`；
  - 结果不落库（或写 events 表 type=`insight.synthesized` 便于审计），直接返回。
- insights.js 顶部加「AI 解读」按钮（配置了 LLM 才显示，参考 m7 的 aiEnabled 判定：`ctx.state.settings`？看 settings 视图怎么读 llm 状态，`/api/settings/llm` 有 `mode`），点击后渲染三块内容。

### 5.5 engine 标签总原则（贯穿验收）

- 任何 AI/规则双路径输出，响应里必须有 `source: "model" | "rules"`；
- 前端渲染对应 badge：`badge-teal「AI 分析」` / `badge-muted「本地规则」`；
- LLM 失败回退时把原因写进 warnings（现有模式）。

### 5.6 验收标准

1. `test_13_ai_synthesis`：旧库迁移幂等（建库→手工 DROP COLUMN 不行，直接测二次 init 不炸）；compile 出的 pack units 每个有 angle；`GET /api/dashboard` 有 digest 字段；`POST /api/insights/synthesize` 演示模式返回 source=="rules" 的三字段。
2. 浏览器：仪表盘见「材料总览」卡（带 AI/规则 badge）；单元页见导读行；洞察页点「AI 解读」出内容。
3. 已有 49 测试不回归。

### 5.6 已知坑

- **不要**把 advisor（本地建议阶梯）改成 LLM——它是确定性安全网；AI 内容是增量卡片，不是替换。
- insights_synthesize 的 stats JSON 序列化后塞 prompt，控制在 2000 字内（只传数字摘要）。
- 洞察 AI 调用可能 20-60s：前端按钮要有 loading 态，api.post 超时传 `{timeout: 120000}`。

**✅ 完成于 2026-10-21**：`db.py` 新增 `EXTRA_COLUMNS` + 幂等加列（`units.angle`，`SCHEMA_VERSION` 升到 4），旧库启动自动补列并有回归测试；编译两条路径都产出 angle；仪表盘「材料总览」卡、单元页 angle 导读、洞察页「生成解读」（`POST /api/insights/synthesize` + `prompts/insights_synth.md`）全部带 AI/规则 badge。真实模型实测：解读同时引用材料主线与实际数据（「本周未开始学习，5 个单元均未接触，掌握度与热度均为 0。材料涵盖提取练习、合意困难等…」）。测试：`test_13_ai_synthesis_surfaces` + 2 个 migration 测试 + `AiPathTest.test_insights_synthesize_model_path`。

**执行时修正**：`json_dumps()` 不接受 `ensure_ascii` 参数（它内部已固定 `ensure_ascii=False`）。这个 bug 在 demo 模式下测不出来——是「降级必须带 warning」的纪律把它暴露在 UI 上的。因此补了 `AiPathTest`：**所有 AI 功能都要有用例打桩 `llm.enabled` / `llm.chat_json` 覆盖 model 分支**，否则异常会被回退静默吞掉。

---

## 6. 任务 D：划词灵感笔记（选择浮条 + 右键菜单）

**用户需求**：在每一个可能复制的界面添加右键菜单，随时把灵感存入笔记。参考 testlearn 的划词工具条 + mem 的右键标注。

### 6.1 现状

- 现有笔记仅限单元页两种固定 kind（速记/我的笔记，unit_notes 表，db.py:250，UNIQUE(user_id,unit_id,kind)）——**不适合跨页面自由灵感**，需要新表
- 前端无任何 selection/contextmenu 处理；utils.js 有 toast/escapeHTML/icon；app.js 有视图挂载点
- 复制面（要挂 data-selectable 的地方）：unit.js 内容区、socratic.js 消息气泡、feynman.js 报告、insights.js、dashboard.js digest 卡、map.js、review.js 卡片正反面、library.js

### 6.2 数据库（boil/db.py SCHEMA_SQL 追加）

```sql
CREATE TABLE IF NOT EXISTS notes (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  goal_id TEXT,
  unit_id TEXT,
  kind TEXT NOT NULL DEFAULT 'inspiration',
  quote TEXT NOT NULL DEFAULT '',
  content TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_user ON notes(user_id, created_at DESC);
```

kind 固定 `'inspiration'`（v1 只有一种；将来扩展 highlight/annotation）。

### 6.3 后端（boil/services.py + server.py）

```python
def list_notes(db, user_id, unit_id=None, q=None, limit=200):
    # SELECT * FROM notes WHERE user_id=? [AND unit_id=?] [AND (quote LIKE ? OR content LIKE ?)] ORDER BY created_at DESC LIMIT ?
def create_note(db, user_id, payload):
    # quote=strip[:2000], content=strip[:4000], unit_id/goal_id 可选, source=strip[:100]
    # 校验：quote 与 content 至少一个非空，否则 ValueError("引用和笔记至少填一个")
def delete_note(db, user_id, note_id):  # 删不存在返回 False
```

路由三条（server.py ROUTES）。**注意：本服务只实现了 `do_GET`/`do_POST`（server.py:362-366），没有 `do_DELETE`——删除用 POST 动作端点，与现有 `/api/reset` 风格一致**。handler 签名统一是 `(self, req, match, query, body)`（照抄 server.py:169 `compile_pack`），校验失败抛 `ApiError`：

```
("GET",  re.compile(r"^/api/notes$"), self.notes),
("POST", re.compile(r"^/api/notes$"), self.note_create),
("POST", re.compile(r"^/api/notes/delete$"), self.note_delete),   # body: {"id": "..."}
```

### 6.4 前端核心：新模块 `static/js/selectionMenu.js`

```js
/* 全局划词菜单：selectionchange 浮条 + contextmenu 右键，选中 [data-selectable] 内文本时出现。 */
import { escapeHTML, toast } from "./utils.js";

export function initSelectionMenu(ctx) { /* 幂等：window.__boilSelMenu 已存在则返回 */ }
```

实现要点（顺序照做）：

1. **触发条件**（selectionchange 回调，全文档只挂一次）：
   - `sel = window.getSelection()`；`sel.isCollapsed || !sel.toString().trim()` → 隐藏返回；
   - 文本长度 4–2000 之外 → 隐藏；
   - `range = sel.getRangeAt(0)`；`container = (range.commonAncestorContainer.nodeType===1 ? range.commonAncestorContainer : range.commonAncestorContainer.parentElement)?.closest("[data-selectable]")`；无 → 隐藏；
   - 记录 `state = {text: sel.toString(), unitId: container.dataset.unitId || "", source: container.dataset.source || container.closest("[data-view]")?.dataset.view || ""}`。
2. **定位**：`rect = range.getBoundingClientRect()`；菜单 `position: fixed`，`left = clamp(rect.left + rect.width/2 - 菜单宽/2, 8, innerWidth-菜单宽-8)`；`top = rect.top > 72 ? rect.top - 44 : rect.bottom + 8`（顶栏 64px，贴顶翻转）。
3. **菜单 DOM**（单例，append 到 body，`mousedown` 上 `e.preventDefault()` 保住选区）：

```
[存为灵感] [复制]   ← v1 两个按钮；按钮样式 class="sel-menu-btn"
```

4. **右键菜单**（用户点名要的交互）：`document.addEventListener("contextmenu", e => { if (当前有活跃选区 && 选区文本包含点击目标) { e.preventDefault(); 在 e.clientX/e.clientY 处显示同一菜单; } })`。判断「选区包含点击目标」：`sel.rangeCount && sel.getRangeAt(0).intersectsNode(e.target)` 或简单用 `sel.containsNode(e.target, true)`。
5. **隐藏时机**：scroll（>10px）、mousedown 在菜单外、Escape、selectionchange 变空。全部用 `hideMenu()`，注意右键菜单打开后 300ms 内忽略这些隐藏事件（避免刚打开就被 contextmenu 的默认逻辑关掉）。
6. **复制**：`navigator.clipboard.writeText(state.text)` → toast「已复制」→ hide。
7. **存为灵感**：hide 浮条 → 在选区附近弹**保存气泡**（一个 fixed 小卡片，含：引用预览（`escapeHTML(state.text)` 截 200 字、只读样式）、textarea「你的灵感 / 备注」（autofocus）、保存/取消按钮）。保存 → `ctx.api.post("/api/notes", { quote: state.text, content: textarea值, unit_id: state.unitId || undefined, source: state.source })` → toast「已存入灵感库」；失败 toast 错误。Enter=保存（Shift+Enter 换行）、Escape=取消。
8. **app.js 接入**：`import { initSelectionMenu } from "./selectionMenu.js"`，在 boot 成功后 `initSelectionMenu(ctx)`（ctx 需要 api + toast；看 app.js 怎么构造传给视图的 ctx，391 行附近有 navigate 等，照做）。

### 6.5 各视图挂 data-selectable

| 视图 | 挂载点 | 附加 data-* |
|---|---|---|
| unit.js | essence/steps/boundaries/misconceptions/faq 内容容器 | `data-selectable data-unit-id="${u.id}" data-source="unit"` |
| socratic.js | 每条消息气泡（AI 与用户都挂） | `data-selectable data-unit-id="${unitId}" data-source="socratic"` |
| feynman.js | 评估报告容器 | `data-selectable data-unit-id="${unitId}" data-source="feynman"` |
| insights.js | 统计卡与 AI 解读区 | `data-selectable data-source="insights"` |
| dashboard.js | digest 总览卡 + advisor 卡 | `data-selectable data-source="dashboard"` |
| map.js | 地图节点说明区 | `data-selectable data-source="map"` |
| review.js | 卡片正反面容器 | `data-selectable data-unit-id="${unitId}" data-source="review"` |
| library.js | 预览内容区 | `data-selectable data-source="library"` |

原则：挂在**文本内容的最外层展示容器**上（不是整个页面壳），输入框/按钮区不挂。

### 6.6 新视图：灵感库 `static/js/views/notes.js`

- 路由 `#/notes`，app.js：import + 注册到路由表（看 app.js 里 `#/insights` 等怎么注册，照抄一份）；`navItems()`（app.js:111）加 `{key:"notes", label:"灵感库", icon:"spark"}`（icon 用已有的 `spark` 或 `edit`）。
- 页面：标题 + 统计（N 条）+ 筛选（全部 / 按单元下拉 / 搜索框 debounce 300ms）+ 笔记列表。每条卡片：引用块（左侧竖线样式 `.note-quote`）+ 笔记内容 + 元信息（来源视图 · 所属单元标题 · 相对时间）+ 删除按钮（确认后 `POST /api/notes/delete` body `{"id": ...}`）。
- 空状态文案：「在任何学习页面划选一段文字，点“存为灵感”，它就会出现在这里。」
- 导出按钮：前端拼 markdown（`## 引用\n> quote\n\ncontent\n`）用 `URL.createObjectURL(new Blob(...))` 下载 `boil-notes.md`。

### 6.7 CSS（static/css/app.css 追加）

`.selection-menu`（fixed、z-index 高于顶栏、圆角、阴影、flex、backdrop-blur，风格对齐 `.settings-jump`/现有卡片）、`.sel-menu-btn`、`.note-popover`、`.note-quote`（border-left 3px + 灰底斜体）、`.note-card`、移动端宽度适配（max-width: min(320px, calc(100vw - 24px))）。

### 6.8 验收标准

1. `test_14_notes_api`：POST 建 note（带/不带 unit_id）→ GET 列表过滤 unit_id/q → `POST /api/notes/delete` 后 GET 不再出现；quote/content 全空 POST → 400。
2. 浏览器（tools/browser_check.py 加一条或手工 agent-browser）：
   - 打开单元页 → 通过 JS `Range`+`Selection` API 选中 essence 段落部分文本（参考 testlearn 的 `probe_selection_crash.py` 的 TreeWalker 选法，或简单 `document.createRange().selectNodeContents(el)` 后 `sel.removeAllRanges(); sel.addRange(range)`）→ 断言 `.selection-menu` 可见 → 点击「存为灵感」→ 填内容保存 → 导航 `#/notes` → 断言新笔记出现且引用文本匹配。
   - 右键：构造选区后在选区中心 `dispatchEvent(new MouseEvent("contextmenu", {bubbles:true, cancelable:true, clientX, clientY}))` → 断言菜单出现在光标处且 `preventDefault` 生效（原生菜单不出）。
3. Escape / 点击空白 / 滚动 都能关闭菜单；输入框内选择文本不触发（input/textarea 在 [data-selectable] 之外或显式排除：触发条件里加 `container.tagName === "INPUT" || "TEXTAREA"` 时跳过——实际 closest 会找到容器，需再查 `sel.anchorNode` 所在元素是否 INPUT/TEXTAREA，是则隐藏）。
4. 已有回归全绿（49+ 新增）。

### 6.9 已知坑

- **事件命名冲突**：socratic.js 若有全局 selectionchange 逻辑（无，已确认），无冲突。
- 移动端 iOS 长按选择：selectionchange 也能触发浮条；右键菜单在触屏不可用（可接受，浮条覆盖主要路径）。
- KaTeX 公式内选择：v1 不特殊处理（testlearn 的 formula 锚定是 F12/批注功能，属 P2 的 F12）。
- 保存气泡的 textarea 里 Enter 触发保存时要防 IME 组合输入：`if (e.isComposing) return`。

**✅ 完成于 2026-10-21**：`notes` 表 + `list_notes`/`create_note`/`delete_note`/`export_notes_markdown` + 三条路由 + `static/js/selectionMenu.js`（划词浮条 + 右键菜单 + 保存气泡）+ 8 个视图挂 `data-selectable`（unit/socratic/feynman/review/insights/dashboard/map/library）+ `#/notes` 灵感库（搜索 debounce、按单元筛选、确认后删除、导出 Markdown、空状态引导）+ 侧边栏与移动导航入口。浏览器实测：划词浮条出现、右键菜单在选区内弹出、保存后落库并带 `source=unit` 与 unit_title、搜索命中正文、删除后列表与服务端同步、控制台零报错。测试：`test_15_inspiration_notes` + 2 个 ServiceTest 用例；`tools/browser_check.py` 新增 3 张截图与 5 组断言（现 26 张，零错误）。

**执行时修正（低阶模型尤其容易再踩）**：
1. **右键命中判定不能只用 `Range.containsNode(target)`**：被点击的元素通常是选区的**祖先**（例如包着文本的 div），`containsNode` 判定恒假，右键菜单永远不会弹出。改成按选区的 `getClientRects()` 做几何命中判定。
2. **`unit_id` 可能含中文**（编译器把标题写进 id）。Python 测试里拼 URL 必须 `urllib.parse.quote`，否则 `UnicodeEncodeError`；前端本来就用的是 `encodeURIComponent`。
3. **新增数据表必须同步三处白名单，否则会静默丢数据**：`backup.IMPORT_TABLES`（导入）、`services.export_data`（导出）、`services.reset_user`（重置）。已用 `test_inspiration_notes_survive_export_import` 把这条不变量钉住——以后加表先加这个测试。
4. **选区的单元归属用 `closest("[data-unit-id]")` 向上找**：页面级容器标 `data-unit-id`、内容块标 `data-selectable`，两者解耦，这样复习卡、对话气泡都能追溯所属单元而不必重复标注。
5. **SPA 只改 hash 不会重新加载模块**：验证前端改动必须整页 reload，否则跑的是旧代码（本次一度误判 step3 没渲染）。
6. `selectionchange` 是异步派发的：同一次 `eval` 里设置选区再读菜单状态会读到旧值，要分两步断言。

---

## 7. P1 迁移批次（任务 E，A-D 完成后按序执行）—— ✅ 全部完成于 2026-10-21，详见第 11 节

每项都遵循同一格式：改哪里 / 怎么改 / 验收。规模都不大，低阶模型可一次一项。

### E1 复习先回忆再翻面（源 mem m3）
- review.js 卡片正面：现有「显示答案」按钮上方加 `<textarea id="rv-recall" class="textarea" placeholder="先不看答案，用自己的话回忆一下——写不出来正好说明这里还没懂。">`；翻面后答案区顶部渲染「你的回忆」块（对照排版：你的回忆在上、标准答案在下）。
- 后端：`services.rate_card`（services.py:577）目前只写 `review_logs`，不写 attempts——加可选参数 `recall: str = ""`：非空时 `db.insert("attempts", {..., "kind": "recall", "response": recall[:4000], ...})`（attempts 表结构见 db.py:162-174，id 用 `new_id("at")`）；review.js 评分时把 textarea 内容一起 POST。
- 验收：翻面前可见 textarea；评分后 attempts 表有记录；空回忆可选（不强制）。

### E2 引文高亮重现（源 mem m2）
- unit.js 渲染完内容后：取该单元的 notes（GET /api/notes?unit_id=）遍历 quote，用 TreeWalker 文本索引 + Range.surroundContents 包 `<mark class="note-hl">`（全部出现位置；跳过 code/katex 节点；一次渲染只处理不重叠的前 20 处）。
- CSS `.note-hl { background: rgba(250,204,21,.25); border-radius:2px; padding:0 1px; box-decoration-break: clone; }`（box-decoration-break 防多行断裂，testlearn 实证必需）。
- 验收：存过灵感的引用文本在单元页被高亮；无 notes 时零开销。

### E3 单元口诀 AI 生成（源 mem m5）
- **现状**：口诀的存储与展示已有——`unit_notes` 表 kind='mnemonic'（db.py:250），单元页可编辑（unit.js:180），复习队列已透出（services.py:544-554）。缺的只是 AI 生成。
- 新 prompt `prompts/mnemonic.md`——**严格任务边界**（照抄 mem 的四条规则：只给记忆钩子（口诀/谐音/类比/反差），禁止复述或总结内容、禁止新增知识、公式 `$...$` 原样保留、每条 ≤40 字共 ≤200 字）；
- 单元页「速记」textarea 旁加「AI 帮我编口诀」按钮（LLM 未配置不渲染）→ 新端点 `POST /api/units/{id}/mnemonic`（读单元 essence/steps/boundaries 拼材料，**服务端取数不信前端传文**，mem 的信任边界原则）→ 结果填入 textarea（用户确认后才保存，绝不静默写入）。
- 验收：演示模式按钮不出现；配置模型后点击 → textarea 填充 1-3 条口诀。

### E4 LLM 网关加固（源 mem m6 + examgen e2/e8）
boil/llm.py 的 `chat`/`chat_json` 增加四个机制（全部带注释级小改动，别重构）：
1. `finish_reason == "length"` → 以 `max(4×, 800)` 的 max_tokens 重试一次，取更长结果；
2. 响应 400 且 body 提到 `response_format`/`json_object` → 去掉该参数重试一次；
3. `parse_json_loose` 增强：解析失败时做「截断补全」猜测（末尾补 `"}]`/`]"` 配平——照 examgen `src/llm/index.js:60-131` 的括号计数逻辑用 Python 重写）；仍失败再抛；
4. 垃圾字面量守卫：`chat_json` 结果若是 `"undefined"/"null"/"None"/"NaN"/"无"` 等字符串 → 视为失败走重试。
- 验收：tests/test_llm.py（若没有则建）用本地假服务器（http.server 线程）覆盖 1/2/4 三种场景。

### E5 可选 AI 熔断（源 mem m7）
- api.js 记录连续 AI 端点失败次数（`window.__boilAiFails`）；≥2 → `ctx.state.aiBroken = true` 并 toast「AI 暂不可用，已临时切换本地模式」；
- 所有「AI 按钮」（suggest/mnemonic/insights synthesize）渲染前检查 `ctx.state.aiBroken`，broken 则不渲染；成功调用清零计数。
- 验收：模拟两次失败后按钮消失；成功后恢复。

### E6 记忆健康度（源 mem m8）
- **现状澄清**：insights 已有「30 天保留率」（复习成功率，services.py insights 的 retention_30d）——那是**历史成功率**；这里要加的是**当前可提取性均值**（此刻平均还能想起多少），两者不同。
- services.insights 加 `memory_health`：全部非 new 卡当前可提取性 `R(t)=(1+FACTOR·t/S)^DECAY` 的均值（FACTOR/DECAY/公式直接用 `boil/srs/fsrs.py:31-32` 的常量与 `Scheduler.retrievability`（fsrs.py:116），别复制 mem 的）；
- insights.js 顶部指标卡加一项（百分比 + 一句话解释「所有已学内容此刻平均还能想起多少」）。
- 验收：有复习记录时数值 ∈ (0,1)；无记录显示「—」。

### E7 内容寻址 LLM 缓存（源 examgen e1）
- llm.py 加模块级 `CACHE_DIR = <data>/llm_cache/`；`chat_json` 计算键 `sha256(json.dumps({model, messages, max_tokens}))`；命中则直接返回缓存（**仅缓存 chat_json，不缓存流式**）；
- settings 加开关（meta 表 KV `llm_cache_enabled`，默认开）；缓存文件 `{key}.json` 存 `{result, created_at}`；
- **风险控制**：compile 这类确定性调用收益最大；socratic/feynman 含会话状态本来就不会命中。注意 messages 里若含时间戳等易变内容会导致永不命中——compile prompt 是稳定的，没问题。
- 验收：同一 compile 请求第二次 LLM 调用计数为 0（llm.py 加 `stats()` 返回 calls/cache_hits，settings 页展示）。

---

## 8. P2 迁移批次（任务 F，E 之后，按价值排序）—— ✅ F1–F11、F13–F16 已完成于 2026-10-21；F12 按原计划仍属可选，见第 11 节

| # | 功能 | 源 | Boil 落点要点 |
|---|---|---|---|
| F1 | 错因标签（6 类固定 taxonomy，评分时选，持久化） | mem m10 | cards 表加 `mistake_tags`（幂等加列）；rate_card body 接收；insights 按标签聚合 |
| F2 | 复习队列侧栏（跳转/只练选中/加未到期卡） | mem m11 | review.js 纯前端改造，数据已有 |
| F3 | 键盘复习（空格翻面、1/2/3 评分、`/` 聚焦搜索） | mem m12 | 全局 keydown，输入焦点时忽略 |
| F4 | 导入恢复前快照（import_data 前先把当前库导出到 data/backups/） | mem m13 | services.import_data 开头加 |
| F5 | 粘贴清洗（全角空格/CR/LaTeX 定界符/富文本项目符号归一） | mem m14 | 新 boil/markdown_fix.py 纯函数 + onboarding textarea paste 事件 + 单测 |
| F6 | LLM 诊断面板（异常分类+HTTP建议+可复制诊断） | mem m15 | settings.js LLM 卡片下加「诊断」折叠区，读 llm.last_error() 结构化展示 |
| F7 | 单元合并链（merged_into + UI 横幅跳转） | examgen e3/e7 | units 表加 `merged_into`；map/unit 渲染横幅；代码强制阈值合并（编译去重时 similarity 在代码里算） |
| F8 | 内容质检（LLM-as-judge 三维评审 + 可执行补丁 + 输出前校验器） | examgen e4/e5/e10 | 编译后自动跑：确定性检查（空字段/长度/重复标题）→ 残差交 LLM 评审 → 修补丁；结果进 warnings |
| F9 | 逐字锚点材料修复（切分质量差时 LLM 给锚点、代码做手术） | examgen e6 | compiler.py heuristic 路径：当某 section > 3000 字时触发 |
| F10 | 检索式知识注入（编译长材料时只注入相关片段而非全文） | examgen e9 | compile_material 超过 24k 字时分批：先 outline pass 再每单元 retrieval pass |
| F11 | AI 提炼卡片：预览→确认→入库 | testlearn t2 | 划词菜单加第三个动作「做卡片」→ 预览 front/back → 确认后进 cards 表 |
| F12 | 公式感知高亮锚定（KaTeX TeX 源 + 出现序号） | testlearn t3 | E2 的增强版，做不做看 E2 用下来的断裂率 |
| F13 | 网关：始终流式请求上游再拼回（防空闲网关 504） | testlearn t5 | llm.chat 内部改 stream:true + 前端无感；SSE 已有基础 |
| F14 | 追问侧线程（划词「问 AI」，带 ±800 字上下文） | testlearn t6 | selectionMenu 第三动作 + socratic side session |
| F15 | SSE 断线重连（request_id + last_seq 重放） | testlearn t7 | socratic.js stream 加重试；server 侧事件缓冲已有 events 表 |
| F16 | 学习风格测试→教学策略人设 | testlearn t8 | onboarding 可选第 4 步；结果写 goal meta，socratic prompt 注入 |

P3（远期，本计划不展开）：番茄钟、记忆 diff/撤销、移动端划词劫持、多用户认证。

---

## 9. 回归清单（每个任务完成后必跑）

```bash
cd /workspace/testweb3/boil
python -m unittest discover -s tests -v          # 全部通过（当前 155）
make check-js                                    # JS 语法
python run.py &                                   # 起服务（或确认已运行）
python tools/browser_check.py                     # 23+ 截图零报错
# 手工冒烟：#/onboarding 全流程（上传→建议→编译→总览/大纲→开始）→ dashboard → unit → review → #/notes
```

浏览器检查注意：tools/browser_check.py 的截图断言列表要随新 UI 同步扩展（新任务各节的验收标准里已写明要加什么断言）。

---

## 10. 完成定义（DoD）

一个任务算「完成」当且仅当：
1. 第 9 节回归清单全绿；
2. 演示模式（不配置 LLM）下该功能的全部路径可用且有 rules 标签；
3. 真实模式（data/llm.json 已配置，勿外泄 key）下手工冒烟通过一次；
4. 本文档对应小节末尾追加一行「✅ 完成于 YYYY-MM-DD + 简短结果」（如改动与本文档描述有出入，更新本文档而不是留下代码与文档的分歧）；
5. 相关文档同步：新端点进 API.md，新表/列进 ARCHITECTURE.md，用户可见功能进 README.md。

（最后：未经用户明确指示，不 commit / 不 push。）

---

## 11. 执行记录

### 2026-10-21 · 任务 A/B/C/D 完成

| 项 | 结果 |
|---|---|
| 单元测试 | 49 → **66** 全绿（新增 17 个） |
| JS 语法检查 | `make check-js` 通过 |
| 浏览器回归 | `tools/browser_check.py` **26 张截图，errors: none** |
| 控制台错误 | 全 7 个路由 0 报错 |
| 真实模型冒烟 | suggest 1.1s / compile 约 60s / insights synthesize 约 25s，均 `source=model` |

**新增文件**：`prompts/meta_suggest.md`、`prompts/insights_synth.md`、`static/js/selectionMenu.js`、`static/js/views/notes.js`、`IMPROVEMENT_PLAN.md`
**新增端点**：`POST /api/packs/suggest`、`POST /api/insights/synthesize`、`GET/POST /api/notes`、`POST /api/notes/delete`、`GET /api/notes/export`
**数据库**：`SCHEMA_VERSION` 2 → 4；新表 `notes`；`units` 新列 `angle`（旧库启动时幂等补列）
**新增导航**：侧边栏与底部移动导航的「灵感库」（`#/notes`）

**下一批（E 批次）待办的起点**：E1 复习先回忆再翻面 → E2 引文高亮重现 → E3 口诀 AI 生成 → E4 网关加固 → E5 AI 熔断 → E6 记忆健康度 → E7 内容寻址缓存。

执行 E2 时注意：`notes` 已带 `unit_id`，前端可直接 `GET /api/notes?unit_id=…` 取引用做高亮，无需后端改动。
执行 E3 时注意：口诀的存储与展示已存在（`unit_notes` kind=`mnemonic`，复习队列已透出），只缺 AI 生成；服务端取单元内容，不要信前端传的原文。
执行 E5 时注意：`insights_synthesize`、`suggest_meta` 已把失败写进 `warnings`/回退 `rules`，熔断只需在前端计数并隐藏入口。

### 2026-10-21 · E 批次完成（E1–E7）

| 项 | 落地 | 验证 |
|---|---|---|
| E1 先回忆再翻面 | review.js 回忆框 + 翻面后草稿排在答案上方；`rate_card(..., recall=)` 非空写入 `attempts(kind="recall")` | `test_recall_before_reveal_is_persisted`、`test_16` HTTP 层、浏览器断言回显内容 |
| E2 引文重现 | 新 `quoteHighlight.js`（TreeWalker 建坐标 → 全量匹配 → 去重叠 → **倒序** surroundContents，跳过 code/pre/.katex）；单元页顶部提示已标处数 | `unit-note-highlight.png` + 回归断言 marks≥1 且 tip 存在 |
| E3 AI 编口诀 | 新 `prompts/mnemonic.md`（严格任务边界）+ `POST /api/units/{id}/mnemonic`；服务端取材料、清洗去重；结果需「填进速记」→「保存速记」两步才落库 | 4 个 AiPathTest 用例 + 真实模型 1.6s 产出「口诀/类比/反差」三条且不复制内容 |
| E4 网关加固 | `finish_reason=length` 加额重试；括号配平扫描 + 截断补全；`junk_output` 守卫（`response_format` 400 降级原本已有） | `test_llm.py` 新增 9 个用例（含截断 JSON、字符串内括号、400 降级） |
| E5 AI 三态熔断 | `utils.js` 的 `aiEnabled/aiSuccess/aiFailure`；未配置或连续 2 次失败 → 入口**不渲染**；主流程不依赖模型 | 回归断言「入口显隐与 /api/health 的 llm.mode 一致」 |
| E6 记忆健康度 | `insights.memory_health`（复用 `Scheduler.retrievability`）+ `learned_cards`；洞察页指标卡与解释文案；无数据 `null` → 「—」 | `test_memory_health_tracks_learned_cards` + 回归断言卡片存在 |
| E7 内容寻址缓存 | `chat_json` 按 `sha256(model+messages+temp+max_tokens)` 落盘 `data/llm_cache/`；命中复用原文再解析；设置页开关 + 一键清空 + 统计 | 5 个 CacheTest；真实模型实测同一请求 **1.15s → 0.00s**、结果一致、calls 不增 |

| 汇总 | 结果 |
|---|---|
| 单元测试 | 66 → **86** 全绿 |
| 浏览器回归 | **27 张截图，errors: none** |
| 新增端点 | `POST /api/units/{id}/mnemonic`、`POST /api/settings/llm/cache/clear` |
| 新增请求字段 | `POST /api/review/{card}` 的 `recall`；`POST /api/settings/llm` 的 `cache_enabled` |
| 数据库 | 无新表；开始写 `attempts`（此前只有读方） |

**执行 E 批次时得到的经验（写给下一个执行者）**：

1. **测试打桩要注意 fixture 键名冲突**：`AiPathTest.setUp` 里若沿用 `self._old` 会覆盖 `TempCase` 的同名字典，`tearDown` 直接 `AttributeError`。用 `self._llm_old` 之类的前缀名。
2. **重定向配置要在 setUp 里补全**：`llm` 的缓存目录取自 `config.DATA_DIR`，测试若不把 `DATA_DIR` 指到临时目录，就会往仓库的 `data/` 里写缓存文件。
3. **写库函数改动要顺带查读方**：`attempts` 表此前只有读方（计数与展示），确认没有指标依赖它之后才敢往里写。
4. **`finish_reason=length` 加额重试要有前提**：只在请求里本来就有 `max_tokens` 时才加额；否则新增的额度会**低于**网关默认值，反而更容易截断。
5. **DOM 批量改写要倒序**：高亮多处引文时，正序包裹会让后面的偏移失效；倒序 + 每轮重建坐标最稳。
6. **别用 `pkill -f "python3 run.py"`**：模式会匹配到执行命令的 shell 自身，把自己的会话杀掉。用 `pgrep` 取 PID 再 `kill`，或给进程加唯一标识。

下一批从 **F1 错因标签**开始（顺序见第 8 节）。F2 复习队列侧栏与 F3 键盘复习是纯前端，风险最低，适合先热身。

### 2026-10-21 · F 批次完成（F1–F11、F13–F16；F12 仍为可选）

| 项 | 落地 | 验证 |
|---|---|---|
| F1 错因标签 | 6 类固定 taxonomy 由后端下发（`/api/review.mistake_tags`）；`review_logs/cards` 加 `mistake_tags_json`、`missed_points`；答对即清空卡片旧标签；洞察页 `mistakes` 给分布 + 样本量 | `test_mistake_tags_are_persisted_and_aggregated`、`test_17`、浏览器断言「没选标签时评分被拦一次且进度未变」 |
| F2 队列侧栏 | 跳转/勾选/只练选中/提前加卡；新 `GET /api/cards`（含未到期卡、搜索、按单元/状态筛） | `test_list_cards_covers_not_due_cards` + 浏览器断言 items/add/select/current |
| F3 键盘复习 | 空格翻面、1–4 评分、`/` 搜索（与 ⌘K 并存）；输入框、浮层、模态打开时一律不抢键 | 浏览器手工 + 现有 keyHandler 单测路径 |
| F4 导入前快照 | `backup.snapshot_db` 用 SQLite 备份 API 写 `data/backups/pre_import_<µs>.db`，保留 10 份；结果回 `snapshot` | `test_import_snapshots_database_first`（校验快照内 integrity_check 与导入前状态） |
| F5 粘贴清洗 | 新 `boil/markdown_fix.py`：LF、零宽、全角空格、项目符号、编号、标题空格、`\[..\]→$$..$$`；代码块/行内代码内部不动；`POST /api/material/fix` + onboarding paste 拦截 | `tests/test_markdown_fix.py` 14 例 + `test_18` |
| F6 诊断面板 | 异常链归类（dns/refused/timeout/tls/proxy/network）、HTTP 状态建议表、回显内容掩码后落盘、`last_error` 带 base_url/model/掩码 Key、设置页可复制诊断 | `DiagnosticsTest` 4 例 |
| F13 上游始终流式 | `chat()` 默认 `stream_upstream`：向网关切 `stream:true` 再拼回非流式形状；网关不认流式时按原 JSON 返回；可关 | `StreamUpstreamTest` 2 例 |
| F7 合并链 | `units.merged_into/merged_reason`；`POST /api/units/{id}/merge`、`GET /api/units/duplicates`；代码判阈值 0.62；relations 自动重连、卡片保留；地图与推荐过滤；单元页横幅 | 3 个合并测试 + `test_21`（含成环、跨目标、回指被吸收者均被拒） |
| F8 质检 | 新 `boil/quality.py`：确定性 lint → 安全补丁（**补丁=原文则 qa-skip**）→ 近似重复 → 残差交模型解释；编译响应新增 `quality` | `tests/test_quality.py` 18 例 |
| F9 锚点修复 | 超 3000 字的无小标题长节：模型只给逐字锚点，代码切片；锚点不在原文/只切出一段则保持原样；失败静默回退 | `AnchorRepairTest` 6 例 |
| F10 检索式分批 | 材料 >24k：先 `_skeleton` 出大纲/总览，再按 7k 预算逐批出单元；`source.batches` 记录批数；单批失败只跳过该批 | 2 个分批测试（逐节都进过模型 + 单批失败不空手） |
| F11 做卡片 | 划词「做卡片」→ `POST /api/cards/preview`（`engine: model/rules`）→ 预览卡 → 确认才 `POST /api/cards/confirm` 入队 | `test_cards_*` 5 例 + `test_19` + 浏览器预览断言 |
| F14 选中追问 | 划词「问 AI」→ `POST /api/ask`（±800 字锚点上下文，单轮）；demo 模式不渲染该动作 | `test_quick_ask_*` + `test_19` |
| F15 SSE 重连 | `stream_reply(retry=True)` 比对最后一条用户消息，不重复入库、不涨轮次；前端先查会话是否已落库，未落才带 retry 重发 | `test_stream_retry_does_not_duplicate_user_turn` |
| F16 风格测试 | `boil/styles.py` 5 题 → 5 人设 + 权重（不下发权重）；人设注入苏格拉底与费曼提示词；仪表盘卡片 + 弹窗 | `tests/test_styles.py` 6 例 + `test_20`（断言人设真的出现在 system 提示词里） |

| 汇总 | 结果 |
|---|---|
| 单元测试 | 86 → **155** 全绿（新增 69） |
| 浏览器回归 | **29 张截图，errors: none** |
| 新增模块 | `boil/quality.py`、`boil/markdown_fix.py`、`boil/styles.py`、`static/js/styleTest.js` |
| 新增端点 | `/api/cards`、`/api/cards/preview`、`/api/cards/confirm`、`/api/ask`、`/api/units/duplicates`、`/api/units/{id}/merge`、`/api/styles/questions`、`/api/styles/apply`、`/api/material/fix`、`/api/settings/llm/cache/clear` |
| 数据库 | `SCHEMA_VERSION` 3→6；`cards.mistake_tags_json`、`review_logs.{mistake_tags_json,missed_points}`、`units.{merged_into,merged_reason}`（旧库启动自动补列） |

**F 批次的踩坑（务必读）**：

1. **默认参数会冻结常量**：`def _batch_sections(sections, budget=BATCH_CHAR_BUDGET)` 让测试改 `compiler.BATCH_CHAR_BUDGET` 完全无效——需要运行时可调的阈值请在函数体里读全局。
2. **`x = x.f(...)` 会毁掉模块名**：`quality = quality.qa_pack(pack)` 让 `quality` 变成本地变量，`UnboundLocalError`。局部名换个叫法（`qa`）。
3. **模块内的私有 helper 不要靠 import 偷用**：`services` 里直接写 `_norm(...)` 会 `NameError`；跨模块要用的就把它做成公开函数（评审后已改名 `quality.norm` / `quality.similar`，下划线只留给模块内部）。
4. **幂等判断要看「最后一条用户消息」而不是「最后一条消息」**：一轮答完后最后一条是教练的回答，`retry` 永远判不出来，会重复入库并白涨轮次。
5. **新表/新列同样要过三处白名单**（`backup.IMPORT_TABLES`、`services.export_data`、`reset_user`）——这次是在 `cards`/`review_logs` 上加列，`SELECT *` 路径自动带上，但**别加新表**，加了必须同步（E 批已经为 `notes` 踩过）。
6. **合并的落点必须在 units 已入库之后**：step3 预览阶段 units 还没安装，`/api/units/{id}/merge` 会 404/400；所以 step3 只读展示质检结果，合并动作放在地图页（有 `GET /api/units/duplicates` 现算）。
7. **回归脚本里 `goto()` 只改 hash**：导航到同一个 hash 不会重渲染，依赖挂载时才跑的逻辑（引文高亮）不会被触发——断言前要经过别的页面再回来。
8. **`/api/units/duplicates` 必须排在 `/api/units/([^/]+)` 之前**，否则会被通用路由吃掉。
9. **假网关要能演「流式」**：`chat()` 改流式后，测试桩如果只按 `stream` 回固定台词，就会把「质检 warnings」这类真信号测错方向——先决定内容，再决定传输方式。

下一批建议从 F12（公式感知高亮，需先观察 E2 上线后的断裂率）或 P3（番茄钟、记忆 diff/撤销、移动端划词劫持）开始；也可回到第 1 节挑新的迁移项。

---

## 12. 评审与修复轮（E+F 提交前）

用户要求「暂不提交，先 review 一遍」。下面是评审发现并已修掉的项，全部带回归用例。

| 级别 | 缺陷 | 修法 | 回归 |
|---|---|---|---|
| **P0** | 划词面板被 document 级 `mousedown → hideAll()` 抢关：真实鼠标点面板内部（含「确认存入」「问 AI」的输入框）会先清空 DOM，click 永远落不到处理函数——F11/F14 对真用户**完全不可用** | `onMouseDown` 放行 `.sel-panel`；键盘浮层守卫统一成 `utils.overlayOpen()`（原来漏了 `.sel-panel`，面板开着时空格/1–4 仍在翻面评分） | `tools/browser_check.py` 新增 `real_click()`（CDP `Input.dispatchMouseEvent`）+ 三条断言；撤掉修复后实测报 `点面板内部把它关掉了 {shown:false, cards:0}` |
| P1 | 复习时写的回忆以 `kind="recall"` + `card.probe_id` 写进 `attempts`，把探测题「N 次作答」变成复习计数（live 库 10/10 张卡都挂 probe_id） | 改 `kind="review_recall"`、`probe_id=None`（来源探测题留在 `feedback_json`），文档同步 | `test_recall_before_reveal_is_persisted` 直接断言探测题计数仍为 0 |
| P1 | 分批编译静默丢内容：每节被 `body[:budget]` 截断、超过 10 个单元被 `collected[:10]` 吞掉、回退单次时 `text[:24000]` 也没有提示——与「模型读完了全部材料」的承诺冲突 | 新增 `_chunk_section`（空行→句号→硬切，再把小块装回预算内，**无损**）；`_normalize_llm_units` 接受 `warnings` 并在超限时明说；划词做卡片截断也写进 `engine_note` | `test_oversized_section_is_never_silently_cut`（24 个标记逐条断言进过模型）、`test_chunk_section_packs_without_losing_text`、`test_unit_cap_is_announced_not_silent` |
| P1 | `mode=replace` 导入：先提交 `reset_user` 再在事务里插入，插入炸掉就留下**被清空的库**；快照在盘上但没有 restore 端点。附带：没有目标时 `reset_user` 不删用户级数据，孤儿卡片会在用户重建后「复活」 | 清库与写入并进同一事务（`reset_user(db, uid, commit=False)`），用户级表移出 `if goal_ids` 守卫 | `test_failed_replace_import_leaves_old_data_intact`（撤掉修复实测 16 张卡被删光 → 现在 0 损失）、`test_reset_without_goals_still_clears_user_data` |
| P1 | `chat_json` 在解析前写缓存：一次截断/占位输出会被永久复用，同一个请求此后每次都炸在缓存命中上 | **解析成功才写盘**；命中坏条目就删掉条目回落到真实调用；缓存键加入 `base_url`（同名模型换网关不复用另一家输出） | `test_unparseable_output_is_not_cached`、`test_poisoned_cache_entry_self_heals`、`test_cache_key_separates_gateway_endpoint` |
| P1 | README 写「洞察页汇总成错因分布」，但前端**从来没渲染过** `insights.mistakes`（F1 计划里也明确要求洞察页有分布 + 样本量）——只做了后端聚合就宣称完成 | 新增「错因分布」卡片（条形 + `tagged_reviews / sample / missed_total` 口径说明）与 `.mistake-dist` 样式；浏览器断言行数或空态 | `browser_check` 的 `mistake tags: rows=… empty=…` 断言；窄屏几何用一次性 CDP 探针核过（无溢出） |

**评审本身的教训**（写下来防下次重犯）：

1. **合成 `element.click()` 测不出浮层交互**。`browser_check.py` 全程 `.click()`，29 张截图全绿也发现不了「面板一按就关」。凡是「点外部关闭」「面板内输入」这类交互，必须用 `Input.dispatchMouseEvent` 走真事件，并且要**反向验证**：撤掉修复看用例是否变红（这轮两次都验了：P0 与导入回滚）。
2. **接口返回了数据 ≠ 功能完成**。F1 的错因分布在后端聚合、被测试断言、在 README 被宣称，但没有任何一行 UI 读它。验收要看「用户看得见的东西」。
3. **计数口径要写进文案**。`memory_health`/`mistakes` 是全局口径而其它字段按目标过滤——查证后发现文案已如实写「所有已学卡」，所以不改语义只补文档；而 `attempts` 的污染是真的会读出假数字，必须改。
4. **评审里我自己有两条报错了**：`GET /api/styles/questions` 其实早就写在 API.md 里（两条空格让我的正则没匹配上，别用单一 grep 下结论）；`list_snapshots()` 不是死代码，剪枝测试要用它。报错的结论要当场撤掉，不要留着好看。
5. **默认参数冻结常量**这条（F 批第 1 条）在 `_normalize_llm_units` 上又差点重演：`limit=MAX_UNITS_SINGLE` 写在签名里会把默认值固定在 import 时刻——这里因为是纯常量才没问题，但把可调阈值放签名里之前先想清楚。

| 汇总 | 结果 |
|---|---|
| 单元测试 | 155 → **163** 全绿（新增 8） |
| 浏览器回归 | 29 张截图 **errors: none** + 真实鼠标三条断言 + 错因分布断言 |
| 文档 | API.md 补 `GET /api/cards`、`mistakes`、缓存自愈、导入同事务；ARCHITECTURE.md 补无损切分、缓存自愈、真鼠标回归；README 计数与两处功能描述改为兑现得了的说法 |

---

## 13. 语义去重与检索分级（用硅基流动 embeddings）

评审留下的第一条开放项：`find_near_duplicates` 用字符集合 Jaccard，实测只能抓近乎逐字重复，同义改写只有 0.17，导致 F7 的合并入口在真实数据上几乎不出现。这一轮按用户指示改用真实嵌入服务（`data/embedding.json` 里的 SiliconFlow `BAAI/bge-m3`，1024 维），并参考 testlearn `ranking.py` 的「字面 + 语义」双路思路。

**先测量，再定阈值**（脚本一次性跑真网关，不写库）：

| 配对 | 字面 Jaccard | bge-m3 余弦 | 事实 |
|---|---|---|---|
| 沸腾 / 水的沸腾（改写） | 0.18 | 0.80–0.85 | 真重复 |
| 间隔复习 / 分布练习 | 0.20 | 0.749 | 真重复 |
| 蒸发 / 沸腾 | 0.18 | 0.75–0.79 | 相关但**不同** |
| 同一目标库里 5 个不同单元 | ≤0.40 | ≤0.72 | 不同 |
| 密度 / 牛顿第一定律 | 0.11 | 0.46 | 不同 |

结论：**两个分数各自都会错**——字面漏掉改写，语义把相关概念判成重复。所以分数只提候选，裁决走阶梯（`quality.merge_decision`，详见 ARCHITECTURE 4.7.1）：`lexical ≥ 0.62` 或 `semantic ≥ 0.80` 由代码放行；0.74–0.80 中间带必须有模型 `same: true`；都不够时人可署名坚持（`override` + 理由 ≥ 4 字，`basis="user"` 落库）。

做完之后实测发现的两件事：

1. **模型对同一对会翻**。第一次跑：蒸发/沸腾 → `same=false`（「沸腾与蒸发是不同汽化现象」），合并被拒；第二次跑同一对 → `same=true`（「均描述液体汽化现象」），合并被放行。单次模型裁决不配拥有破坏性权力 → 于是 (a) 判定问法按 id 定序，让候选列表与合并复查共用同一个提示词，第二次直接命中内容寻址缓存（实测 `calls: 1, hits: 3`，页面显示的依据与端点采用的依据必然同源）；(b) `basis="model"` 的放行在前端必须再点一次确认。
2. **「拒绝」不等于封死**。分数与模型都不同意时用户仍可能知道自己那两条是一回事，所以给了署名越权：不写理由就报「至少 4 个字」，写了就把「学习者确认：<原话>」存进 `merged_reason`，日后审计分得清是谁的决定（`test_merge_override_requires_a_written_reason`）。

搜索侧参考 testlearn 的 hybrid，但**没有照搬 RRF**：名次融合会让「只是有点像」挤掉精确命中，那适合给模型喂片段，不适合给人的搜索框。改成分级——双命中 > 只字面 > 只语义，每条带 `matched_by`（`test_search_tiers_put_exact_hits_before_lookalikes`）。

诚实性收口：嵌入不可用时 `semantic` 一律 `null` 并在文案里说明，**绝不拿本地哈希向量冒充语义分**（`_pair_semantics` 只走 `embed_texts`，不走 `embed` 的本地回退）。

| 汇总 | 结果 |
|---|---|
| 新增/改动 | `quality.{lexical_score,semantic_pair,signals,judge_pair,merge_decision,_pair_semantics}`、`find_near_duplicates` 双路候选、`services.{unit_duplicates,merge_units,search}`、`server` merge 传 `override`、`map.js` 候选行显示判据/依据/不可合并与署名对话框 |
| 测试 | 168 全绿（新增阶梯表、候选只由语义带提出、署名越权、搜索分级 4 条）；`tests/test_quality.py` 加 `setUpModule` 隔离嵌入配置，防止测试打真网络 |
| 浏览器 | harness 新增「种一个重复单元 → 真鼠标走完候选→依据→合并」回归，含防误删护栏（被吸收方必须是种子单元才点） |
| 实跑证据 | `dup card: 疑似重复 1 对 · 语义 + 字面 ｜ 字面近乎一致`；`merged: 已把「…」并入「…」`；override 路径 `basis="user"` 且 `merged_reason=学习者确认：…` |
| 已知未覆盖 | `basis="model"` 的确认弹窗只在服务层与离线用例验证过，浏览器回归没造出「语义落在中间带且模型判 same」的数据 |

下一步仍可回到：合并的 unmerge（当前关系边重写不可撤销）、嵌入缓存与 llm_cache 的容量上限、F12 公式感知高亮、P3（番茄钟 / 记忆 diff / 移动端划词）。

### 13.1 完整真实模拟（P1–P16）抓出并修掉的四个问题

模拟跑在隔离实例上：把 `data/boil.db` 用 SQLite 备份 API 复制到 `/tmp/boiljourney`，连同一份 `llm.json`/`embedding.json`，用真 Dots 模型 + 真 bge-m3 走 16 个阶段（元信息建议 → 10,204 字长材料编译 → 建目标 → 语义候选与合并阶梯 → 速记 → 苏格拉底真流式与重连 → 费曼六维 → 复习与错因 → 洞察与 AI 解读 → 搜索 → 划词三动作 → 风格测试 → 粘贴清洗 → 设置与缓存 → 导出/合并/坏替换回滚 → 数据审计）。

| 抓到的问题 | 严重度 | 修法 | 现在的证据 |
|---|---|---|---|
| `SINGLE_CALL_LIMIT = 24000` 是按上下文长度定的，但 10,204 字材料的真返回在尾部被截成 `"difficulty":4,":5,":":40"`，能否修复成掷硬币（第一次模拟就回退成了本地编译） | 高：直接决定「AI 读完全部材料」是不是空话 | 上限按实测输出能力降到 6000；`_normalize_llm_units` 接受模型直接回的单元数组；前端编译请求超时从 90s 提到 600s（分批要串多次调用，默认值会先炸）；编译 warnings 现在渲染进 step3 的质检框而不是只弹 5 秒 toast | 模拟重跑：`batches=2 material_chars=10204 generated_by=model` |
| 语义候选用完整 `unit_text`，同主题单元全挤在 0.76–0.81，8 个单元的包一次跳 **9 对候选 / 7 次模型判定**，还把「间隔效应」和「测试即学习」判成可合并 | 高：噪声让合并入口不可用，且打开地图页变成烧钱 | 判定文本改成只看标题+本质（真重复 0.870 / 最危险假对 0.797）；一次最多判 `JUDGE_LIMIT=3` 对，其余标 `pending_judgement` 用户点了才判 | 模拟重跑：**0→2 对候选、2 次判定**，两对假候选都被模型判「原理与应用不同」而拒绝 |
| 候选列表刚写「不可合并」，合并端点自己再问一次模型就改判 `same=true` **并真的合了库**（缓存被关掉时尤其明显） | 高：破坏性操作被执行了，而 UI 说的是相反的话 | 非字面证据的合并在服务端强制要求 `confirmed: true`；判定问法按 id 定序；前端把「确认合并」「请模型判定」「坚持合并（写理由）」拆成三条路径 | `test_non_lexical_merge_needs_human_confirmation` + 模拟：不带 confirmed 报「需要你先确认」且库里一个字没改，带 confirmed 才合 |
| 用户手改的备份文件不合法时报 **500「服务器内部错误」**，而回滚其实是对的 | 中：错误级别与信息都误导人 | `sqlite3.Error` 映射成 `400 import_conflict`，消息里直接写「本次导入已整体回滚，原有数据未受影响」 | `test_22_bad_backup_rolls_back_and_says_400`：400 + 四张表计数一条不少 + 坏行没进去 |

**顺带修的一处自伤**：`map.js` 里 `/api/map` 与 `/api/units/duplicates` 串行 await，把两个延迟加起来等；改并行。

**关于探针的教训**（模拟脚本自身的错，记下来免得下次误判产品）：SSE 要按 `event:` 行取类型（`data:` 里的 JSON 已不含 type）；`/api/export` 与 `/api/notes/export` 返回裸文件不是 `{ok,data}` 信封；`/api/settings/embeddings/test` 只认 POST；`/api/import` 的请求体就是导出文档本身，且 replace 需要 `confirm=IMPORT`。**最后一条差点让我把「回滚通过」记成假通过**——报错其实是闸门挡的，根本没走到插入。断言必须区分「因为正确所以通过」和「因为没跑到所以通过」。

| 最终验证 | 结果 |
|---|---|
| 单元/集成/HTTP/LLM 测试 | **172 全绿**（本轮 +13） |
| `make check-js` | 通过 |
| 浏览器回归 | **36 张截图，errors: none**；`dup card: 疑似重复 1 对 · 语义 + 字面 ｜ 字面近乎一致`、`merged: 已把…（字面近乎一致）`、真鼠标面板三条断言、错因分布断言全过 |
| 真实模拟 | P1–P16 全部通过（隔离实例，日常库未动） |

---

## 14. 收尾批次：撤销合并 / 回忆记录 / 缓存上限

计划里「不展开」的 P3 和自评清单里剩下的小洞，挑价值最高的三项做掉，其余（番茄钟、记忆 diff/撤销、移动端划词、多用户）继续搁置。

| 项 | 为什么值得做 | 做法 |
|---|---|---|
| **撤销合并** | 文档和 UI 一直写着「当前不能撤销」——一个会重写关系边、会改变地图与计划的操作没有退路，只能靠手工改库 | `units.merged_op` 指向那次 `unit.merged` 事件；合并时把每条关系改动（`action: update|delete` + 原端点 + 新端点）写进事件的 `undo.relations`；`POST /api/units/{id}/unmerge` 在一个事务里撤销指向并回滚边。三条闸：归宿已被并走 / 本单元之后又吸了别人 / 记录缺失（缺失则降级为只撤指向并标 `degraded`，不假装恢复了边） |
| **回忆记录** | `attempts(kind=review_recall)` 收集了用户自己写的回忆，但从来没在任何页面显示过——和之前「错因只算不显示」是同一类问题 | `unit_detail.recalls` 取最近 6 条；单元页「我的回忆记录」按时间倒序显示分数 + 原文；复习页注明「评分后这份回忆会存进单元页」 |
| **LLM 缓存上限** | 内容寻址缓存只增不减，长期跑会一直吃盘 | `CACHE_MAX_ENTRIES = 800`，写入后超出就按 mtime 淘汰到九成；嵌入缓存本来就有 `CACHE_CAP`，这次补齐了 LLM 侧 |

**真实浏览器回归又抓到两个真错**（都是这轮新代码）：

1. `unit.js` 的撤销处理器调用了 `toast` 却没 import——`Uncaught (in promise) ReferenceError`，harness 的 JS 异常采集当场把这些抓成 errors；30 秒后 toast 断言也跟着红。
2. 撤销成功但提示词只有「已恢复 0 条关系边」，用户看不出这是一次撤销。前端改为固定前缀「已撤销合并；」，服务端把「记录在但没涉及关系」和「记录缺失」分开措辞。

**一条环境操作的教训**：`ps -eo pid,cmd | awk '/[p]ython3 run.py/{print $1}'` 会匹配到**我自己这条命令**的 cmdline（里面就写着 `python3 run.py`），于是把执行它的 shell 一起杀了（exit 143）。以后起服务一律写 pid 文件，杀进程只按 pid 文件来。

| 验证 | 结果 |
|---|---|
| 单元/HTTP 测试 | **177 全绿**（+5：撤销恢复边、归宿移动后拒绝、吸收过别人后拒绝、HTTP 撤销往返、缓存上限） |
| 浏览器回归 | 36 张截图 `errors: none`；新增「真鼠标合并 → 单元页撤销 → 确认」与「回忆记录渲染」两条断言 |
| 数据库审计 | 种子残留 0、悬空归宿 0、`merged_op` 缺失 0、临时回忆行 0、重复边 0 |

---

## 15. P3 收尾：撤销评分 / 番茄钟 / 触摸端划词

计划里被标成「低价值、暂不展开」的三项，用户要求补完。做完发现前两项其实都不是锦上添花：一个是数据正确性问题，一个是「收了数据却没有任何地方呈现」的老毛病。

| 项 | 实际做了什么 | 关键取舍 |
|---|---|---|
| **t11 记忆差异化 + 撤销** | 评分响应带 `undo`（本次间隔、掌握度前后、`op` 凭据）；复习页侧栏出现「刚才这次」+ 撤销按钮。`POST /api/reviews/undo` 在一个事务里还原卡片行、恢复 mastery、删 review_log / recall attempt / evidence / boil_record、当天 `reviews` 减一 | 掌握度是 EMA 状态量，「减一条证据」在数学上不成立 → 评分时把卡片与 mastery 行快照写进 `review.rated` 事件，撤销整行还原。只允许撤**该单元最近一次**：用 `review_logs.rowid` 判先后（时间戳只到秒，同秒两次会误判），同一次不能撤两遍 |
| **t10 番茄钟** | 顶栏「专注」按钮 + 15/25/45 分钟选择 + 倒计时（`localStorage` 跨刷新恢复）+ 完成自动记账；`GET /api/focus`、`POST /api/focus/{start,finish}` | 只有 completed 写入 `study_days`；`session` 同时是事件 id，重复提交返回 `duplicate`，不会把 25 分钟记两遍；提前结束必须先确认，文案直说「不计入学习时长」 |
| **t12 触摸端划词** | `touchstart` 时收起旧菜单并进入拖选态（`selectionchange` 不再抢跑），`touchend` 后 280ms 把浮条放到选区上方；窄屏菜单换行并夹在视口内；`[data-selectable]` 加 `-webkit-touch-callout: none` 让系统长按菜单让位 | 不做「包 span 去原生菜单」那套 DOM 手术：选区保持原生，只在事件时序上接管；iOS 的系统菜单无法完全阻止，但自定义浮条不会因此消失 |

**这一轮 harness 又教了我两件事**：

1. **toast 会堆叠**。`document.querySelector('.toast')` 永远拿到最早的那条，于是「放弃专注」的断言读到了「已开始 25 分钟专注」。改成取 `querySelectorAll('.toast')` 的最后一条。凡是「刚弹出的提示」都要取最后一个节点。
2. **不要用时间戳判断「谁后发生」**。撤销评分的守卫最初用 `reviewed_at >`，而 `now_iso()` 只到秒，同一秒内的两次复习被判成并列，测试里「不该允许」的场景居然放行了。换成 SQLite 的隐式 `rowid` 比较，和时钟精度彻底解耦。

| 验证 | 结果 |
|---|---|
| 单元/HTTP 测试 | **182 全绿**（+5：撤销评分整行还原、同单元后来复习则拒绝、HTTP 撤销往返+二次撤销 400、专注只计完成、专注重复提交幂等） |
| 浏览器回归 | 36 张截图 `errors: none`；新增三条真交互断言：`focus running: 24:59` → `focus abandoned: 提前结束了…`、`review undo: 刚才这次 10 分钟 记得 · 掌握度 30% → 51%` → `review undone: 已撤销这次评分…`、`touch menu: 4 个动作，边界 45–343/390` |
| 数据审计 | 撤销后 `review_logs` 归零、`review.undone` 有审计事件；两次放弃的专注都没有进 `study_days`；无种子残留 |

P3 之后，计划里只剩 **F12（公式感知高亮）** 与明确不做的多用户认证。F12 的前置条件仍是「观察到 E2 在含公式材料上真的断裂」——目前的真实材料里没有复现，继续不做。

---

## 16. F12 公式感知高亮（计划里最后一项）

计划对 F12 的前提写得明确：「做不做看 E2 用下来的断裂率」。所以先测，再决定——结果是**断裂率 100%，而且比预想的更严重**。

**实测**（隔离实例，一个含 `$x \to 0$`、`$\sin x \sim x$` 的单元，用真实选区跨过公式、走 app 自己的保存路径）：

| 观察 | 结果 |
|---|---|
| `selection.toString()` | `'当 \n𝑥\n→\n0\nx→0 时，\nsin\n\u2061\n𝑥\n∼\n𝑥\nsinx∼x，这是…'` —— MathML、annotation、两遍字形全在 |
| 存下的 `quote` | 同一坨乱码，**笔记本身已经不可读** |
| 重新打开单元页 | `.note-hl` = **0**，提示条不出现 |

也就是说这不仅是「高亮锚定」问题，还是**数据质量问题**：跨公式的灵感在笔记库里存的是没法读的字符串。

**做法**：

1. **可读化**：保存时克隆选区，把 `.katex` 换成 `$TeX$`（TeX 取自 `annotation[encoding="application/x-tex"]`），得到 `当 $x \to 0$ 时，$\sin x \sim x$，…`。复制、做卡片、问 AI 用的是同一份文本——原来它们拿到的也是乱码。
2. **锚点**：用与高亮器同一套 TreeWalker 坐标（跳过 `.katex`）算出选区对应的「去公式可见文本」切片和它的出现序号，存 `notes.anchor_json`（`SCHEMA_VERSION` 7→8，旧库启动自动补列）。高亮时优先按锚点只点亮那一处；材料改过锚点失效，退回引文原文。
3. **不碰 KaTeX 的 DOM**：被公式隔开的文字落成几段 `<mark>`，公式本身不包（往 KaTeX 内部插节点风险太高）；提示条因此改成「已标出 N 段（来自 M 条灵感）」。
4. 追问上下文从 `innerText` 换成同样的 `$TeX$` 文本，`indexOf` 才能对上选中的位置。

**实测复盘**（同一脚本、同一材料）：

```
saved quote: 当 $x \to 0$ 时，$\sin x \sim x$，这是等价无穷小替换的常用起点。
after reload marks: 3   tip: 已标出 3 段原文（来自 1 条灵感；跨公式的引文会被公式拆成几段）
```

**harness 又抓到一个自己挖的坑**：`from urllib.parse import quote` 是模块级导入，我在 `main()` 里写了个局部变量 `quote`，于是整个函数里的 `quote(...)` 都变成「局部变量未赋值」——**去重候选那段直接 UnboundLocalError**，被它自己的 try/except 吞成一条普通错误。改名为 `quote_text` 后恢复。这也说明那几个大 try/except 虽然保住了流程，但会把名字冲突这类低级错误降级成一个模糊的业务错误，排查时得先看完整的 errors 列表。

| 验证 | 结果 |
|---|---|
| 单元/HTTP 测试 | **184 全绿**（+2：锚点往返与清洗、锚点对外不暴露内部列） |
| 浏览器回归 | 36 张截图 `errors: none`；新增 `math highlight: 3 段，当 ｜ 时，｜，这是常用起点。`（种一个含公式的单元 → 真选区跨公式 → 存灵感 → 重新打开） |
| 数据审计 | 临时单元与笔记都已清理；`notes` 表已按 v8 迁移，旧笔记没有 anchor 时仍走原文匹配 |

计划到此**全部完成**：§3–§8 的任务 A–F、§12 评审修复、§13 语义去重、§14 撤销合并、§15 P3、§16 F12。唯一明确不做的是多用户认证（见 §1.6）。


---

## 17. 版本控制、真人点击旅程与学习效果盘点（2026-10-24）

三件事：给项目一个学习者看得见的版本；用真人的节奏把整个产品点一遍；然后诚实回答"这样学到底有没有用"。

### 17.1 更新日志必须是给人看的那一份

`git log` 里有的是 `feat: keep quotes that cross a formula readable and re-findable`。
学习者要的不是这句话，而是"选中一段包含公式的文字不再错位"。所以：

- **`CHANGELOG.md` 是唯一事实源**，`boil/releases.py` 解析它 → `GET /api/releases` → 侧边栏徽标 / 浮层 / 设置页分区。
  不解析 git：Docker 镜像里没有 `.git`（`.dockerignore` 排除了它），而且 commit message 的读者是协作者。
- **构建号是另一回事**：`build_identity()` 直接读 `.git/HEAD` → `refs/heads/main`（回退 `packed-refs`），
  不起 git 进程；`.git` 是 worktree 指针文件时相对路径按仓库根解析；ref 里出现 `..` 或以 `/` 开头就不跟。
  没有 `.git` 时 `build=""`，前端就不显示 —— 宁可空着，不编一个。
- **格式自检与版本自检分开**：`changelog_problems()` 只管日志本身，`version_problems()` 只管
  `config.VERSION` 是否等于最新一条。混在一起就没法用示例文本测格式规则（我第一版就撞上了：
  任何假样本都会因为"版本号不等于 1.1.0"被判红）。
- **顺序比较用数字元组**：字符串比较会认为 `1.10.0 < 1.9.0`，发版顺序会被误判。
- **代码块里的示例不是发布**：`_lines()` 先剥掉 ``` 围栏，否则"该怎么写日志"的示例会被当成第 9.9.9 版。
- **已读状态放浏览器**：`localStorage.boil_seen_release`。它是"这台设备读到哪儿"，不是学习数据，不该进库。

**反向验证**（把守卫拆掉，测试必须红）：

| 破坏 | 结果 |
|---|---|
| 最新条目改成 1.2.0 | `config.VERSION 是 1.1.0，但 CHANGELOG.md 最新的版本是 1.2.0` ✅ 红 |
| 标题写成 `## 版本 1.1.0（2026年10月24日）` | `无法解析的版本标题` + 版本落到 1.0.0 ✅ 红 |
| 删掉一句话摘要 | `版本 1.1.0: 缺少一句话摘要` ✅ 红 |
| 删掉 1.1.0 的条目 | `没有任何条目` + 三个空分组 ✅ 红 |
| 把 1.1.0 改成 0.9.0（顺序倒退） | `排在 … 之后，版本号却更大` ✅ 红 |
| 文件清空 | `没解析出任何版本` ✅ 红（且服务照常启动、页面显示"没有读到更新日志"） |

### 17.2 `tools/human_journey.py`：真人节奏的用户旅程

和 `browser_check.py` 分工不同：后者是全站体检（每页截图 + 抓 JS 报错），前者走**一条连贯的"新用户的一天"**，
断言的是屏幕上读得到的字，不是 DOM 里有没有某个节点。最后一轮 **22/22 全绿**（`exit=0`）：

```
粘贴 774 字材料（含 LaTeX）→ AI 生成信息（模型给出「让记忆留下来的四件事」·「学习方法 / 记忆科学」）
→ 编译成 4 个单元 → 建目标 → 今日建议 → 单元页 → AI 口诀填进速记 → 真拖拽选词存灵感
→ 当场 1 段 / 刷新后 1 段高亮 → 复习：先回忆→翻面→评分→撤销 → 番茄钟 24:58 后放弃（不计入时长）
→ 版本徽标（未读圆点）→ 更新内容浮层（2 个版本 / 31 条）→ 圆点消失 → 设置页版本分区
→ ⌘K 搜到单元 → 洞察 AI 解读 → 灵感库看到那条 → 导出真的落盘（114,917 字节 / 4 单元 / 12 卡 / 无密钥）
→ 390×844 手机尺寸再走一遍 → 事件流记全（goal.created / note.created / review.rated / focus.session）
```

它抓到的七个问题里，**三个是产品的，四个是测试自己的** —— 后者同样值得写下来，因为它们正是"绿色测试骗人"的方式：

产品侧：

1. **存完灵感，引文不当场亮**：`unit.js` 只在渲染时跑一次高亮，用户要离开再回来才看到 E2 的回现。
   现在监听 `boil:note-saved` 重画，重画前先拆掉旧 `<mark>` 并 `normalize()`（否则旧标记里的文字被跳过，亮不出第二遍）。
2. **`/api/packs/suggest` 吃 90 秒默认超时**：端点慢的时候服务端还在算，前端已经报失败。改成 180 秒。
3. **提示过度声称**：模型没返回学科时，toast 仍然说"已生成标题与主题"。改成只列真的填进去的字段，
   并补一句"学科没判断出来，可以自己补一句"。

测试侧（每一个都会让假故障看起来像产品坏了，或者让真故障看起来像通过）：

1. **`html{scroll-behavior:smooth}` 让点击坐标过期**：`scrollIntoView()` 之后立刻量 `getBoundingClientRect()`
   拿到的是动画起点的坐标，点击落在别的元素上。老 harness 靠 `prefers-reduced-motion` 绕过了这点 ——
   真人不关动画。现在滚动用 `behavior:'instant'`，并且**能不滚就不滚**（划词浮条是 fixed 定位，
   页面滚过 10px 就被 `onScroll` 收掉，滚一下再点只能点到一个 0×0 的空壳）。
2. **`querySelector` 命中 ≠ 看得见**：`.selection-menu` 常驻 DOM，只切 `display`。
   用它做"浮条出现了"的断言，等于什么都没测。加了 `visible()` / `wait_visible()`。
3. **两个浮层不是一个类**：笔记面板是 `.note-popover`，做卡片/问 AI 的是 `.sel-panel`。写错选择器时测试会
   在"面板没出现"处失败，看起来像产品坏了。
4. **断言阈值不能拍脑袋**：我写"材料应进去 ≥900 字"，实际材料 774 字 —— 红的是测试。现在跟 `len(MATERIAL)` 比。
   同类错误还有：未读圆点在**点击那一刻**就被 `openReleaseModal` 摘掉，测试必须在点击前拍快照。

### 17.3 学习效果：`tools/effectiveness_sim.py` 能说什么，不能说什么

用项目自己的 FSRS-5 调度器推演 400 张卡 / 90 天，比较"什么时候复习"：

| 执行率 90% | 复习次数 | 次/卡 | 90 天 R | 稳住的卡 |
|---|---|---|---|---|
| 到期才复习（本项目） | 3,694 | 9.2 | 0.95 | 99% |
| 每 3 天全量翻一遍 | 11,582 | 29.0 | 0.99 | 99% |
| 每天全量翻一遍 | 32,148 | 80.4 | 1.00 | 100% |
| 学一遍就不管 | 400 | 1.0 | 0.36 | 0% |

- **能说的**：`request_retention=0.9` 这个默认值在做的是取舍 —— 比"每天全量"少做 **8.7 倍**的功，
  R 只低 0.05。这是可核对的工作量结论，可以直接指导默认值。
- **也能说**：执行率从 90% 掉到 60%，90 天 R 几乎不动（0.95→0.95），但"稳住的卡"从 99% 掉到 96%。
  调度器救不了不做复习的人，所以产品重心是"今天这几张卡"这种小到能完成的承诺，不是更聪明的算法。
- **不能说**：这**不是效果证明**。用 FSRS 自己的公式验证 FSRS 的假设是循环论证。
  检索练习与间隔效应的证据在文献里（本项目采纳它），但本项目自己的长期证据是**空白**：
  没有跨月留存实测、没有策略对照、没有做过模型预测 R 与实测回忆率的校准。
  这三条是下一阶段唯一有意义的验收标准（见 §13 与本节末尾）。

---

## 18. 小白视角实测：一个不懂乐理的人用 Boil 学一章（2026-10-24）

先做校准（§17 承诺的第 1 步），再**以完全新人的身份把产品用一遍**，取证方式是把每一步屏幕上的文字原样 dump 下来（`/tmp/novice-transcript*.txt` + `screenshots/novice-*.png`），然后照着实读。

材料是一章真的乐理（音程/音数/三全音/和弦转位，1,400 字，术语密度按教材水平写），我的设定是"从没学过乐器"。全程约 38 分钟。

### 18.1 校准功能落地

`services.calibration()` + 洞察页「模型说得准不准」卡片：把每次复习**前**模型预测的 `retention_before` 和**后**的实际结果（`rating>=3`）分桶对齐，给出 `bias`（平均预测−实际）与 `brier`。

- 口径是重点：**只算 `state_before='review'` 且 `retention_before>0`**。小白这一场复习了 4 次，全部是新卡首答，`samples=0` —— 卡片老实显示"还没有可比的复习"，而不是拿 4 条无意义的样本画一张图。
- `bias` 与 `brier` 都要给：只看 bias 会被"高估低估互相抵消"骗过去（测试里 0.9/0.4 那组 bias 0.15、brier 0.085 就是这个形状）。
- 样本 < 20 拒绝下结论，分桶样本 < 3 不报 gap。
- 11 个单元测试 + 1 个 HTTP 测试；229 项全绿。

### 18.2 真的帮上忙的地方

1. **苏格拉底的第一问真的在诊断，不是提问模板**：
   > 如果我说"C 到 E 是三度"，你能不能先告诉我：这个"三度"具体是怎么数出来的？

   我答"大三度就是有四个音？我以为是数手指"——它没有纠正我，也没有给答案，而是把问题缩小：
   > 从 C 到 E，中间你唱的时候，会经过 C、D、E 这三个音名，对吧？……你数一下，一共几个音名？

   我说"不太懂，你能别说术语，用大白话再讲一次吗"，下一轮它真的换成了数音名。**"先答后示"这条纪律在真实模型下是守住的。**
2. **费曼这一刀砍得准**。我交了一段糊话（"音程就是距离，像尺子量出来的""解决就是回到舒服的音"），拿到 **讲解分 28**，六个维度各自带理由，其中两条正好是要害：
   > 通俗度 50：语言本身简单，适合小孩，但用"尺子量""别扭"等比喻反而模糊了音程的双维度本质。
   > 类比 20："尺子量距离"只强调"距离"，忽略了音程是"度数+音数"两个维度，类比不完整甚至误导。

   它没有因为"话说得通俗"就给高分，反而指出**类比会替代定义**——这正是新人最需要听到的一句话。
3. **评分按钮上直接写着代价**：`忘了 1 分钟 / 模糊 5 分钟 / 记得 10 分钟 / 很简单 16 天`。我第一次复习就明白了"评分会改变排程"，不需要读任何说明。
4. **合意困难是可感的**：先写回忆才给答案，我写的那句"根音三音五音谁在上谁在下我说不清"被存进「回忆记录」——下次复习能对照，这种"当时的自己"很有用。

### 18.3 真的卡住人的地方（按严重程度）

1. **答案就贴在考卷旁边**。苏格拉底页右侧常驻写着
   > 这个单元的本质：音程由度数（音级数量）和音数（全音个数）共同定义，缺一不可
   > 评分关键点：度数由音级数量决定 / 音数是全音个数，半音=0.5 / E-F和B-C是半音 / 必须双维度描述音程

   我第一问还没答，答案已经在那儿了。新人要么抄，要么觉得"你在考我刚才读过的那几行"。**这条与"我不替你完成思考"直接冲突**，是本次最大的问题。
2. **仪表盘在给新人虚假的自信**。学完 38 分钟，系统同时显示：

   | 指标 | 显示 | 真相 |
   |---|---|---|
   | 记忆健康度 | **100%** | 刚复习完的卡 R 当然接近 1，公式没错但毫无信息量 |
   | 30 天保留率 | **75%** | 分母只有 4 次复习，且都排在"内容还热"的时候 |
   | 费曼讲解分 | 28 | 我其实没懂 |
   | 掌握度 | 9.8%（apply 0%） | 同上 |

   前两个数技术上都不算错，但新人只会看到"我状态很好"。**指标必须成对出现**，否则就是自我安慰机。
3. **复习页顶部三行数字互相矛盾**：`0 张到期`、`17 张新卡`、`本轮到卡 0 / 17`。我第一反应是坏了。人话应该是"今天没有到期的复习，先来学 17 张新卡"。
4. **第一次点「模糊」像没反应**（已修）：要求先选错因这件事，只靠一行小字 + 0.32 秒抖动，关掉动效就基本看不见。现在会明确弹一句"先选一个错因，或者再点一次评分直接跳过"。
5. **AI 给我的材料描述里塞了压缩术语**：它把这一章概括成"学习音程度数、性质、协和性及**三七和弦**构成与解决"——`37 和弦`是数字低音记谱法的行话，新人读到只会更慌；目标名还被填成"我的学习材料"这种没用的话。

### 18.4 由此排进计划的三件事

| 优先级 | 做什么 | 为什么 |
|---|---|---|
| P0 | 苏格拉底页的「本质 / 评分关键点」在第一答之前折叠，答完再展开，并在折叠处写明"这是评分依据，先别看" | 现在它和"我不替你完成思考"自相矛盾；这是产品最核心的承诺 |
| ~~P1~~ **已做** | 记忆健康度旁边给出「剔掉今天复习过的卡」的口径（`memory_health_settled`），「30 天保留率」改名「复习答对率」并标样本数，文案写清两个数都不回答"学会了没有" | 单独看会让新人高估自己，而这恰恰是间隔重复最该防的"熟悉感错觉" |
| ~~P1~~ **已做** | 复习页顶部改成人话：到期为 0 时显示「今天没有到期的复习」+「先来 N 张新卡」，「本轮到卡」改成「本轮进度」 | 三行自相矛盾的数字会让人以为产品坏了 |
| ~~P2~~ **已做** | `meta_suggest.md` 加约束：概括里不许出现材料没有的行话/记谱缩写，目标名必须带主题词 | 新人读到"三七和弦构成与解决"只会更慌 |

**一句话体感**：AI 的三件事（追问、评分、排程）对新人是真有用的，尤其费曼那一刀；但产品的"仪表盘"目前站在鼓励而不是说实话的一边，而最该守的"不替你想"在界面上被自己右侧栏剧透了。
