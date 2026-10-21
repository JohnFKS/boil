# Boil 架构

> 目标不是“做一个聊天页面”，而是做一个可持续扩展、可验证学习效果的本地优先学习运行时。

## 1. 边界与非目标

Boil v1 的目标：

- 单用户、本地优先、零运行时依赖。
- 用同一条闭环串起：路径、预消化、苏格拉底、费曼、间隔复习。
- 把“可变内容”和“稳定内核”分开，让内容/提示词/评分器可以独立升级。
- 所有失败可见；所有关键行为留下事件与证据。

v1 明确不做：

- 多租户 SaaS / 计费 / SSO。
- K12 学科培训。
- 自研基础模型。
- 微服务、消息队列、复杂前端构建链。
- 用“AI 讲解”代替学习者提取。

## 2. 代码地图

```
boil/
├── run.py                     # 入口
├── boil/
│   ├── server.py              # HTTP / SSE / 静态 SPA
│   ├── services.py            # 用例层：onboarding、dashboard、review、insights…
│   ├── db.py                  # SQLite schema 与访问层
│   ├── events.py              # 追加式事件日志
│   ├── packs.py               # LearningPack 校验 / 安装 / 查询
│   ├── compiler.py            # 材料 → Pack（LLM + 本地降级）
│   ├── planner.py             # 前置 DAG + 时间预算 + 最少必要路径
│   ├── socratic.py            # 苏格拉底状态机 + 流式 + 报告
│   ├── feynman.py             # 费曼六维评分 + 缺口
│   ├── mastery.py             # 四维证据聚合
│   ├── llm.py                 # OpenAI 兼容网关（普通/流式/JSON）
│   ├── srs/fsrs.py            # FSRS-5 四档调度
│   └── config.py / utils.py
├── content/packs/             # 内置内容包
├── prompts/                   # 苏格拉底 / 费曼 / 编译器提示词
├── static/                    # 自研 SPA（无构建链）+ 本地 KaTeX
├── tests/                     # unittest：核心 / API / LLM / live
├── tools/                     # examgen 导入器、浏览器验收
├── data/                      # 运行时数据（gitignore）
├── Dockerfile / compose / Makefile
└── docs: README / PACK_SPEC / API / SECURITY
```

## 3. 内核与 Pack

### 3.1 内核稳定的东西

- **KnowledgeUnit**：一个可教、可测的最小单元。
- **Probe**：对单元的不同提取方式：recall / explain / apply / boundary / transfer。
- **Evidence**：一次行为产生的证据，带来源、置信度、时间。
- **Mastery**：单元 × 维度 × 分数 × 置信度 × 重复次数。
- **Plan**：目标 + 前置 DAG + 时间预算 → 每天做什么。
- **Card / ReviewLog**：FSRS 的卡片与复习历史。
- **Session / Message**：对话与讲解的容器。
- **Event**：所有上述行为的追加式记录。

内核不包含任何具体学科词汇。它不知道“极限”“二重积分”或“考研”。

### 3.2 可以变的东西

| 类型 | 内容 | 更新方式 |
|---|---|---|
| Subject Pack | 学科本体、编译器提示、评测器、种子内容 | 版本化 Pack |
| Content Pack | 单元、探针、卡片、来源 | 版本化 Pack |
| Skill Pack | 提示词、策略、UI schema、评测集 | 目录/版本 |
| Evaluator Pack | 确定性校验 / LLM judge | 版本化 |
| Prompt | `prompts/*.md` | 文件替换 |
| Model Adapter | OpenAI 兼容 / 本地 | 设置切换 |

`packs.py` 负责：

- `validate_pack`：ID 唯一、必填字段、探针 kind、关系目标存在。
- `normalize_pack`：补默认值、派生 key_points、生成卡片。
- `install_pack`：幂等安装；同版本不重置卡片 SRS；新版本更新内容但保留卡片 ID 与状态。
- 关系重建：按 `order_index` 隐式建立顺序前置，避免没有显式关系的包无法规划。

### 3.3 版本与回滚

- `schema_version`：Pack 格式版本。
- `pack.version`：语义化内容版本。
- `goal_packs`：目标当前安装的版本。
- `packs.manifest_json`：完整快照，便于回滚/审计。
- 安装使用事务：所有单元、探针、卡片、关系在同一事务中；失败整包拒绝。

## 4. 数据流

### 4.1 Onboarding

```
POST /api/onboarding
  → ensure_user
  → 选择/编译 Pack
  → 安装 Pack（事务）
  → units + relations
  → planner.build_plan
  → plans 插入新版本
  → event: goal.created / plan.built / pack.installed
  → 返回 dashboard
```

### 4.2 苏格拉底

```
POST /api/socratic/start
  → sessions(kind=socratic, turn=0, stage=diagnose)
  → 第一问（LLM 或本地追问模板）
  → messages(assistant)

POST /api/socratic/stream (SSE)
  → messages(user)
  → turn += 1
  → stage = diagnose/deepen/boundary/apply/integrate
  → LLM 流式生成，或本地追问
  → messages(assistant)
  → turn >= 6 时：generate_report → evidence × 4 维 → 完成
  → SSE: status / delta / done
```

报告生成：

- 真实模型：`prompts/socratic_report.md` + 单元上下文 + 对话记录 → JSON。
- 演示模式：关键点覆盖 + 因果词/边界词/长度启发式。
- 报告只写“证据”，不写“你真棒”；置信度低时明确说明。
- `session.meta.evidence_added` 防止重复写证据。

### 4.3 费曼

```
POST /api/feynman/evaluate
  → 文本长度校验
  → LLM JSON 评分（live）或六维启发式（demo）
  → explanations 落库
  → evidence(explain, transfer)
  → event: feynman.evaluated
```

六维与权重：

- 正确性 35%
- 完整性 25%
- 结构 15%
- 通俗度 10%
- 边界 10%
- 类比 5%

### 4.4 复习

```
GET /api/review
  → cards due_at <= now（新卡也进入队列）
  → 每张卡用同一 Scheduler.preview 生成四档间隔
POST /api/review/{card}
  → Scheduler.schedule
  → cards 更新 + review_logs 插入
  → evidence(recall)
  → event: review.rated
```

FSRS-5：

- 19 个权重；`DECAY=-0.5`，`FACTOR=19/81`。
- 新卡 → learning 短步；review → 长间隔；again → relearning。
- `preview` 与真实 `schedule` 共用逻辑，避免 UI 显示与实际到期漂移。
- 目标保留率 0.9；最大间隔 100 年；保留率只用于显示与调度，不对外宣称“预测准确”。

## 5. 掌握度

`mastery.add_evidence` 使用带置信度的指数移动平均：

```
alpha = clamp(0.16 + 0.38 * weight * confidence, 0.16, 0.62)
score = old * (1 - alpha) + evidence * alpha
```

四维：

- `recall`：复习评分、recall 探针
- `explain`：苏格拉底报告、费曼
- `apply`：apply 探针（路线图：变式题）
- `transfer`：边界/反例探针、费曼、苏格拉底综合

“已烧开”需要四维都 ≥ 0.8 且总重复 ≥ 4。这个阈值是产品假设，不是科学定论；未来用学习效果基准校准。

## 6. 规划器

输入：单元顺序、前置关系、重要度、估算分钟数、每日分钟数、截止日期。

- 拓扑排序保证前置；环时按 order_index 兜底。
- 每日新内容预算 = 每日分钟 - 复习预算 - 10% buffer；最多 2 个新单元/天。
- 每 7 天安排一个休息日。
- 时间不够时，按重要度选高权重单元，并自动补前置；其余进入 `parked`（暂存）。
- 计划是版本化的；重排从今天开始，不累积历史欠账。

## 7. LLM 网关

`llm.py` 是唯一模型出口：

- 配置：`data/llm.json`（0600）；API Key 不回传。
- 支持：普通、流式 SSE、JSON 模式。
- 兼容：OpenAI / DeepSeek / DashScope / Moonshot / Ollama / 自定义。
- 失败：HTTP / 网络 / 空正文 / 非法 JSON 都有显式错误；`llm_state.json` 记录最近错误。
- JSON 修复：去除代码围栏、截取首尾括号、去尾逗号。
- 无 Key：业务层走本地演示引擎，不在网关里伪造模型回答。
- 真实模型失败：苏格拉底会显示“已切换到本地演示引擎”，报告会带 demo 标记；不会静默冒充 live。

## 8. 前端

- 自研 SPA，ES modules，无构建链；`static/js/app.js` 负责 shell/router/搜索。
- 页面模块：`views/dashboard|map|unit|socratic|feynman|review|library|insights|settings|onboarding.js`。
- 设计系统：`static/css/app.css`；深/浅主题；响应式；减少动效适配。
- Markdown 渲染：先转义，再做受控替换；KaTeX 本地化，离线公式渲染。
- SSE：`api.stream` 使用 fetch ReadableStream，解析事件；路由切换时 abort。
- 图表：手写 SVG（雷达/折线/柱状/热力/环形），无图表库。
- 所有用户内容转义；链接协议白名单；不渲染任意 HTML。

## 9. 错误与安全纪律

沿用三个原项目的三条纪律：

1. **不许静默失败**：模型未配置、网络失败、JSON 非法、Pack 校验失败都必须可见。
2. **降级仍然正确**：无模型 → 演示引擎；无 KaTeX → 文本；无外部依赖 → 标准库。
3. **契约优先**：数据/接口/SSE 事件有明确形状；破坏性变更需要迁移与测试。

安全边界：

- 默认绑定 `127.0.0.1`。
- 可选 `BOIL_TOKEN`；远程必须再套 TLS。
- SQLite 参数化查询；静态文件路径校验；请求体上限 12MB。
- API Key 0600；导出包不含 Key。
- 单用户产品，不是多租户；不要直接公网多用户使用。

## 10. 可扩展点

- 新增学科：写一个 Subject Pack + 内容包 + 评测器。
- 新增评分方式：在 `feynman.py` 的维度/权重或独立 Evaluator Pack 中扩展。
- 新增对话策略：替换 `prompts/socratic_system.md` 或引入 Skill Pack。
- 新增卡片类型：扩展 Probe kind 与 Scheduler 的探针选择策略。
- 新增模型：在 `llm.PROVIDERS` 或自定义端点配置。
- 新增前端页面：`static/js/views/` + 路由 + nav。
- 新增集成：MCP 网关（路线图）、Anki 导入、Obsidian/SiYuan 捕获。

## 11. 测试策略

- 单元：FSRS、规划器、编译器、Pack 校验、JSON 修复。
- 服务：onboarding、dashboard、map、unit、review、socratic、feynman、insights、重排、导出。
- HTTP：REST、SSE、静态资源、错误码、包导入/编译。
- LLM：本地假端点的普通/流式/JSON/失败可见。
- live 服务：假端点返回符合 schema 的报告/评分/内容包。
- 浏览器：所有页面渲染 + 交互 + JS 异常/console error/请求失败收集。

## 12. 未来：影子模式与学习效果基准

当前算法是“合理的 v1”，不是“被证明最优”。下一阶段：

- 事件溯源回放：用历史卡片/证据比较新旧调度器。
- 影子模式：真实用户继续用旧算法，后台用新算法计算差异；达标再切。
- 学习效果基准：7/30 天延迟保持、迁移测试、时间到掌握；公开方法与失败案例。
- 这些是产品可信度的核心，而不是营销文案。
