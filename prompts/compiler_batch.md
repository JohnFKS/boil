# 任务

这是一份长材料的**其中一批**。只针对本批内容出知识单元；整份材料的大纲已经给过你，用来判断这批落在哪一段。

# 原则

- 只用本批材料；不补材料外的事实，不编其它批次的内容。
- 按覆盖档出单元：每个**能独立考**的点都要有归属，宁少而准；本批有小节零覆盖就是漏了。
- 每个单元要有：一句话本质、类比、例子、步骤、边界、常见误解、关键点、探针、卡片，
  外加一句 `angle`（怎么学、和主线的关系，30 字内，不复述本质）。
- 探针至少覆盖 recall / explain / apply / boundary，要能区分"背过"和"理解"。
- 类比须注明仅帮助理解；没有就输出空字符串。

# 拆分口径（硬要求）

- 标题必须是**一个知识点**，不是学习安排（「第 3 天」「综合复习」「模拟题」等是课表）；
  材料的小节标题（如「第三章」）是出处，不是标题。
- 「知识点 + 题型/场景/例题」的标题要**抽象回知识点**：
  「银行家算法判断安全状态问题」→「银行家算法」；题目与场景写进 example / probes。
- 补背景只能写进 analogy 并注明「补充理解」，不许冒充原文。
- 每个单元必须给 `section`（照抄小节编号如 `sec-7`）与 `anchor`
  （该节**原样出现**的 10–40 字，不改写、不加省略号）；核不上即「未溯源」。
- `point_kind` 只取 concept / principle / procedure / fact / meta。

# 覆盖档

{coverage}

# 输出

只输出一个 JSON 对象，不要 Markdown、不要代码围栏：

{
  "units": [
    {
      "title": "单元标题",
      "point_kind": "concept | principle | procedure | fact | meta",
      "section": "sec-7",
      "anchor": "该小节里原样出现的 10–40 个字",
      "angle": "这个单元该怎么学，30 字内",
      "essence": "一句话核心本质",
      "analogy": "类比；没有就空字符串",
      "example": "一个具体例子",
      "steps": ["步骤 1", "步骤 2"],
      "boundaries": ["适用条件或失效场景"],
      "misconceptions": ["常见误解"],
      "faq": [{"q": "问题", "a": "回答"}],
      "key_points": ["评分时应该出现的关键点，3–6 条"],
      "difficulty": 3,
      "importance": 4,
      "estimated_minutes": 45,
      "probes": [
        {"kind": "recall", "prompt": "回忆题", "answer": "参考答案", "rubric": ["要点"], "hints": ["提示"]},
        {"kind": "explain", "prompt": "解释题", "answer": "参考答案", "rubric": ["要点"], "hints": ["提示"]},
        {"kind": "apply", "prompt": "应用题", "answer": "参考答案", "rubric": ["要点"], "hints": ["提示"]},
        {"kind": "boundary", "prompt": "边界题", "answer": "参考答案", "rubric": ["要点"], "hints": ["提示"]}
      ],
      "cards": [{"front": "卡片正面", "back": "卡片背面"}]
    }
  ]
}

# 整份材料

标题：{title}
主题：{subject}
大纲：{outline}

# 本批材料

{text}
