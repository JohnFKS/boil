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
│   ├── heat.py               # T 温度 / PN 缺口 / BoilRecord
│   ├── advisor.py            # 强度阶梯推荐器 v0（规则 + 理由 + 成本）
│   ├── embeddings.py         # 云端/本地向量：语义分组/搜索/相关单元
│   ├── backup.py             # 完整备份导入（表/列白名单）+ 导入前 SQLite 快照
│   ├── quality.py             # 编译产物质检：确定性检查 + 残差交给模型
│   ├── markdown_fix.py        # 粘贴清洗：只形状归一，不改写内容
│   ├── styles.py              # 学习风格测试 → 教学人设 → 提示词注入
│   ├── mcp_server.py         # MCP stdio 服务（13 个工具）
│   ├── socratic.py            # 苏格拉底状态机 + 流式 + 报告
│   ├── feynman.py             # 费曼六维评分 + 缺口
│   ├── mastery.py             # 四维证据聚合
│   ├── llm.py                 # OpenAI 兼容网关（普通/流式/JSON）
│   ├── srs/fsrs.py            # FSRS-5 四档调度
│   └── config.py / utils.py
├── content/packs/             # 内置内容包
├── prompts/                   # 苏格拉底 / 费曼 / 编译器（整篇·大纲·分批）/ 元信息 / 速记 / 卡片 / 追问 / 洞察解读
├── static/                    # 自研 SPA（无构建链）+ 本地 KaTeX
│   ├── js/selectionMenu.js    # 全局划词浮条 + 右键菜单 → 灵感 / 做卡片 / 问 AI
│   ├── js/quoteHighlight.js   # 引文快照在单元页重现（TreeWalker + 逆序包裹）
│   └── js/styleTest.js        # 5 题风格测试弹窗 → 写入目标人设
├── tests/                     # unittest：核心 / API / LLM 网关 / 质检 / 风格 / 粘贴清洗 / live
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
- **T / PN / BoilRecord**：火候状态、必要步骤缺口、每次模式尝试的前后温度与结果。
- **Advice**：一个带理由、成本、目标与档位的下一步建议；采纳/否决进入事件日志。
- **UnitNote**：速记/笔记，来自 mem 的速记与批注思路。
- **Backup**：带白名单的完整 JSON 导出/导入；API Key 永不进入备份；另有只读的 Anki 制表符导出。`mode=replace` 的清库与写入共享一个事务（`reset_user(commit=False)`），插入失败整体回滚，不留空库。
- **MCP Tools**：把同一批服务用例以 stdio JSON-RPC 暴露给 agent，不复制业务逻辑。
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
- 与**产品版本**是两回事：`config.VERSION` + `CHANGELOG.md` 管"这个应用是哪一版、改了什么"（见 4.9），
  `pack.version` 管"这壶水的内容是哪一版"。前者给用户看，后者给安装与幂等逻辑用。

## 4. 数据流

### 4.1 Onboarding

```
POST /api/packs/suggest（可选，编译前先出标题/学科/目标名）
  → merge_material_documents → LLM 或本地规则 → {title, subject, goal_title, description, source}

POST /api/packs/compile
  → merge_material_documents（多文件 + 粘贴文本，上限 200k 字符）
  → compiler.compile_material
      LLM 路径：units + outline + study_plan + digest + generated_by="model"
      本地路径：同构字段 + generated_by="rules"（形状一致，只是叙事更朴素）
  → normalize_pack（顶层新字段原样保留）→ 保存到 data/packs/

POST /api/onboarding
  → ensure_user
  → 选择/编译 Pack
  → 安装 Pack（事务；manifest_json 保存完整 Pack，units 表带 angle 列）
  → units + relations
  → planner.build_plan(ai_outline=…)   # AI 决定顺序叙事，代码决定日期算术
  → goal.meta_json 存 ai_digest / ai_outline / ai_study_plan / ai_generated_by
  → plans 插入新版本
  → event: goal.created / plan.built / pack.installed
  → 返回 dashboard（含 digest，前端据此渲染「材料总览」卡）
```

SSE 断线不重打一遍：`stream_reply(retry=True)` 会比对最后一条用户消息，同一句话不重复入库、不白涨轮次；前端断开后先 `GET /api/socratic/{id}` 看回答是否已落库——落了就直接显示，没落才带 `retry` 退避重发（最多一次），仍失败则给出「再试一次」。

呈现纪律：凡是 AI 生成的内容（总览、大纲、阶段、单元 angle、洞察解读），响应里都带
`source`/`generated_by` 字段，前端渲染成「AI 分析」或「本地规则」badge。规则回退绝不冒充模型输出。

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
前端：翻面前先写「回忆」草稿 → 翻面后草稿排在标准答案上方做对照
POST /api/review/{card}
  → Scheduler.schedule
  → cards 更新 + review_logs 插入
  → recall 非空时 attempts 插入（kind="review_recall"，空白不写，不占 probe_id）
  → evidence(recall)
  → event: review.rated
```

FSRS-5：

- 19 个权重；`DECAY=-0.5`，`FACTOR=19/81`。
- 新卡 → learning 短步；review → 长间隔；again → relearning。
- `preview` 与真实 `schedule` 共用逻辑，避免 UI 显示与实际到期漂移。
- 目标保留率 0.9；最大间隔 100 年；保留率只用于显示与调度，不对外宣称“预测准确”。
- **记忆健康度**（洞察页）= 全部 `state != 'new'` 卡片此刻的 `retrievability` 均值，直接复用 `Scheduler.retrievability`，不复制公式。它和「30 天保留率」是两件事：后者是历史答对率，前者是当下还能提取多少；无已学卡片时返回 `null` 而不是编一个数。

### 4.5 速记与口诀

```
POST /api/units/{id}/mnemonic
  → 服务端读 units 的 essence/steps/boundaries/misconceptions 组提示词
  → llm.chat_json（prompts/mnemonic.md：只给记忆钩子，禁止复述与新增知识）
  → 清洗：去重、≥4 字、每条 ≤60 字、最多 3 条
  → 返回给前端，不写库
用户确认「填进速记」→「保存速记」→ unit_notes(kind='mnemonic')
  → 复习队列与卡片正面把它作为提取线索透出
```

AI 只给候选，落库由人决定；未配置模型时入口不渲染，服务端还会挡一层 `503`。

### 4.6 复习增强：回忆、错因与提前加卡

- 翻面前写「回忆」草稿 → 翻面后草稿排在标准答案上方做对照 → 随评分写入 `attempts(kind="review_recall")`（与探测题作答分开计数）。
- 评分 1/2 时要求标错因（6 类固定 taxonomy），只拦一次、可跳过；标签写进 `review_logs.mistake_tags_json` 与卡片，答对则清空卡片上的旧标签。洞察页 `mistakes` 给分布与样本量。
- 队列侧栏：跳转任意一张卡、勾选后「只练选中」（重排不改已做部分）、「提前加卡」用 `GET /api/cards` 从全量卡片里挑未到期的加进本轮。
- **撤销评分**：掌握度是 EMA 状态量，「减掉一条证据」不成立，所以评分时把卡片状态行与 mastery 行快照写进 `review.rated` 事件的 `undo` 字段；`POST /api/reviews/undo` 用 `op`（事件 id）在一个事务里还原卡片、恢复 mastery、删除 review_log / recall attempt / evidence / boil_record，并把当天的 `reviews` 减一。只允许撤该单元最近一次复习（用 `review_logs.rowid` 判先后，时间戳只到秒）；同一次不能撤两遍。页面侧栏的「刚才这次」显示这条 diff 与撤销入口。
- 单元页的「我的回忆记录」列出最近 6 条回忆原文与得分：数据写进去就要能被看见。

### 4.6.1 专注（番茄钟）

开始不落库，结束才记：`POST /api/focus/finish` 以 `session` 作为事件 id（`fo_<session>`），重复提交返回 `duplicate` 而不是把时长记两遍；**只有 `completed` 写 `study_days`**，`abandoned` 只留审计事件。顶栏按钮负责倒计时（15/25/45 分钟），刷新后从 `localStorage` 恢复并在到点时补交；提前结束必须先点「结束这次专注」，界面明说「不计入学习时长」。

### 4.7 质检与合并（借鉴 examgen）

```
compile_material(_batched)
  → normalize（补齐缺失的探针类型）
  → quality.qa_pack
       lint_pack         确定性：空本质 / 重复标题 / 步骤混数字 / 缺探针 / 正反面相同 / 近似重复对
       apply_safe_repairs 只打安全补丁；补丁 == 原文则跳过（qa-skip，保护版本号与缓存键）
       find_near_duplicates 两路候选：字面近乎逐字（0.82）或云端语义进候选带（0.74）
       judge_residual / judge_pair  只把疑似重复交给模型判定；失败降级并留 note
```

### 4.7.1 重复裁决阶梯（阈值来自实测，且被真实模拟推翻过一次）

用硅基流动 `BAAI/bge-m3`（1024 维）实测。**文本口径决定一切**：

| 口径 | 真重复 | 最危险的假对 | 同主题其它对 |
|---|---|---|---|
| 标题 + 本质（`quality.dup_text`） | 0.870 | 0.797（提取练习 / 测试即学习） | 0.62–0.75 |
| 完整 `unit_text`（含步骤/边界/要点） | 0.795 | **0.813**（间隔 / 测试） | 全部挤在 0.76–0.81 |

完整口径下真重复排在假候选**之后**——所以检测只看标题+本质。即便如此，0.870 与 0.797 只差 0.07，一条阈值线分不开，于是：

| 依据 | 条件 | 结果 |
|---|---|---|
| `lexical` | 字面 ≥ 0.62 | 代码放行 |
| `model` | 语义 ≥ 0.74 **且**模型 `same: true` **且**请求带 `confirmed: true` | 放行 |
| `user` | 以上都不够，`override: true` + 理由 ≥ 4 字 | 放行，`merged_reason` 记「学习者确认：<原话>」 |
| 拒绝 | 都不满足且没署名 | 报错，把两个分数与合并线一起说清楚 |

**语义分不再单独授权合并**（第一版给它设了 0.80 自动线，被完整真实模拟否掉：8 个单元的包里一次跳出 9 对候选、7 次模型判定，还把「间隔效应」和「测试即学习」判成可合并）。配套两条护栏：判定按分数排序且一次请求最多判 `JUDGE_LIMIT=3` 对，其余标 `pending_judgement`，用户点合并时才判（同一问法 → 命中内容寻址缓存）；打开地图页因此不会变成烧钱。

另外两个由实测钉死的细节：模型对中间带同一对会**翻**（真实模拟里，候选列表刚说「不可合并」，端点自己再问一次就改判 `same=true` 并真的合了库），所以判定问法按单元 id 定序让两次共用同一提示词（缓存开着时同源），并且**非字面证据的合并在服务端强制要求 `confirmed`**——确认这件事不能只写在前端；嵌入不可用时 `semantic` 一律 `null`，阶梯退化为字面 + 署名，**绝不拿本地哈希向量冒充语义分**。

合并语义：**单元不删除，只改指向归宿**。`units.merged_into` + `merged_reason`（`merged_op` 指向那次合并的事件）；`map_data` 与 `_next_units` 过滤被吸收的单元；`unit_detail` 返回 `merged_into` / `absorbed` / `can_unmerge`；relations 自动重连到归宿（自环与重复边清掉）；卡片留在原单元继续到期，因此复习进度不丢。链式合并沿 `merged_into` 走到最终归宿（上限 12 跳），互相合并/成环被拒绝。

**撤销合并**：关系边是按记录重写的，所以合并时把每一条的实际改动（`action: update|delete` + 原端点 + 新端点）写进 `unit.merged` 事件的 `undo.relations`，`units.merged_op` 记住事件 id。`unmerge_unit` 只撤销指向再按记录回滚边；三种情况拒绝（归宿已被并走 / 本单元之后又吸了别人 / 记录缺失则降级为「只撤指向」并如实标注 `degraded`）。撤销同样走一个事务。

### 4.8 长材料与风格测试

- 材料 >24k 字符：先 `_skeleton`（每节标题 + 首句）出大纲与总览，再按 `_batch_sections` 分批（每批 ≤7k）逐批出单元；模型只给内容，**切片由代码做**（`_slice_at_anchors` 按逐字锚点切，锚点必须原样存在于正文，否则不切）。
- 单次调用上限 `SINGLE_CALL_LIMIT = 6000`（原 24,000）：实测 8.6k 字材料能出可用 JSON，10.2k 字就在尾部被截成 `"difficulty":4,":5,":":40"`，能否修复成掷硬币；上限按真实输出能力定，不按上下文长度定。模型直接回一个单元数组时 `_normalize_llm_units` 也会接受，不再误判成本地编译。
- 装不下预算的小节由 `_chunk_section` 无损切开（空行 → 句号 → 硬切，再把小块装回预算内），**没有任何字符被丢弃**；单元数超过上限（单次 10、分批 40）会写进 `warnings` 而不是静默吞掉。内容是「模型读完了全部材料」这句承诺的兑现处，截断必须吭声。
- 风格测试在 `boil/styles.py`：5 题 × 3–4 选项，选项带 persona 权重；`questions_payload()` 故意不返回权重。人设写进 `goals.meta_json.persona`，`styles.persona_block_for(db, goal_id)` 渲染成提示词片段注入苏格拉底与费曼（放这里是因为这两个模块都不能 import services——会成环）。

### 4.9 版本与更新日志（用户可见）

- **唯一事实源是 `CHANGELOG.md`**：`boil/releases.py` 把它解析成结构化 release（版本 / 日期 / 一句话摘要 / 分组条目），`GET /api/releases` 原样给出，前端渲染成侧边栏徽标 + 浮层 + 设置页分区。不解析 `git log`：commit message 是写给协作者的（"feat: keep quotes that cross a formula readable"），学习者要的是"跨公式的引文不再错位"；而且 Docker 镜像里没有 `.git`。
- **构建号另说**：`build_identity()` 直接读 `.git/HEAD` → `refs/heads/<branch>`，回退 `packed-refs`，不启动 git 进程；`.git` 是 worktree 指针文件时按仓库根解析相对路径，ref 里含 `..` 或以 `/` 开头就不跟（不拿仓库外的文件当版本号）。没有 `.git` 时 `build=""`，前端不显示构建号 —— **宁可空着，不编一个**。
- **坏数据不许拖垮启动**：文件缺失或整篇解析不出来时 `load()` 返回 `[]`，页面显示"没有读到更新日志"而不是白屏或 500。
- **两类自检分开**：`changelog_problems()` 只管日志本身（缺日期、没条目、空分组、重复、顺序倒退、`## 版本 1.1.0（2026年10月24日）` 这种解析不出的标题）；`version_problems()` 只管 `config.VERSION` 是否等于最新条目。分开的理由很实际：混在一起就没法用示例文本测格式规则。`all_problems()` 给 CI 用。
- **顺序比较用数字**：`version_key("1.10.0")=(1,10,0)` 而不是字符串比较，否则 `1.10.0 < 1.9.0` 会误判发版顺序。
- **已读状态在浏览器**：`localStorage.boil_seen_release` 记下用户看过哪个版本；版本变了侧边栏亮小圆点，看完更新内容（浮层或设置页摊开）才消失。服务端不存这个状态 —— 它是"这台设备上的这个人读到哪儿"，不是学习数据。


### 4.10 校准：模型说"你想得起来"，你到底想得起来吗

`services.calibration()` 回答一个此前无法回答的问题：FSRS 每次复习前给出的可提取性预测（`review_logs.retention_before`，
从建库起就在写）到底准不准。

- **口径先于计算**：只取 `state_before='review'` 且 `retention_before>0` 的样本。新卡与学习步骤里内容还在眼前，
  预测要么没做、要么无意义，混进来的结果是"看起来非常准"——这是最容易自欺的地方。
- **两个数看不同的事**：`bias = 平均预测 − 实际答对率`（正数＝偏乐观＝间隔排太长）说方向，
  `brier` 说每一次的误差大小。只有 bias 会被"高估与低估互相抵消"骗过去，所以两个都要给。
- **样本不足就不下结论**：`CALIBRATION_MIN = 20`；不够时 `verdict` 说"再攒 N 次"，前端不画条形图。
  分桶里样本 < 3 的 `gap` 直接给 `None`，两次样本不报差异。
- **它和"30 天保留率"不是一回事**：后者（`stats.retention_30d`）的分母只有"来复习的卡"，而复习是故意排在
  R≈0.9 的时刻发生的，天生偏高，衡量的是"复习得怎么样"。校准衡量的是"这个预测可不可信"，
  是①跨月留存实测 与 ②策略对照 的前提：预测不可信，前两者的数字也没法解释。

"""

### 4.12 全局 agent：能操作，但决定权在人

`boil/agent.py` + `static/js/agentOrb.js`。设计目标不是"最强 autonomy"，而是"敢在真实学习数据上开"。

- **能力白名单是代码，不是提示词。** `CAPABILITIES` 里 28 项，每项声明 `kind/method/path/args/risk/desc`。
  模型返回的每个动作都要过 `validate_action()`：id 查表、参数按类型与取值域校验（评分只能 1–4、
  番茄钟只能 15/25/45、路由只能白名单页），路径参数只以模板形式交给前端并 `encodeURIComponent`。
  越界的动作不是"警告后执行"，是**丢弃 + 在界面上说明为什么丢弃**。
- **服务端不执行任何东西。** 规划器只产出提议；发请求的是浏览器，每个动作一张卡片，写着
  `GET /api/map` 和"为什么"。这条边界让"模型被话术带跑"最坏也只是提议了一个需要人点击的动作。
- **危险动作双锁。** `risk=danger` 的按钮默认 `disabled`，必须亲手输入确认词才解锁；
  提示词里明确禁止模型自己填 `confirm`，而 `_check_arg("confirm_reset")` 又要求值必须是 `RESET`，
  前端还有一层"没输就不发请求"。旅程测试用 `window.fetch` 计数器证明：不输确认词，`/api/reset` 请求数为 0。
- **回答系统问题靠检索仓库文档。** `doc_chunks()` 按标题切块（mtime 变了就重建），检索是字面为主：
  **标题命中比正文命中值钱**（问「合并单元」要先命中「单元合并」那一节），问句功能词先剥掉，
  门槛 `lex>=1.2`；完全没有字面信号时语义分要 ≥0.55 才收 —— 实测 bge-m3 对无意义中文也能给到 0.4，
  放它进来的结果是"用户胡说，系统认真引用一段文档"。
- **引用不许编。** 模型给的 `citations` 必须落在本次检索到的文档里，否则剔除并说明。
- **降级仍然正确。** 没配模型时 `plan_offline()` 按关键词分类（问数据 / 问怎么用 / 下指令），
  答桉带出处、`engine=rules` 明标；纯指令不附带文档讲座。
- **多轮有上限。** `MAX_ACTIONS=6`、`MAX_TURNS=4`；"继续"只是再提议，不是自动执行。

### 4.13 备考冲刺：错题本、速背、限时自测卷（移植自 kaobuddy，MIT）

借来的是**场景**而不是实现：那套是 React + FastAPI + IndexedDB，这里是 stdlib + 原生模块 + SQLite，
代码不能搬，能搬的是"离考试八天的人需要什么"。三件事都放在新页 `#/sprint`，与 FSRS 队列刻意分开：
间隔重复管"以后别忘了"，冲刺管"这两天别再丢分"，用一套排程同时满足只会两头做坏。

- **错题本** `services.mistake_book()`：从 `review_logs` 里取窗口内的全部评分，按 `rowid` 判先后，
  只把"**最近一次仍然答错**"的卡收进本子。用 rowid 不是洁癖 —— `reviewed_at` 只到秒，
  同一秒里"先错后对"用时间戳分不出来，答对回去的卡会永远赖在本子里吓唬人（这条有测试盯着：
  `test_mistake_book_exits_after_you_get_it_right`）。`tag_counts` 仍统计窗口内所有答错记录：
  错过两次又答对，那两次也值得知道。
- **重练入口** `GET /api/review?ids=...`：复习队列原本只出"到期的卡"，而错题的意义恰恰是
  "还没到期也该再看一眼"。返回 `picked_from: "mistakes"` 与 `missing`，找不到的卡如实报数量。
  卡片 id 里带包名与单元 slug，实测 67–96 字符，**后端一律不截断 id**：按定长截断会把刚发出去的
  id 变成查无此卡，还回一句"不属于你的目标"（这条也是踩出来的，见 `test_cram_shot_accepts_real_long_unit_ids`）。
- **速背** `services.cram_plan()`：`重要度/5 × 0.4 + (1 − 掌握度) × 0.35 + 临近度 × 0.25 + 错过次数 × 0.15`，
  三周内开始加临近权重；每项直接摊开 本质 / 必背要点 / 口诀 / 常见考法 / 易错提醒，来源都是已有表，
  不额外请求模型。"斩掉"写进新表 `cram_shots`（`ON CONFLICT DO NOTHING`，可 undo、可 reset），
  只影响这份清单，不改 `cards` 一行。
- **限时自测卷** `services.exam_build()` / `exam_grade()`：题目只取 `probes`（资料编译的产物），
  每个单元最多 2 题逼出跨单元覆盖，学过的单元排前面 —— 一门没学也允许出卷，但 `note` 里写明
  "当练习用，别当诊断"。`questions` 响应里**没有** `answer` 与 `rubric`，交卷前看不到答案。
  判分用 `utils.coverage(答案文本, 评分要点)`：≥75% ✓、≥35% △、其余 ✗、空着 `blank`（算 0 分）、
  **没有评分要点的题记 `ungraded` 且 `score: null`** —— 判不动就承认判不动，不编分数。
  `graded_by` 恒为 `rules`：模型不参与判分，因此这份分数不随上游换模型而漂移。
  交卷写 `attempts(kind='exam')`、按实际用时累加 `study_days`、把丢分题送进错题本；同一份卷子拒绝二次交卷。
- **A4 打印**：`@media print` + `@page { size: A4 }`，前端临时造一张 `.print-sheet` 并强制
  展开所有 `<details>`（纸质卷子上不该有"翻面才有答案"），打印完立刻移除节点。
  不引 `jspdf` / `html2canvas` —— 浏览器自带打印就是这个项目愿意接受的"PDF 依赖"。
- **手指刷卡**：`#review-card-slot` 上的 pointer 事件，`pointerType === "mouse"` 一律不接管，
  否则会把划词存灵感那条路堵死。没翻面时右滑 = 翻面，翻面后右滑 = 记得、左滑 = 忘了。

### 4.14 拆解口径、逐字出处与覆盖率（整合 kaobuddy 口径 + GraphRAG/RAPTOR/Tree-KG 做法）

`boil/decompose.py` 只做三件事，而且**一次模型都不调用**：判断"这像不像一个知识点"、
把每条断言钉回原文的一句话、把材料被读到了几成算出来。之所以单开一个模块：这三件事
以前散在提示词里，模型不遵守时没有任何东西拦一下。

- **口径先于提示词**：`schedule_like()` 认得「第 3 天」「每日任务」「综合复习」「查漏补缺」这类
  其实是课表的标题；`strip_type_tail()` + `canonical_title()` 把「银行家算法判断安全状态问题」
  归回材料里已有的名字。两处刻意留着边界：「第三章」是出处不是罪名，
  「间隔复习 / 提取练习」是被研究的策略而不是安排 —— 误杀这两个就等于把元认知材料整节删掉。
  `canonical_title()` 只在**能对上已有名字**时改写：凭空"抽象"就是让模型替我们编知识点名，那是另一类幻觉。
- **出处按引用而不是按自报**（GraphRAG / Tree-KG 的做法）：小节编译时就有稳定编号 `sec-N`，
  骨架与小节标题一起送进提示词；模型必须回 `section` + `anchor`。`locate()` 按逐字匹配核对，
  只容忍空白差异（浏览器与模型都会吞换行），改写过的"证据"不算证据；
  核不上先在别的小节里找这句原文（节号写错但句子是真的，出处按原文落回），
  全都核不上就写 `kind:"unverified"` —— 界面标成「未溯源」，而不是假装没这回事。
  兜底锚点 `fallback_anchor()` 用二元组重合挑**原文里逐字的一句**，重合不够就放弃。
- **覆盖率按材料字数加权**：按小节个数算会让"一句带过的小节被引用"和"三千字的核心节被引用"等价，
  正好掩盖长材料里没被读到的那部分。没有登记表时返回 `null`：那是"不知道"，不是"没问题"。
- **第二层（章）用软归属**（RAPTOR 的递归分层，但把硬切分换成"够像就都进"）：
  一节同时属于两个主题时不再被迫二选一；嵌入不可用时退化成按顺序分章并如实标 `engine:"order"`，
  不假装是语义聚的。
- **类型是可量化的缺口**：`concept / principle / procedure / fact / meta` 由规则判，判不出就算概念并标
  `default`；"这份拆解一条方法都没有"从一句感觉变成 `kind_gaps`。
- **覆盖档只在模型路径生效**：`standard` / `full` 拧的是模型的拆分粒度，本地规则编译器不读它，
  所以响应把 `requested` 与 `applied` 分开给，界面据此说明"这次没走模型，档位不改结果"。

质检侧 `quality.lint_pack()` 因此多了三条确定性检查：`schedule_like_title`（warn，带材料里更合适的名字）、
`type_tail_title`（info）、`kind_missing`（info，可安全补）、`ungrounded`（info）。
呈现位置：地图页「拆解体检」卡（覆盖率 / 溯源率 / 章节数 / 缺项，章可按节过滤节点）、
单元页「出处」卡（节号 + 逐字锚点，或如实的「没有可核对的出处」）。

## 5. 火候、PN 与 Advisor

### 5.1 T（温度）

`heat.compute_heat(mastery, due_count)`：

- 基础值 = 四维平均分 × 100；
- 四维都 ≥0.8 且 reps ≥4 → 基础值至少 96；
- 超过 3 天没有新证据 → 每天衰减 2.2；
- 每张到期卡 → 最多扣 20。

T 是产品状态量，不是科学测量值。阈值 40/80/100 用于推荐与展示，必须用真实数据校准。

### 5.2 PN（必要步骤）

`heat.pn_state` 读取最近一次已完成的苏格拉底报告与费曼解释里的 `gaps`，把它们作为“还缺的步骤”；没有报告时返回未知，而不是假装完整。单元的 `steps` 是 PN 的默认映射。

### 5.3 Advisor v0

`advisor.build_advice`：

1. 有到期卡 → 回测优先；时间不够就按时间预算还一部分。
2. 无到期卡 → 找下一个未烧开单元；若前置未烧开，先补前置。
3. 按 T 选档：T=0 预消化；T<40 苏格拉底；40–79 有 PN 缺口 → 费曼；≥65 无缺口 → 极致对标；≥80 → 极致对标。
4. 时间 ≤8 分钟 → 音频；时间不够当前档 → 降一档。
5. 输出一个建议：`mode × 档位 × 目标 × 理由 × 成本 × PN 缺口 × 训练阶梯`。
6. 采纳/否决通过 `/api/advice/feedback` 进入事件日志，供未来策略学习。

约束：每场只给一个建议；必须给理由；可否决且记账。

### 5.4 Markdown 解析与 Embedding

- **有标题的 Markdown**：标题/列表是结构真相源，优先按标题切分；LLM 可在配置时做更细抽取。
- **无标题材料**：`embeddings.semantic_groups` 用向量把相邻主题段落聚成单元；这是“辅助切分”，不替代结构解析。
- **向量引擎**：可接硅基流动/OpenAI 兼容 `/embeddings`（推荐 `BAAI/bge-m3`，1024 维）；批量 32 条、磁盘缓存、按 index 排序、60 秒失败冷却；未配置或失败时回退本地 `local-hash-256`。
- **语义搜索/相关单元**：同一向量层用于 `/api/search` 与单元页“相关单元”；设置页显示模式、维度、缓存/调用统计和最后一次错误。
- **隐私边界**：云端启用后，参与分组/检索的文本会发往所配端点；Key 只存 `data/embedding.json`（0600），不进入备份。

### 5.5 BoilRecord

`socratic` 报告、`feynman`/`benchmark` 评分、`recall` 评分都会写入 `boil_records`：

- `mode` / `level` / `result_json`
- `heat_before` / `heat_after`
- `pn_diff_json`
- `advice_id` / `accepted`

它是“六个模式共享同一份状态”的落点，也是未来做“单位时间烧开数” benchmark 的原始数据。

## 6. 掌握度

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

## 7. 规划器

输入：单元顺序、前置关系、重要度、估算分钟数、每日分钟数、截止日期、可选 `ai_outline`。

- 拓扑排序保证前置；环时按 order_index 兜底。
- `ai_outline` 只重写 `order_index`（LLM 建议的学习顺序），**前置依赖仍由拓扑排序裁决**：AI 提方案，代码做计算与校验，模型不能绕过依赖关系。
- 每日新内容预算 = 每日分钟 - 复习预算 - 10% buffer；最多 2 个新单元/天。
- 每 7 天安排一个休息日。
- 时间不够时，按重要度选高权重单元，并自动补前置；其余进入 `parked`（暂存）。
- 计划是版本化的；重排从今天开始，不累积历史欠账。
- `plan.outline` 透传 AI 大纲，重新排期（`/api/plan/rebuild`）时从 `goal.meta.ai_outline` 取回，顺序不会丢。

### 7.1 灵感笔记（notes）

与 `unit_notes` 分开：`unit_notes` 是「每个单元每类一条」的速记/笔记，`notes` 是可以跨页面无限累积的划词记录，带 `quote`（引用原文快照）、`content`、`source`（来源视图）、可空 `unit_id`/`goal_id`。

```
前端 selectionMenu.js
  → selectionchange（划词浮条）/ contextmenu（右键）
  → 只在 [data-selectable] 容器内触发；输入框/textarea 内不触发
  → POST /api/notes {quote, content, unit_id?, source}
  → 事件 boil:note-saved → #/notes 视图自动刷新
```

引用存的是**文字快照而不是字符偏移**，因此材料重排、单元重新安装都不会让笔记失效。单元页渲染完成后由 `static/js/quoteHighlight.js` 把引文在正文里出现的位置全部高亮（`<mark class="note-hl">`）：TreeWalker 建坐标 → 找全部匹配 → 丢弃重叠 → **倒序**逐个用 `Range.surroundContents` 包裹（逆序可避免前面的偏移被自己的改动破坏），并跳过 `code / pre / .katex` 子树；`box-decoration-break: clone` 让跨行高亮不断色。

**公式（F12）**：`selection.toString()` 跨过 KaTeX 时返回的是 MathML + annotation + 两遍字形混杂的乱码（实测 `'当 \n𝑥\n→\n0\nx→0 时…'`），既不值得存也没法匹配。所以保存时先把克隆选区里的 `.katex` 换成 `$TeX$`（取自 `annotation[encoding="application/x-tex"]`）得到可读引文；同时用同一套 TreeWalker 坐标算出「去公式可见文本」的切片与它的出现序号，存进 `notes.anchor_json`。高亮时优先用这个锚点定位（只点亮那一处），失效才退回引文原文；被公式隔开的文字会落成几段 `<mark>`，公式本身不动——不往 KaTeX 的 DOM 里插节点。返回 `{marks, quotes}` 两个数，提示条写「已标出 N 段（来自 M 条灵感）」。追问的上下文也从 `innerText` 改成同样的 `$TeX$` 文本，`indexOf` 才对得上。

## 8. LLM 网关

`llm.py` 是唯一模型出口：

- 配置：`data/llm.json`（0600）；API Key 不回传。
- 支持：普通、流式 SSE、JSON 模式。
- 兼容：OpenAI / DeepSeek / DashScope / Moonshot / Dots / Ollama / 自定义。
- 认证：默认 `Authorization: Bearer`；支持自定义头名与前缀（Dots 使用 `api-key`）。
- 高级请求体：`extra_body` 可合并 `chat_template_kwargs` 等供应商参数，不覆盖核心字段。
- 失败：HTTP / 网络 / 空正文 / 非法 JSON 都有显式错误；`llm_state.json` 记录最近错误。
- 重试策略：`408/409/429/5xx` 指数退避；`400` 且提示 `response_format` 时去掉该参数降级重试；`finish_reason == "length"` 时把 `max_tokens` 提到 `max(4×, +800)`（上限 32000）重发一次，而不是把半截 JSON 交给业务层——**只有在我们自己设过 max_tokens 时才加额**，否则会凭空压低网关默认上限。
- JSON 修复：去除代码围栏后做**括号配平扫描**（跳过字符串内的括号与转义），优先取第一个完整值；不完整时按未闭合栈补 `]}"` 再试；最后才退回尾逗号清理与 rfind 兜底。
- 垃圾输出守卫：模型只回 `undefined/null/无/…` 这类占位字符串时判为失败（`junk_output`），不写进卡片、速记或笔记。
- 内容寻址缓存：`chat_json` 按 `sha256(网关+模型+messages+温度+max_tokens)` 落盘 `data/llm_cache/`，命中复用原始文本再解析（所以解析器改进会 retroactively 生效）。网关地址进键是为了同名模型换服务商时不复用另一家的输出。只缓存结构化调用——流式对话带会话状态，缓存它等于伪造回答。
- 缓存不固化失败：**解析成功才写盘**，命中坏条目时删掉它再走真实调用。否则一次截断输出会让同一个请求永久报错。
- 缓存有上限（800 条，超出按 mtime 淘汰到九成）：内容寻址缓存只增不减会一直吃盘，而它的价值在于「重跑免费」，不在于「永久保存」。可在设置页关闭或清空，`public_config()` 暴露 calls/hits/entries。
- 无 Key：业务层走本地演示引擎，不在网关里伪造模型回答。
- 真实模型失败：苏格拉底会显示“已切换到本地演示引擎”，报告会带 demo 标记；不会静默冒充 live。
- AI 入口三态（前端 `utils.js`）：未配置 = **完全不渲染**（不是置灰）；可用 = 渲染；连续 2 次失败 = 熔断隐藏并提示已切本地模式。复习、苏格拉底、费曼、洞察主流程永不因网关挂掉而卡住。
- 上游始终流式：`chat()` 默认用 `stream_upstream` 以 `stream:true` 请求上游、把分片拼回非流式形状再交给 `_extract_message`，避开思考型模型长推理被空闲网关 504；网关不认 `stream` 时按普通 JSON 原样返回。可用设置里的开关关掉。
- 诊断可行动：网络异常按 `__cause__` 链归类为 dns / refused / timeout / tls / proxy / network，HTTP 状态码有建议表，回显的服务端内容先掩码（`sk-…`、`Bearer …`）再落盘与回传；`last_error` 带 base_url、模型与掩码 Key，设置页可一键复制整段诊断。

## 9. 前端

- 自研 SPA，ES modules，无构建链；`static/js/app.js` 负责 shell/router/搜索。
- 页面模块：`views/dashboard|map|unit|socratic|feynman|review|library|insights|settings|onboarding.js`。
- 设计系统：`static/css/app.css`；深/浅主题；响应式；减少动效适配。
- Markdown 渲染：先转义，再做受控替换；KaTeX 本地化，离线公式渲染。
- SSE：`api.stream` 使用 fetch ReadableStream，解析事件；路由切换时 abort。
- 图表：手写 SVG（雷达/折线/柱状/热力/环形），无图表库。
- 所有用户内容转义；链接协议白名单；不渲染任意 HTML。
- `static/js/releases.js`：版本徽标与更新内容浮层。数据只在启动时取一次（`/api/releases`，
  取不到就静默降级成 `me.version`，版本信息读不到不该挡住页面启动）；渲染器只有一份
  （`releaseHTML`），浮层和设置页共用，避免两处文案漂移。滚动上限加在自己的 `.rel-scroll` 上，
  不加在 `.modal-body` 上——后者会波及所有宽模态。

## 10. 错误与安全纪律

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

## 11. 可扩展点

- 新增学科：写一个 Subject Pack + 内容包 + 评测器。
- 替换 embedding：`embeddings.py` 的接口保持稳定，可接真正的语义模型。
- 新增导入格式：`backup.py` 的表白名单与列过滤可扩展。
- 新增评分方式：在 `feynman.py` 的维度/权重或独立 Evaluator Pack 中扩展。
- 新增对话策略：替换 `prompts/socratic_system.md` 或引入 Skill Pack。
- 新增卡片类型：扩展 Probe kind 与 Scheduler 的探针选择策略。
- 新增模型：在 `llm.PROVIDERS` 或自定义端点配置。
- 新增前端页面：`static/js/views/` + 路由 + nav。
- 新增集成：Anki 导入（当前为导出）、Obsidian/SiYuan 捕获；MCP 已提供 stdio 服务。

## 12. 测试策略

- 单元：FSRS、规划器、编译器、Pack 校验、JSON 修复。
- 服务：onboarding、dashboard、map、unit、review、socratic、feynman、insights、重排、导出。
- HTTP：REST、SSE、静态资源、错误码、包导入/编译。
- LLM：本地假端点的普通/流式/JSON/失败可见。
- live 服务：假端点返回符合 schema 的报告/评分/内容包。
- 版本：`tests/test_releases.py` 覆盖解析（含换行续写、`### ` 前无分组的裸条目）、
  `unreadable_headings`（写坏的版本标题不能静默消失）、`version_key` 的数字顺序、
  `.git` 各种形态（分支 / detached / packed-refs / worktree 指针 / 缺失 / 坏 HEAD / 越权 ref），
  以及**真实 CHANGELOG**：每条 release 必须有日期、摘要、≥3 条目，且 `config.VERSION` 必须是最新那条。
- 浏览器：所有页面渲染 + 交互 + JS 异常/console error/请求失败收集。触摸端的划词浮条用 `Input.dispatchTouchEvent` + 移动视口验证（浮条必须出现在屏幕内，且拖选过程中不许抢跑）。浮层必须用 CDP 真实鼠标事件走一遍（`real_click`）：`element.click()` 不派发 mousedown，「点外部关闭」这类 document 级逻辑用合成点击永远测不到——划词面板曾被自己的 hideAll 抢关，29 张截图全绿也发现不了。
- 用户旅程：`tools/human_journey.py` 走一条连贯的"新用户的一天"（粘贴材料 → AI 生成信息 → 编译 →
  单元 → 划词存灵感 → 复习+撤销 → 番茄钟 → 版本浮层 → 搜索 → 洞察 → 灵感库 → 导出落盘 → 手机尺寸），
  断言的是**用户看得见的文字**而不是 DOM 存在；停顿、滚动、hover、拖拽都按人的节奏来。
- 备份：导出 → 合并导入 → 目标/单元/卡片/火候记录/速记一致；替换导入中途插入失败要整体回滚，不许留下被清空的库。
- 合并/撤销：`merge` 改边 → `unmerge` 后边回到原指向、单元回到地图；链被移动过时撤销必须被拒绝而不是猜。
- Notes：速记保存 → 复习队列带出 mnemonic。
- Embeddings：本地/云端语义分组、语义搜索、相关单元召回；批量、缓存、失败冷却、降级与错误可见。
- 数学：数学包公式内容 + KaTeX 本地资源；真实浏览器检查 `.katex`。
- Advisor：新单元、苏格拉底、费曼、对标、回测优先、低时间音频、前置边界、否决记账。
- 火候：T 衰减、到期惩罚、PN 缺口、BoilRecord 升温/降温。
- Pack 质量：原子率、四类探针覆盖、DAG 无环。

## 13. 未来：影子模式与学习效果基准

当前算法是“合理的 v1”，不是“被证明最优”。下一阶段：

- 事件溯源回放：用历史卡片/证据比较新旧调度器。
- 影子模式：真实用户继续用旧算法，后台用新算法计算差异；达标再切。
- 学习效果基准：7/30 天延迟保持、迁移测试、时间到掌握；公开方法与失败案例。
- 这些是产品可信度的核心，而不是营销文案。
