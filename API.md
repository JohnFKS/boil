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

- `goal`、`plan`（今日、策略、里程碑、前 14 天）
- `counts`（units/boiled/learning/new/rusting/due）
- `mastery`（四维平均、最弱维度）
- `statuses`（每个单元的状态与到期数）
- `next_units`、`next_action`
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

### `GET /api/units/{unit_id}`

返回：

- `unit`（含 steps/boundaries/misconceptions/faq/key_points/source_ref）
- `mastery`、`status`、`probes`、`cards`、`sessions`、`relations`、`attempts`

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

### `POST /api/packs/compile`

```json
{
  "title": "第 3 章 导数",
  "subject": "高等数学",
  "text": "……",
  "use_llm": true
}
```

返回：

```json
{
  "pack": {"schema_version": "1.0", "id": "user.…", "units": []},
  "summary": {},
  "warnings": []
}
```

- `use_llm=true` 且配置了模型：LLM 结构化抽取；失败显式降级并给出 warning。
- 无模型 / `use_llm=false`：本地确定性编译器。
- 编译会保存用户 Pack 到 `data/packs/`，可以直接用于 onboarding。

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
{"rating": 3, "elapsed_ms": 6000}
```

`rating`：1 忘了 / 2 模糊 / 3 记得 / 4 很简单。

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

## 8. 洞察

### `GET /api/insights?goal_id=...`

返回：

- `mastery`（四维）
- `counts`
- `plan_progress`
- `retention`（近 30 天，按天聚合）
- `forecast`（未来 7 天到期，不含新卡）
- `heatmap`（56 天）
- `stats`（今日/本周/连续/30 天保留率）
- `gaps`（来自苏格拉底报告）
- `next_action`
- `recent_sessions`

---

## 9. 设置与模型

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
  "temperature": 0.5,
  "max_tokens": null,
  "timeout": 120
}
```

- `api_key` 省略：保持现有 Key。
- `api_key: ""`：清除 Key，回到演示模式。
- 响应为新的 public config；Key 永远不会回传。

### `POST /api/settings/llm/test`

发送一个极短的 ping。成功：

```json
{"ok": true, "latency_ms": 532, "model": "deepseek-chat", "reply": "OK"}
```

失败：`400`，`error.code` 可能是 `not_configured` / `network` / `http_error` / `empty_content`。

---

## 10. 搜索、事件、导出

### `GET /api/search?q=学习`

```json
{"units": [], "packs": []}
```

### `GET /api/events?after=0&limit=30`

返回追加式事件；`after` 是 `seq`。

### `GET /api/export`

下载 JSON 文件（`Content-Disposition: attachment`），包含目标、计划、Pack、单元、卡片、复习日志、会话、消息、证据、掌握度、解释、事件、学习日。
**不包含** API Key。

### `POST /api/reset`

```json
{"confirm": "RESET"}
```

清空当前用户数据。默认仅本机允许；非本机部署需要 `BOIL_ALLOW_RESET=1`。

---

## 11. 错误码

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

## 12. curl 示例

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
