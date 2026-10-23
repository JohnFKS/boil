"""学习风格测试：5 道情境题 → 教学人设 → 注入苏格拉底/费曼的提示词。

题目考的不是"你喜欢看视频还是看书"这种自我印象，而是**具体情境下会怎么做**；
选项之间没有对错，只有"哪种带法你更容易坚持"。测完随时可以重测。
"""
from __future__ import annotations

from typing import Any

PERSONAS: dict[str, dict[str, str]] = {
    "stepwise": {
        "label": "小步快跑型",
        "strategy": "把知识拆得更碎，一次只推进一小步；每步都要他先说清，再给下一步。避免一次抛多个问题。",
    },
    "contrast": {
        "label": "对比辨析型",
        "strategy": "多用「A 和 B 差在哪」的对照提问；他容易在相似概念间混淆，追问时优先给反例与边界。",
    },
    "example": {
        "label": "先例子后定义型",
        "strategy": "先给一个具体情境或例子，再引导他自己归纳出定义与条件；不要先抛抽象表述。",
    },
    "pressure": {
        "label": "被追问型",
        "strategy": "直接质疑他表述里的漏洞，连续追问「为什么一定这样」；他不怕被推翻，怕的是没人较真。",
    },
    "build": {
        "label": "动手构建型",
        "strategy": "让他把知识用起来：给场景、给约束，请他设计做法或算一遍；讲解控制在两句以内。",
    },
}

QUESTIONS: list[dict[str, Any]] = [
    {
        "id": "q1",
        "text": "第一次接触「圆盘法求体积」，你希望教练怎么开场？",
        "options": [
            {"text": "先给一道具体题，让我边做边问", "weights": {"example": 2, "build": 1}},
            {"text": "从「为什么能切成薄片」讲起，一步一步推", "weights": {"stepwise": 2, "example": 1}},
            {"text": "先摆出圆柱、圆锥、球，问它们算法差在哪", "weights": {"contrast": 2}},
            {"text": "直接给公式，然后当场考我推导", "weights": {"pressure": 2}},
        ],
    },
    {
        "id": "q2",
        "text": "你答对了一题，但方法很绕。教练该怎么做？",
        "options": [
            {"text": "追问：能不能用更短的路径得到同样结果", "weights": {"pressure": 1, "build": 1}},
            {"text": "指出绕在哪一步，并给一个更直的分解", "weights": {"stepwise": 2}},
            {"text": "拿一道结构不同、方法相同的题让你再判断", "weights": {"contrast": 2}},
            {"text": "换一个真实场景让你重做一遍", "weights": {"example": 1, "build": 1}},
        ],
    },
    {
        "id": "q3",
        "text": "哪个时刻你最容易放弃？",
        "options": [
            {"text": "一次要消化太多新概念时", "weights": {"stepwise": 2}},
            {"text": "两个很像的概念总是记混时", "weights": {"contrast": 2}},
            {"text": "只讲道理，看不到具体例子时", "weights": {"example": 2}},
            {"text": "被反复质疑却说不清依据时", "weights": {"pressure": 1, "build": 1}},
        ],
    },
    {
        "id": "q4",
        "text": "复习一个已经学过的单元，你更想",
        "options": [
            {"text": "做几道新情境的应用题", "weights": {"build": 2, "example": 1}},
            {"text": "把易混概念并排列一张对照表", "weights": {"contrast": 2}},
            {"text": "按固定步骤把整套流程走一遍", "weights": {"stepwise": 2}},
            {"text": "讲给教练听，被他挑错", "weights": {"pressure": 2}},
        ],
    },
    {
        "id": "q5",
        "text": "你更信任哪一种「我真的懂了」的信号？",
        "options": [
            {"text": "能一步步复述出推理链条", "weights": {"stepwise": 2}},
            {"text": "能说出它和相似概念的差别与适用边界", "weights": {"contrast": 2}},
            {"text": "能立刻举出一个课本以外的例子", "weights": {"example": 2}},
            {"text": "能在陌生题上独立做出结果", "weights": {"build": 2, "pressure": 1}},
        ],
    },
]


def questions_payload() -> dict[str, Any]:
    return {
        "questions": [{"id": q["id"], "text": q["text"],
                       "options": [{"text": o["text"]} for o in q["options"]]}
                      for q in QUESTIONS],
        "personas": [{"key": k, "label": v["label"]} for k, v in PERSONAS.items()],
    }


def compute_persona(answers: Any) -> dict[str, Any]:
    """答案 = 每题的选项下标数组。返回主人设 + 次倾向与得分明细。"""
    if not isinstance(answers, list) or len(answers) != len(QUESTIONS):
        raise ValueError(f"需要 {len(QUESTIONS)} 个答案")
    scores: dict[str, float] = {k: 0.0 for k in PERSONAS}
    for q, raw in zip(QUESTIONS, answers):
        try:
            choice = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("答案必须是选项下标") from exc
        if not 0 <= choice < len(q["options"]):
            raise ValueError("选项下标超出范围")
        for key, weight in q["options"][choice]["weights"].items():
            scores[key] = scores.get(key, 0.0) + weight
    total = sum(scores.values()) or 1.0
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    primary = ranked[0][0]
    secondary = next((k for k, v in ranked[1:] if v > 0), None)
    return {
        "key": primary,
        "label": PERSONAS[primary]["label"],
        "strategy": PERSONAS[primary]["strategy"],
        "secondary": secondary,
        "secondary_label": PERSONAS[secondary]["label"] if secondary else "",
        "scores": {k: round(v / total, 3) for k, v in ranked},
        "answered": len(answers),
        "source": "rules",
    }


def prompt_block(persona: Any) -> str:
    """给苏格拉底/费曼提示词用的一段；没有测试结果就返回空串。"""
    if not isinstance(persona, dict) or not persona.get("strategy"):
        return ""
    return ("\n\n# 这位学习者的带法（来自 5 题风格测试，可随时重测）\n"
            f"主倾向：{persona.get('label', '')}"
            + (f"（次倾向：{persona.get('secondary_label')}）" if persona.get("secondary_label") else "")
            + f"\n{persona['strategy']}")


def persona_block_for(db, goal_id: str | None) -> str:
    """从 goals.meta_json 取人设并渲染成提示词片段。

    放在这里是因为苏格拉底与费曼都要用，而它们都不能 import services（会与 services
    形成循环依赖）。"""
    if not goal_id:
        return ""
    from .db import json_loads
    row = db.one("SELECT meta_json FROM goals WHERE id=?", (goal_id,))
    if not row:
        return ""
    return prompt_block((json_loads(row["meta_json"], {}) or {}).get("persona"))
