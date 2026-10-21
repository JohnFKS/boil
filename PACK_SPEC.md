# LearningPack 规范 v1.0

Pack 是 Boil 的内容更新单元。一个 Pack 可以包含一个学科的一部分，也可以是完整的一门迷你课。
安装是幂等的；新版本更新内容，但保留卡片与学习证据。

## 1. 最小示例

```json
{
  "schema_version": "1.0",
  "id": "boil.example.memory",
  "version": "1.0.0",
  "kind": "subject",
  "name": "记忆示例",
  "description": "一个最小的可学习内容包",
  "subject": "学习科学",
  "language": "zh-CN",
  "license": "CC-BY-4.0",
  "cover": "ember",
  "tags": ["记忆", "示例"],
  "source": {
    "system": "original",
    "kind": "authored",
    "generated_at": "2026-10-21T00:00:00Z",
    "note": "示例内容"
  },
  "units": [
    {
      "id": "memory-1",
      "title": "记忆的本质",
      "essence": "记忆是形成的神经回路，需要强度和频度。",
      "analogy": "复习像压路基，每次提取都会把路压实。",
      "example": "合上书复述一个定义，比再读一遍更有效。",
      "steps": ["拆成最小单元", "合书复述", "间隔复习"],
      "boundaries": ["一次性信息不需要长期记忆", "睡眠不足会影响巩固"],
      "misconceptions": ["看懂等于记住", "重复阅读等于复习"],
      "faq": [{"q": "为什么要合书？", "a": "为了强制提取，而不是识别。"}],
      "key_points": ["神经回路", "刺激强度", "刺激频度", "合书复述"],
      "difficulty": 2,
      "importance": 5,
      "estimated_minutes": 35,
      "order_index": 0,
      "tags": ["记忆"],
      "source_ref": {
        "system": "original",
        "kind": "section",
        "id": "memory-1"
      },
      "relations": [],
      "probes": [
        {
          "id": "memory-1-recall",
          "kind": "recall",
          "prompt": "用一句话说出记忆的本质。",
          "answer": "记忆是形成的神经回路，需要强度和频度。",
          "rubric": ["神经回路", "强度", "频度"],
          "hints": ["想想学习的本质是什么。"]
        },
        {
          "id": "memory-1-boundary",
          "kind": "boundary",
          "prompt": "什么情况下不需要把它背下来？",
          "answer": "一次性、随时可查的信息。",
          "rubric": ["一次性信息", "可查"],
          "hints": ["不是所有信息都值得长期保留。"]
        }
      ],
      "cards": [
        {"front": "记忆的本质是什么？", "back": "形成的神经回路，需要强度和频度。"}
      ]
    }
  ]
}
```

## 2. 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 否 | 默认 `1.0` |
| `id` | string | 是 | 全局唯一；建议 `组织.学科.范围` |
| `version` | string | 是 | semver；用于幂等与版本升级 |
| `kind` | string | 否 | `subject` / `content` / `skill`；默认 subject |
| `name` | string | 是 | 展示名称 |
| `description` | string | 否 | 一句话描述 |
| `subject` | string | 否 | 学科/主题 |
| `language` | string | 否 | 默认 `zh-CN` |
| `license` | string | 否 | 内容许可证；建议显式填写 |
| `cover` | string | 否 | `ember` / `ink` / `steam` / `mint` |
| `tags` | string[] | 否 | 展示标签 |
| `source` | object | 否 | 来源、版本、生成时间、说明 |
| `units` | Unit[] | 是 | 非空，最多 100 个 |

## 3. Unit 字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string | 是 | Pack 内唯一；安装后用于幂等 |
| `title` | string | 是 | 单元标题 |
| `essence` | string | 是 | 一句话本质；会进入卡片背面 |
| `analogy` | string | 否 | 类比；不要替代定义 |
| `example` | string | 否 | 具体例子 |
| `steps` | string[] | 否 | 标准步骤 |
| `boundaries` | string[] | 否 | 适用/失效条件 |
| `misconceptions` | string[] | 否 | 常见误解 |
| `faq` | {q,a}[] | 否 | 常见问题 |
| `key_points` | string[] | 否 | 评分关键点；缺失时自动派生 |
| `difficulty` | int 1–5 | 否 | 默认 3 |
| `importance` | int 1–5 | 否 | 默认 3；用于最少必要路径 |
| `estimated_minutes` | int | 否 | 默认 45 |
| `order_index` | int | 否 | 顺序；同时用于隐式前置关系 |
| `tags` | string[] | 否 | 单元标签 |
| `source_ref` | object | 否 | 来源与版本；用于追溯 |
| `relations` | Relation[] | 否 | 出边；目前支持 `prerequisite` |
| `probes` | Probe[] | 是 | 至少 1 个；建议包含 recall + explain + apply + boundary |
| `cards` | Card[] | 否 | 为空时自动从第一个 probe 生成一张 |

### Relation

```json
{"to": "unit-id", "kind": "prerequisite"}
```

- `to` 必须存在于同一 Pack。
- `kind` 目前只使用 `prerequisite`；其他类型保留。
- 没有显式前置时，安装器会按 `order_index` 建立顺序前置。

### Probe

```json
{
  "id": "memory-1-recall",
  "kind": "recall",
  "prompt": "……",
  "answer": "……",
  "rubric": ["关键点"],
  "hints": ["提示"]
}
```

- `kind`：`recall` / `explain` / `apply` / `boundary` / `transfer` / `contrast`。
- 至少需要一个 `recall`。
- `id` 可省略，安装时会生成稳定 ID（`p_<unit>_<index>`）。

### Card

```json
{"front": "正面", "back": "背面"}
```

- 卡片只是探针的一种展示形式。
- 安装时 ID 为 `c_<unit>_<index>`；重复安装会保留该卡片的 SRS 状态。

## 4. 校验规则

`validate_pack` 会拒绝：

- 缺少 `id` / `name` / `version` / `units`。
- 单元 ID 重复。
- 单元缺少 `title` / `essence`。
- 单元没有任何 probe，或没有 recall probe。
- probe kind 不在允许集合。
- depth/importance 不在 1–5。
- relation 指向不存在的单元。
- 单元超过 100 个。

安装器还会：

- 事务内安装；任一步失败整包回滚。
- 以 `(goal_id, pack_id, pack_unit_id)` 幂等。
- 同版本重复安装不重置卡片。
- 新版本更新单元内容，保留卡片 ID/状态。
- 记录 `packs.manifest_json` 与 `goal_packs.version`。

## 5. 版本与迁移

- 修改内容：`1.0.0 → 1.0.1`；保留 unit ID。
- 修改结构/删除 unit：提高 minor/major，并在外部提供迁移说明。
- 删除 unit：旧卡片仍保留在数据库中；需要后续清理策略（路线图）。
- 不要重用已被删除的 unit ID；否则历史证据会错位。
- `schema_version` 变化时需要迁移器；当前只有 v1。

## 6. 内容质量门禁（建议）

对高 stakes 内容，发布前至少：

- 数学/逻辑：等价性校验（符号/数值/逻辑）。
- 覆盖：对照考纲或学习目标，检查知识单元覆盖。
- 边界：适用/失效条件不矛盾；分支互斥。
- 来源：每个单元有 `source_ref` 与许可证。
- 反例：每个重要结论至少一个边界/反例探针。
- 人工复核：模型生成的内容必须可追溯到来源，不能把“模型自信”当事实。

examgen 的三道质检（数学等价 / 考点覆盖 / 边界一致）是 v1 数学包的来源；
未来的 Evaluator Pack 应把这类质检变成可执行门禁。

## 7. 从 examgen 导入

```bash
python3 tools/import_examgen.py \
  --input ../_work/examgen/output/模板库/第05章-二重积分-模板库-v0.14.md \
  --output content/packs/math-calculus-ch5.json \
  --limit 6
```

导入器会解析：

- 模板编号/版本 → `source_ref`
- 核心本质 → `essence`
- 标准步骤 → `steps`
- 失败边界 → `boundaries`
- 二级分支 → `tags` / FAQ
- 典型例题 → `example`

原始 OCR 教辅 `date1/` **不会**被导入或分发。

## 8. 自定义 Skill Pack（路线图）

当前 v1 的提示词在 `prompts/`。未来 Skill Pack 将包含：

```
skill-pack/
├── manifest.json
├── prompt.md
├── policy.md
├── ui.json
└── evals/
    ├── golden.jsonl
    └── rubric.md
```

要求：

- 声明 `api_version` 与权限（读取哪些数据、能否写证据）。
- 必须提供黄金测试与失败样例。
- 通过契约测试后才能加载。
- 支持影子模式：先回放，再切换。
