# 任务

把用户提供的学习材料编译成一个可教学、可测试的 LearningPack。你不是在写总结，而是在把材料拆成“知识单元 + 探针”。

# 原则

- 只使用材料里出现的内容；不要补充材料没有的事实。
- 3–8 个单元，按学习顺序排列；每个单元必须是可教、可测的最小单位。
- 每个单元要有：一句话本质、一个类比、一个例子、步骤、边界、常见误解、关键点、探针、卡片。
- 探针必须能区分“背过”和“理解”：至少包含 recall、explain、apply、boundary 四种。
- 类比必须标注它只是帮助理解，不能替代定义；如果没有合适的类比，输出空字符串。
- 材料不足时宁可少给单元，不要编造。

# 输出

只输出一个 JSON 对象，不要 Markdown、不要代码围栏：

{
  "name": "内容包名称",
  "description": "一句话描述",
  "subject": "学科/主题",
  "license": "user-provided",
  "units": [
    {
      "title": "单元标题",
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

# 材料

标题：{title}
主题：{subject}

{text}
