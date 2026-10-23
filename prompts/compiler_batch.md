# 任务

这是一份长材料的**其中一批**。只针对本批内容出知识单元；整份材料的大纲已经给过你，用来判断这批落在哪一段。

# 原则

- 只用本批材料里出现的内容；不补充材料外的事实，也不要把其它批次的内容编出来。
- 本批出 1–4 个单元；内容单薄就少出，不要硬凑。
- 每个单元要有：一句话本质、一个类比、一个例子、步骤、边界、常见误解、关键点、探针、卡片。
- 每个单元还要一句 `angle`：这个单元该怎么学、和主线的关系，30 字内；不要复述本质。
- 探针至少覆盖 recall、explain、apply、boundary 四种，要能区分"背过"和"理解"。
- 类比要注明只是帮助理解；没有合适类比就输出空字符串。

# 输出

只输出一个 JSON 对象，不要 Markdown、不要代码围栏：

{
  "units": [
    {
      "title": "单元标题",
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
本批进度：第 {batch_index} / {batch_total} 批

# 本批材料

{text}
