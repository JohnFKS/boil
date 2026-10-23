# Boil HTTP API

Base URL：`http://127.0.0.1:7860`

所有 JSON 响应使用统一信封：

```json
{"ok": true, "data": { }}
```

```json
{
  "ok": false,
  "error": {
    "code": "bad_request",
    "message": "人话错误信息",
    "detail": "可选的技术细节"
  }
}
```

- 默认单用户 `local`。
- 如果设置了 `BOIL_TOKEN`，所有 `/api/*` 请求需要：
  - `Authorization: Bearer <token>` 或
  - `X-Boil-Token: <token>`
- 请求体上限 12 MB；非 GET 请求用 `Content-Type: application/json`。
- 时间一律 ISO 8601 UTC；计划日期使用服务器本地日期。
- 所有响应带安全头：CSP、`X-Frame-Options: DENY`、`Referrer-Policy: no-referrer`、`Permissions-Policy`、`X-Content-Type-Options: nosniff`。

---

## 1. 健康与用户

### `GET /api/health`

```json
{
  "app": "Boil",
  "version": "1.0.0",
  "status": "ok",
  "mode": "demo",
  "llm": { "mode": "demo", "enabled": false },
  "packs": 2,
  "data_dir": "/app/data",
  "uptime_seconds": 12,
  "time": "2026-10-21T05:00:00Z"
}
```

### `GET /api/me`

```json
{
  "user": {"id": "local", "name": "学习者"},
  "goals": [],
  "active_goal": null,
  "llm": {},
  "token_required": false,
  "version": "1.0.0"
}
```

### `GET /api/goals`

返回用户、目标列表与当前目标；每个目标带 pack 进度。

---

## 2. 目标与计划

### `POST /api/onboarding`

创建一个目标，安装内容包，生成计划。

```json
{
  "name": "阿学",
  "pack_id": "boil.xueshen.learning",
  "goal_title": "掌握学习力",
  "deadline": "2026-12-31",
  "daily_minutes": 40,
  "level": "beginner"
}
```

也可以直接提交材料（后端会编译为 Pack）：

```json
{
  "name": "阿学",
  "goal_title": "导数与微分",
  "subject": "高等数学",
  "material_title": "第 3 章 导数",
  "material": "## 定义\n……",
  "daily_minutes": 40,
  "deadline": "2026-12-31"
}
```

返回完整的 dashboard 数据；如果编译有降级，会带 `warnings`。

### `GET /api/dashboard?goal_id=...`

返回：

- `goal`、`plan`（今日、策略、里程碑、大纲 `outline`、前 14 天）
- `counts`（units/boiled/learning/new/rusting/due）
- `mastery`（四维平均、最弱维度）
- `statuses`（每个单元的状态与到期数）
- `next_units`、`next_action`
- `digest` / `study_plan_phases` / `generated_by`：编译时 AI 读完整份材料后生成的总览与阶段计划（存在 goal 的 meta 里），`generated_by` 为 `model` 或 `rules`
- `activity`（事件流）
- `map_preview`
- `llm`（模式与错误）

### `POST /api/plan/rebuild`

```json
{
  "goal_id": "goal_xxx",
  "title": "掌握学习力",
  "daily_minutes": 55,
  "deadline": "2026-11-30"
}
```

只重排尚未“烧开”的单元；已烧开单元不再进入新学队列，但仍按 FSRS 复习。
返回新的 dashboard。

---

## 3. 地图与单元

### `GET /api/map?goal_id=...`

```json
{
  "goal": {},
  "nodes": [
    {
      "id": "u_xxx",
      "title": "学习的本质是记忆",
      "essence": "……",
      "status": "learning",
      "status_label": "学习中",
      "mastery": 0.16,
      "due_count": 0,
      "facets": {},
      "order_index": 0,
      "tags": [],
      "boundaries": [],
      "key_points": []
    }
  ],
  "edges": [
    {"id": "rel_xxx", "source": "u_a", "target": "u_b", "kind": "prerequisite"}
  ],
  "summary": {}
}
```

### `POST /api/units/{unit_id}/notes`

```json
{"kind": "mnemonic", "content": "复习像压路基，每次提取都压实一点"}
```

`kind`：`mnemonic` / `note`。内容为空会删除该条。返回最新的 notes 字典。

### `POST /api/material/fix`

粘贴清洗：把网页 / Word / PDF / AI 对话里复制来的脏 Markdown 归一。纯函数、不落库。

```json
{"text": "#　提取练习\r\n• 合书复述\r\n公式 \\[x^2\\]", "options": {"bullets": true}}
```

返回 `{"text": "…", "applied": ["换行统一为 LF", "全角空格转半角", "项目符号归一为 -", "LaTeX 定界符转为 $/$$"], "changed": true}`。

- 代码块与行内代码内部一律不动（只处理 `\` ` 之外的文本）。
- `options` 可选，键见 `boil/markdown_fix.OPTION_KEYS`；未传的走默认全开。
- 超过 200,000 字符返回 `bad_request`。清洗失败不阻断粘贴本身（前端会退回原样插入）。

### `POST /api/units/{unit_id}/mnemonic`

让模型给这个单元编**记忆钩子**（口诀/谐音/类比/画面/反差）。无请求体。

```json
{
  "mnemonics": ["口诀：看懂合上写，对照补漏差", "类比：像健身，练（提取）才长肌肉（记忆）"],
  "note": "口诀押韵步骤，类比健身强化",
  "unit_id": "u_…",
  "source": "model",
  "warnings": []
}
```

- 材料由服务端从 `units` 表读取（本质/步骤/边界/常见误解），**不接受前端传来的原文**，避免把任意文本塞进模型。
- 未配置模型：`503 ai_not_configured`；模型没给出可用钩子：`400`。前端在未配置时不渲染这个入口。
- 返回的结果**不会自动写库**：前端填进输入框、用户确认保存后才进入 `unit_notes`（kind=`mnemonic`）。

### `GET /api/units/{unit_id}`

返回：

- `unit`（含 steps/boundaries/misconceptions/faq/key_points/angle/source_ref）
- `mastery`、`status`、`heat`、`heat_label`、`pn`、`records`
- `notes`（速记/笔记）、`related`（语义相关单元）
- `probes`、`cards`、`sessions`、`relations`、`attempts`、`recalls`（复习回忆原文）

### 灵感笔记（划词保存）

前端在任意 `data-selectable` 区域划词或右键，把选中的文字连同想法存成一条跨页面的笔记。与 `unit_notes`（单元内的速记/笔记）是两套东西：`unit_notes` 每个单元每类只有一条，`notes` 可以无限累积并带引用原文。

```
GET  /api/notes?unit_id=…&q=…&limit=200   列表（默认 200，上限 500）
POST /api/notes                           新建
POST /api/notes/delete                    删除，body {"id": "…"}
GET  /api/notes/export                    下载 boil-notes.md
```

> 本服务只实现 `do_GET` / `do_POST`，因此删除用 POST 动作端点，与 `/api/reset` 同一风格。

新建请求：

```json
{
  "quote": "提取一次，记忆痕迹就被强化一次",
  "content": "可以用作每天早上第一个自测题。",
  "unit_id": "u_…（可选，不属于任何单元时省略）",
  "source": "unit",
  "anchor": {"search": "去公式可见文本", "occurrence": 0}
}
```

`anchor` 可选，只在选区跨过 KaTeX 公式时才有：KaTeX 渲染出来的 DOM 文本与 `selection.toString()` 对不上（后者混着 MathML、annotation 与两遍字形），所以前端把选区里的公式换成 `$TeX$` 存进 `quote`，另外存一段「去掉公式后的可见文本」和它在本页出现的序号。列表里只回 `{search, occurrence}`，内部的 `anchor_json` 不外泄；`search` 少于 4 字不存（没有消歧价值），`occurrence` 夹在 0–40。

- `quote` 与 `content` 至少要有一个非空，否则 `bad_request`。
- 长度上限：`quote` 2000 字、`content` 4000 字，超出会被截断而不是报错。
- `unit_id` 不存在时置空；`source` 只接受白名单（`unit`/`socratic`/`feynman`/`review`/`insights`/`dashboard`/`map`/`library`/`notes`），其它值清空。
- `goal_id` 省略时自动挂到当前目标。
- 列表返回 `notes`（含 JOIN 出来的 `unit_title`）、`total`、`goal_id`。
- 灵感笔记会随 `GET /api/export` 一起导出、被 `POST /api/import` 恢复，并会被 `POST /api/reset` 清除。

---

## 4. 内容包

### `GET /api/packs`

```json
{
  "packs": [
    {
      "id": "boil.xueshen.learning",
      "name": "学神模型 · 学习力",
      "version": "1.0.0",
      "unit_count": 8,
      "estimated_minutes": 300,
      "installed": true,
      "cover": "ember",
      "license": "CC-BY-4.0"
    }
  ],
  "installed": {"boil.xueshen.learning": "1.0.0"},
  "goal": {},
  "default_pack": "boil.xueshen.learning"
}
```

### `GET /api/packs/{pack_id}`

返回完整、规范化后的 Pack（含 units、probes、cards、relations、source）。

### 选中内容的三个动作

划词菜单（`selectionMenu.js`）在配置模型后会多出「做卡片」「问 AI」两个动作；两者都是**先预览、确认后才落库**。

```
GET  /api/cards         ?q=&unit_id=&state=&limit=  → 全量卡片清单（复习中途「提前加卡」）
POST /api/cards/preview   {text, unit_id?}      → 预览，绝不写库
POST /api/cards/confirm   {cards, unit_id?}     → 写入 cards，进复习队列
POST /api/ask             {question, context, unit_id?} → 单轮回答
```

`GET /api/cards` 返回 `{"cards": [...], "total": N}`，`limit` 夹在 1–200（默认 60），`state` 只接受 `new/learning/review/relearning`。`/api/ask` 的 `context` 只取选中位置前后各约 800 字，回答会带 `source: "model"`。

`/api/cards/preview` 返回：

```json
{
  "status": "preview",
  "cards": [{"front": "…", "back": "…"}],
  "engine": "model",
  "engine_note": "由模型基于选中文字提炼；确认前不会进入复习队列。",
  "unit_id": "u_…", "unit_title": "提取练习",
  "tool_args": {"cards": [], "unit_id": "u_…"}
}
```

- `engine` 为 `model` 或 `rules`：没有模型、或模型失败时回退到确定性抽取（问答 → 配对 → 完形三种形状，只从原文取词），并如实标注。
- 确认时若没带 `unit_id`，会归到当前目标的第一个单元（卡片必须挂单元，否则 FSRS 无法排程）；没有任何目标时报错。
- 选中文字少于 8 字报错；超过 4000 字截断。
- `/api/ask` 需要真实模型，否则 `503 ai_not_configured`；上下文由前端截取选中位置前后各约 800 字。

### 搜索

```
GET /api/search?q=   单元 + 内容包
```

字面命中（标题/本质/标签包含查询串）与语义命中（bge-m3 余弦过阈值）**分级**排序：双命中 > 只字面 > 只语义，同级再按语义分。每条带 `matched_by: ["lexical","semantic"]` 与 `score`。响应含 `fusion: "tiered"` 与 `engine`（实际用了哪个向量引擎）。

这里没有照搬 testlearn 的 RRF：那套是给模型检索讲义片段用的，名次融合会让「只是有点像」的结果挤掉精确命中；给人的搜索框里精确命中必须排在前面。

### 单元合并（合并不是删除）

```
GET  /api/units/duplicates?goal_id=   当前目标里疑似重复的存活单元对
POST /api/units/{unit_id}/merge        {"into": "另一单元id", "reason": "可选", "override": false, "confirmed": false}
POST /api/units/{unit_id}/unmerge      撤销这个单元被吸收的那一次合并
```

候选由**两路信号**提出：字面近乎逐字（`lexical ≥ 0.82`），或云端语义进入候选带（`semantic ≥ 0.74`）。语义分只看**标题 + 一句话本质**（`quality.dup_text`）：把步骤/边界也算进去时（`embeddings.unit_text`），同一主题的单元全部挤在 0.76–0.81，真重复反而排在假候选后面，分数就废了。每对都带 `lexical` / `semantic` / `engine` / `auto` / `same` / `allowed` / `basis` / `decision_reason` / `pending_judgement`，响应还有 `checked`、`judged`、`thresholds`（含 `judge_limit`）。**没有配置嵌入服务时 `semantic` 为 `null`**，`engine` 回 `rules`——绝不拿本地哈希向量冒充语义分。

裁决阶梯（`quality.merge_decision`。实测标题+本质口径：真重复 0.870，而「提取练习 / 测试即学习」这种相关但不同的能到 0.797——只差 0.07，一条线分不开，而合并会按记录重写关系边）：

1. `lexical ≥ 0.62` → 代码直接放行（`basis: "lexical"`）；
2. 其余一律要模型签字：`same: true` 才放行（`basis: "model"`），并且**服务端要求 `confirmed: true`**——模型对同一对两次能给出相反答案，缓存关掉时更是如此。没带 `confirmed` 就报「需要你先确认」且一个字都不改库；
3. 语义低于 0.74 或模型不同意 → 拒绝。但人可以署名坚持：`override: true` **且** `reason` ≥ 4 字才放行，`basis` 记为 `user`，`merged_reason` 存「学习者确认：<原话>」。

一次 `GET /api/units/duplicates` 最多请模型判 `judge_limit`（默认 3）对，按分数从高到低；没判到的带 `pending_judgement: true`，用户点合并时才判（同一问法，命中缓存）。这样打开地图不会变成烧钱。

判定问法按单元 id 定序，所以候选列表渲染过的判定会被内容寻址缓存复用——**页面上说的依据和端点实际用的依据一定是同一个答案**（实测 `calls: 1, hits: 3`）。
- 合并后：被吸收的单元从地图节点与「下一步推荐」中消失，但 `unit_detail` 仍可访问并返回 `merged_into`（归宿）、`absorbed`（吸收了谁，带 reason）、`can_unmerge` 与 `unmerge_blocked_reason`；前置关系自动重连到归宿，自环与重复边被清掉；复习卡保留在原处继续到期。
- 链式合并会自动指向最终归宿；会形成环或互相合并的请求被拒绝。

**撤销合并**（`POST /api/units/{id}/unmerge`，body 可为空）：把指向恢复为空，并按合并时写进 `units.merged_op` 指向的事件记录**回滚关系边**——被改指到归宿的边回到原指向，因自环/去重被删的边重新插入（若等价边已存在或端点已不存在则跳过，响应里的 `relations_restored` / `relations_skipped` 会如实给出数量）。

三条闸（不满足就 400 并说明原因，不猜着改）：
1. 归宿本身还活着（没被并进别人）；
2. 这个单元在被吸收之后没有又收过别的单元；
3. 合并记录还在。旧版本或导入来的合并可能没有记录：这时仍然撤销指向，`degraded: true` 且 `note` 明说「关系边保持现状」——不会假装恢复了。

`unit_detail` 的 `recalls` 是最近 6 条 `attempts(kind="review_recall")`（复习时写的回忆原文 + 分数 + 时间），单元页据此渲染「我的回忆记录」。

### 专注（番茄钟）

```
POST /api/focus/start    {"minutes": 25, "unit_id": "可选"}   → {session, minutes, started_at}
POST /api/focus/finish   {"session": "...", "minutes": 25, "unit_id": "...", "outcome": "completed|abandoned"}
GET  /api/focus          今天的完成情况 {sessions, minutes, recent}
```

- 开始不落库，结束才记；**只有 `completed` 计入 `study_days` 的学习时长与次数**。提前结束记为 `abandoned`，只留下一条审计事件，不冒充完成。
- `session` 同时是事件 id（`fo_<session>`）：重复提交（比如刷新后补记）返回 `duplicate: true`，不会把 25 分钟记两遍。
- 前端顶栏的「专注」按钮启动倒计时，时长可选 15 / 25 / 45 分钟；刷新页面会从 `localStorage` 恢复，走完自动提交完成。

### 学习风格测试

```
GET  /api/styles/questions   5 道情境题（不下发权重，避免暗示「正确」选项）
POST /api/styles/apply       {"answers": [0,1,0,2,1]}
```

返回 `{"goal_id": "…", "persona": {"key","label","strategy","secondary","secondary_label","scores","answers","taken_at","source"}}`，写入 `goals.meta_json.persona`。

- 苏格拉底（开局问题与每轮追问）与费曼评估的提示词会追加一段「这位学习者的带法」；没测过就完全不追加。
- 答案数量或取值不合法返回 `bad_request`；重测直接覆盖旧人设。

### `POST /api/packs/compile`

```json
{
  "title": "第 3 章 导数",
  "subject": "高等数学",
  "text": "补充粘贴内容（可选）",
  "documents": [
    {"name": "讲义.md", "text": "……"},
    {"name": "笔记.txt", "text": "……"}
  ],
  "use_llm": true
}
```

`documents` 可选，用于把多个文件合并到同一个主题：后端会按文件名添加二级标题，再接上 `text` 作为“补充笔记”。最多 20 个文件，合并后上限 200,000 字符；空文档会忽略。

返回：

```json
{
  "pack": {
    "schema_version": "1.0", "id": "user.…", "units": [],
    "outline": [{"title": "第一部分", "summary": "为什么先学它", "unit_titles": ["…"]}],
    "study_plan": [{"phase": "地基", "unit_titles": ["…"], "focus": "本阶段目标", "minutes": 180}],
    "digest": {"overview": "150 字以内的材料总览", "advice": ["学习建议"]},
    "generated_by": "model"
  },
  "summary": {},
  "warnings": []
}
```

- `use_llm=true` 且配置了模型：LLM 结构化抽取；失败显式降级并给出 warning。
- 无模型 / `use_llm=false`：本地确定性编译器。
- 使用 `documents` 时，Pack 的 `source.documents` 会记录文件名与字符数，`source.material_chars` 记录合并长度。
- 编译会保存用户 Pack 到 `data/packs/`，可以直接用于 onboarding。
- 响应里另有 `quality`：`{issues, repaired, near_duplicates, engine}`。质检先跑确定性检查（空本质、重复标题、步骤里混具体数字、缺探针类型、正反面相同的卡片、近似重复单元对），只有需要判断力的残差才交给模型；能安全修的字段会自动补上并计入 `repaired`，**补丁等于原文时跳过**，避免无意义地翻动版本与缓存键。
- 材料超过 **6,000** 字符时自动分批（`SINGLE_CALL_LIMIT`）。这条线按实测的输出能力定，不按上下文长度定：8.6k 字的材料单次调用能出可用 JSON，10.2k 字就在尾部被截成 `"difficulty":4,":5,":":40"`，能不能修好变成掷硬币。旧写法是 24,000。
- 分批：先用全篇骨架出大纲，再逐批只针对本批出单元，因此每一节都真的进过模型；单批失败只跳过该批并留 warning。`source.batches` 与 `source.material_chars` 会记下实际批数与原始长度。
- 装不下预算的超长小节按「空行 → 句号 → 硬切」无损切开再把小块装回预算内，**不截断、不丢字**；单元数超过上限（单次 10 个、分批 40 个）时会在 `warnings` 里明说丢了几个，回退到单次调用而不得不截断正文时同样会给出 warning。对用户承诺「读完全部材料」，就不能静默扔内容。
- `outline` / `study_plan` / `digest` 两条路径都会生成，形状一致；`generated_by` 标明是 `model` 还是 `rules`，前端据此显示「AI 分析」或「本地规则」。大纲顺序会作为 `planner.build_plan(ai_outline=…)` 的排序依据，但**前置依赖仍由代码拓扑排序保证**，日期算术也始终由代码完成。
- 每个单元带 `angle`：一句话说明这个单元该怎么学。

### `POST /api/packs/suggest`

导入材料后、编译前就能用的元信息生成（标题 / 学科 / 目标名 / 一句话描述）。

```json
{"text": "粘贴内容（可选）", "documents": [{"name": "讲义.md", "text": "……"}]}
```

返回：

```json
{
  "title": "提取练习与复习策略",
  "subject": "学习科学",
  "goal_title": "掌握高效记忆与迁移",
  "description": "一句话说明这份材料最值得学什么",
  "source": "model"
}
```

- 与编译共用合并规则（`documents` + `text`），少于 60 字符返回 `bad_request`。
- 未配置模型或模型失败时回退本地规则：取第一个 Markdown 标题或首行，`source` 为 `rules`。
- 只读操作：不落库、不创建 Pack，前端只回填空字段。

### `POST /api/packs/import`

```json
{"pack": { "id": "x", "name": "x", "version": "1.0.0", "units": [] }}
```

校验通过后保存到 `data/packs/`；返回 `pack` 与 `summary`。校验失败返回 `pack_error`。

---

## 5. 苏格拉底对话

### `POST /api/socratic/start`

```json
{"unit_id": "u_xxx", "goal_id": "goal_xxx"}
```

`goal_id` 可省略（后端从 unit 推导）。返回：

```json
{
  "session": {
    "id": "ss_xxx",
    "status": "active",
    "stage": "diagnose",
    "turn": 0,
    "meta": {},
    "messages": [
      {"id": "msg_xxx", "role": "assistant", "content": "……", "created_at": "…"}
    ]
  },
  "unit": {},
  "report": null,
  "stage_label": "诊断起点"
}
```

### `POST /api/socratic/stream`（SSE）

请求：

```json
{"session_id": "ss_xxx", "message": "我的回答……"}
```

响应 `Content-Type: text/event-stream`：

```
event: status
data: {"text":"正在理解你的回答…"}

event: delta
data: {"text":"你提到了……"}

event: delta
data: {"text":"为什么它成立？"}

event: done
data: {"session":{...},"report":null,"engine":"demo"}

event: close
data: {}
```

可能事件：

| 事件 | 说明 |
|---|---|
| `status` | 思考/整理状态 |
| `delta` | 增量文本 |
| `done` | 本轮完整 session；如果自动收束，带 `report` |
| `error` | 错误；前端应显示并允许重试 |
| `close` | 流结束 |

第 6 次学生作答后自动生成报告并收束。

### `POST /api/socratic/finish`

```json
{"session_id": "ss_xxx"}
```

提前结束并生成报告：

```json
{
  "session": {},
  "report": {
    "summary": "……",
    "understood": [],
    "gaps": [],
    "misconceptions": [],
    "facets": {"recall": 0.8, "explain": 0.7, "apply": 0.6, "transfer": 0.5},
    "next_step": "……",
    "confidence": 0.8,
    "engine": "live"
  }
}
```

### `GET /api/socratic/{session_id}`

返回会话、消息、单元摘要与报告。

---

## 6. 费曼评估

### `POST /api/feynman/evaluate`

```json
{
  "unit_id": "u_xxx",
  "mode": "child",
  "text": "讲给 10 岁小孩的内容……"
}
```

`mode`：`child` / `peer` / `examiner`。

返回：

```json
{
  "overall": 0.66,
  "dimensions": {
    "correctness": {"score": 0.78, "reason": "关键点覆盖较高"},
    "completeness": {"score": 0.6, "reason": "覆盖了约 60% 的关键点"},
    "structure": {"score": 0.54, "reason": "检测到 2 个因果/结构连接词"},
    "simplicity": {"score": 1.0, "reason": "句子较短，较易理解"},
    "boundary": {"score": 0.36, "reason": "提到 1 个边界/条件标志"},
    "analogy": {"score": 0.45, "reason": "缺少有效类比"}
  },
  "gaps": [],
  "jargon": [],
  "model_answer": "……",
  "followups": [],
  "next_step": "……",
  "engine": "demo",
  "note": "……",
  "mode": "child",
  "mode_label": "讲给 10 岁小孩",
  "unit_id": "u_xxx",
  "unit_title": "……",
  "text": "……",
  "created_at": "……"
}
```

---

## 7. 复习

### `GET /api/review?limit=30&unit_id=...`

```json
{
  "cards": [
    {
      "id": "c_xxx",
      "unit_id": "u_xxx",
      "unit_title": "……",
      "front": "学习的本质是什么？",
      "back": "……",
      "state": "new",
      "due_at": "……",
      "due_label": "新卡",
      "preview": {"1": "1 分钟", "2": "5 分钟", "3": "10 分钟", "4": "16 天"},
      "retention": 0
    }
  ],
  "due_total": 0,
  "new_total": 16,
  "stats": {}
}
```

- `due_total` 不包括新卡；`new_total` 单独计数。
- 卡片列表包含到期卡与新卡。

### `POST /api/review/{card_id}`

```json
{"rating": 3, "elapsed_ms": 6000, "recall": "提取练习就是合上书用自己的话讲出来。", "mistake_tags": ["concept", "boundary"]}
```

`rating`：1 忘了 / 2 模糊 / 3 记得 / 4 很简单。

响应里带 `undo`：`{op, rating, interval_label, state_before, mastery_before, mastery_after}`，`op` 就是撤销凭据。

### `POST /api/reviews/undo`

```json
{"op": "ev_xxx"}
```

撤销最近一次评分：卡片状态（state/step/stability/difficulty/due_at/last_review/reps/lapses/标签）整行还原，删掉那条 `review_logs`、对应的 `review_recall`、火候记录与 mastery 证据，并把 `study_days.reviews` 减一（同一天才减）；掌握度是 EMA 状态量，所以评分时就把快照存进 `review.rated` 事件，撤销时整行还原而不是「减掉一条证据」。

只允许撤**该单元最近一次**复习：同一单元之后若又复习过，快照已经作废，直接 400 并说明原因（用 `review_logs` 的 rowid 判先后，避免同秒时间戳分不出来）。同一个 `op` 不能撤两次。

- `recall` 可选：翻面前写的回忆草稿，非空时以 `kind="review_recall"` 写入 `attempts`（空白不写）。它不带 `probe_id`，所以不会被探测题的「N 次作答」计数吸收；来源探测题记在 `feedback_json.probe_id`。
- `mistake_tags` 可选：错因标签，取值固定为 `concept`（概念没吃透）/ `boundary`（条件边界）/ `step`（步骤缺一环）/ `confusion`（记混）/ `carelessness`（粗心笔误）/ `blank`（完全没印象）；未知标签丢弃、去重、最多 4 个。**只有 1/2 会被记录**——答对后卡片上的旧标签会清空，避免挂着过期错因。
- 标签清单由 `GET /api/review` 的 `mistake_tags`（`[{key,label}]`）下发，前端不自己复制一份。

```json
{
  "card": {},
  "result": {
    "state": "learning",
    "due_at": "2026-10-21T05:10:00Z",
    "interval_label": "10 分钟",
    "interval_days": 0.0069,
    "retention_before": 0
  },
  "mastery": {},
  "remaining": 15
}
```

---

## 8. 火候建议

### `GET /api/advice?minutes=30&goal_id=...`

返回**一个**建议：

```json
{
  "id": "adv_...",
  "mode": "socratic",
  "level": 2,
  "mode_label": "讨论",
  "feature": "苏格拉底对话",
  "cta": "开始对话",
  "title": "第 2 档 · 苏格拉底对话",
  "reason": "「学习的本质是记忆」现在 T=28°。上次报告还缺「输出比输入更强」。……",
  "cost_minutes": 12,
  "minutes": 30,
  "target": {"id": "u_...", "title": "学习的本质是记忆", "heat": 28, "heat_label": "温热"},
  "heat": 28,
  "pn": {"total": 4, "missing": ["输出比输入更强"], "source": "socratic", "complete": false},
  "ladder": [
    {"level": 2, "mode": "socratic", "label": "讨论", "feature": "苏格拉底对话", "active": true},
    {"level": "gate", "mode": "benchmark", "label": "非档位", "feature": "极致对标", "active": false}
  ],
  "href": "#/unit/u_..."
}
```

`mode` 可为 `audio` / `digest` / `socratic` / `recall` / `feynman` / `benchmark` / `done` / `onboarding`。
`pn.missing` 是最近一次苏格拉底/费曼报告里明确说缺失的步骤。

### `POST /api/advice/feedback`

```json
{"action": "accepted", "advice": {...}}
```

`action`：`accepted` 或 `dismissed`。会写 `advice.accepted` / `advice.dismissed` 事件，供未来策略学习。

## 9. 洞察

### `GET /api/insights?goal_id=...`

返回：

- `mastery`（四维）
- `counts`
- `plan_progress`
- `average_heat`（平均火候）
- `records`（最近 BoilRecord）
- `retention`（近 30 天，按天聚合）
- `forecast`（未来 7 天到期，不含新卡）
- `heatmap`（56 天）
- `memory_health`（所有已学卡此刻的平均可提取性；没有已学卡片时为 `null`，前端显示「—」）与 `learned_cards`
- `mistakes`（`{tags: [{key,label,count}], tagged_reviews, missed_total, sample}`）：最近 200 次复习里**用户自己标过**的错因分布。口径是全局（所有目标），且只统计标了错因的那几次——洞察页的「错因分布」卡片会把这两个数直接写在下面，避免读成「全部复习都这样」。
- `stats`（今日/本周/连续/30 天保留率）
- `gaps`（来自苏格拉底报告）
- `next_action`
- `recent_sessions`

### `POST /api/insights/synthesize`

把**材料总览 + 全部学习数据**一起交给模型，生成一段本周解读。请求体可选 `{"goal_id": "…"}`。

```json
{
  "summary": "本周综合解读",
  "suggestions": ["可执行建议"],
  "risks": ["风险提醒"],
  "based_on": {"digest": true, "units": 5},
  "source": "model",
  "warnings": []
}
```

- 送进模型的统计只有数字摘要（单元数、已烧开/学习中/生锈、平均热度、计划完成度、30 天保留率、最近缺口）与最弱三个单元，控制在 2k 字内。
- 未配置模型或模型失败：回退规则文案，`source` 为 `rules`，并把失败原因写进 `warnings`——不会静默冒充 AI。
- 结果不落库；前端点击「生成解读」时才调用（模型路径可能需要十几秒）。

---

## 10. 设置与模型

### `GET /api/settings/llm`

```json
{
  "mode": "demo",
  "enabled": false,
  "base_url": "https://api.openai.com/v1",
  "model": "gpt-4o-mini",
  "deep_model": "gpt-4o-mini",
  "api_key_set": false,
  "api_key_masked": "",
  "providers": [],
  "last_error": null,
  "forced_off": false
}
```

### `POST /api/settings/llm`

```json
{
  "base_url": "https://api.deepseek.com/v1",
  "api_key": "sk-...",
  "model": "deepseek-chat",
  "deep_model": "deepseek-reasoner",
  "auth_header": "Authorization",
  "auth_prefix": "Bearer ",
  "extra_body": {},
  "temperature": 0.5,
  "max_tokens": null,
  "timeout": 120
}
```

- `api_key` 省略：保持现有 Key。
- `api_key: ""`：清除 Key，回到演示模式。
- `auth_header` 默认 `Authorization`；`auth_prefix` 默认 `Bearer `。
  Dots 使用 `auth_header: "api-key"`、`auth_prefix: ""`。
- `extra_body` 会合并进请求体（不会覆盖 `model/messages/stream/max_tokens/temperature/response_format` 等核心字段）。
  Dots 可用 `{"chat_template_kwargs":{"enable_thinking":false}}` 控制深度思考。
- 响应为新的 public config；Key 永远不会回传。

### `GET /api/settings/embeddings`

返回 Embedding 配置（Key 只给掩码）；`mode` 为 `cloud` / `fallback` / `local`，`last_error` 可见。

### `POST /api/settings/embeddings`

```json
{
  "base_url": "https://api.siliconflow.cn/v1",
  "model": "BAAI/bge-m3",
  "api_key": "sk-...",
  "timeout": 30
}
```

- `api_key` 省略：保持现有 Key；空字符串：清空并回退本地向量。
- 返回公开配置，不含 Key。

### `POST /api/settings/embeddings/test`

实测一次云端 `/embeddings`，返回维度、耗时与模型；失败返回 `400 embedding_error`，不会回传 Key。

### `POST /api/settings/llm/test`

发送一个极短的 ping。成功：

```json
{"ok": true, "latency_ms": 532, "model": "deepseek-chat", "reply": "OK"}
```

失败：`400`，`error.code` 可能是 `not_configured` / `network` / `http_error` / `empty_content`。

`POST /api/settings/llm` 另外接受两个布尔开关：`cache_enabled`（结构化调用缓存）与 `stream_upstream`（是否向上游要流式再拼回，默认开）；失败记录的 `last_error` 现在带 `hint`（可行动建议）、`base_url`、`model` 与 `api_key_masked`，且回显内容里的 Key 会被掩码后再落盘。

### `POST /api/settings/llm/cache/clear`

清空内容寻址缓存，返回 `{"removed": 12, "cache": {"calls":12,"hits":5,"writes":12}, "entries": 0}`。

缓存只作用于 `chat_json`（编译、大纲、元信息、速记、洞察解读）：键为 `sha256(模型 + messages + 温度 + max_tokens)`，命中直接复用原始文本再解析，因此同样材料重跑**零 token、零漂移**（实测 1.15s → 0ms）。流式对话带会话状态，不缓存。缓存键含 `base_url`：同名模型换网关不会复用另一家的输出。**只有解析成功的结果才写缓存**；命中到坏条目（截断、占位串）会删掉该条目并回落到真实调用，一次失败不会被固化成永久失败。缓存有上限（`CACHE_MAX_ENTRIES = 800`）：写入后超过上限就按文件 mtime 淘汰到九成，不会无限吃盘。

开关随 `POST /api/settings/llm` 一起保存（`cache_enabled`，默认开）；`GET /api/settings/llm` 与 `/api/health` 会回 `cache`（calls/hits/writes）与 `cache_entries`。缓存落在 `data/llm_cache/`（gitignore 内），API Key 永不进入缓存内容。

---

## 11. 搜索、事件、导出

### `GET /api/search?q=学习`

```json
{"units": [], "packs": [], "engine": "local-hash-256"}
```

单元结果带 `score`；`engine` 为实际向量模型名（如 `BAAI/bge-m3`，失败降级时为 `local-hash-256`）。

### `GET /api/events?after=0&limit=30`

返回追加式事件；`after` 是 `seq`。

### `GET /api/export`

下载 JSON 文件（`Content-Disposition: attachment`），包含目标、计划、Pack、单元、卡片、复习日志、会话、消息、证据、掌握度、解释、火候记录、速记笔记、事件、学习日。
**不包含** API Key。

### `GET /api/export/anki`

下载 Anki 可导入的制表符分隔文本（`text/tab-separated-values`），列顺序为正面、背面、标签；
文件头包含 `#separator:tab`、`#html:true`、`#tags column:3`。背面附带来源单元与口诀（若有）。
导入方式：Anki → 文件 → 导入 → 选择 `boil-anki.txt` → 字段分隔选“制表符”。

### 导入前的数据库快照

`POST /api/import` 在写入前会用 SQLite 备份 API 把当前库快照到 `data/backups/pre_import_<时间戳>.db`（保留最近 10 份），响应的 `snapshot` 字段给出文件名；快照失败时导入仍会继续，但会在 `snapshot` 里为 `null`，前端提示先手动导出。用文件复制不行——WAL 模式下复制会拿到不一致的快照。

请求体就是导出文档本身（也接受 `{"data": {...}}` 的包法）；`mode=replace` 还必须带 `confirm: "IMPORT"`。

`mode=replace` 的清库与写入在**同一个事务**里：任何一条插入失败都整体回滚，不会留下「旧的已删、新的没进来」的空库（真实模拟里用一行 `front: null` 的卡验过：报 400、四张表计数一条不少、坏行也没进去）。备份是用户可以手改的东西，所以数据库层的合法性错误统一映射成 `400 import_conflict` 并带上「本次导入已整体回滚，原有数据未受影响」，而不是甩一个 500「服务器内部错误」。当前没有从快照恢复的端点，快照要靠文件替换手工回退。

### `POST /api/import`

```json
{"data": {"goals": [], "units": [], "...": "..."}, "mode": "merge"}
```

- `mode`：`merge`（按 ID 合并/更新）或 `replace`（先清空当前用户数据再导入）。
- `replace` 必须带 `"confirm": "IMPORT"`。
- 返回各表导入计数与 `goals/units/cards/records/notes` 摘要。
- 只导入白名单表与列；`llm` 配置和 API Key 永远不会导入。

### `POST /api/reset`

```json
{"confirm": "RESET"}
```

清空当前用户数据。默认仅本机允许；非本机部署需要 `BOIL_ALLOW_RESET=1`。

---

## 12. 错误码

| HTTP | code | 说明 |
|---|---|---|
| 400 | `bad_request` / `bad_json` / `pack_error` | 请求/内容包校验失败 |
| 401 | `unauthorized` | 需要 BOIL_TOKEN |
| 403 | `forbidden` | 无权重置/非本机 |
| 404 | `not_found` | 接口/单元/会话不存在 |
| 413 | `too_large` | 请求体过大 |
| 502 | `llm_error` | 模型服务/网络/空正文 |
| 500 | `internal` | 未预期错误（服务端日志有堆栈） |

---

## 13. curl 示例

```bash
# 健康
curl http://127.0.0.1:7860/api/health

# 创建目标
curl -X POST http://127.0.0.1:7860/api/onboarding \
  -H 'Content-Type: application/json' \
  -d '{"name":"阿学","pack_id":"boil.xueshen.learning","goal_title":"掌握学习力","daily_minutes":40}'

# 开始苏格拉底
curl -X POST http://127.0.0.1:7860/api/socratic/start \
  -H 'Content-Type: application/json' \
  -d '{"unit_id":"u_xxx"}'

# 流式回答
curl -N -X POST http://127.0.0.1:7860/api/socratic/stream \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"ss_xxx","message":"学习是形成可提取的记忆。"}'

# 复习队列
curl 'http://127.0.0.1:7860/api/review?limit=5'

# 评分
curl -X POST http://127.0.0.1:7860/api/review/c_xxx \
  -H 'Content-Type: application/json' \
  -d '{"rating":3,"elapsed_ms":5000}'
```
