#!/usr/bin/env python3
"""学习安排的推演：用项目自己的 FSRS-5 调度器，比较不同复习策略的「工作量—保持率」曲线。

先说清它能证明什么、不能证明什么：

- **能**：在 FSRS 的记忆模型下，把同一批卡片维持到某个可提取水平，各策略要花多少次复习、
  复习会被推到多长的间隔上、漏掉一天会怎样。这是工作量与形状的结论，可以直接指导默认值。
- **不能**：证明「间隔复习真的比重新阅读有效」。那是文献里的事（检索练习与间隔效应的
  大量实验和元分析），本项目只是**采纳**了这个结论。用 FSRS 自己的公式去"验证"FSRS 的
  假设是循环论证 —— 想要真证据，需要跨月留存数据或对照实验。

所以每一行输出都是「模型推演」口径，绝不写成「实测」。

用法：
    python3 tools/effectiveness_sim.py                 # 默认 400 张卡、90 天
    python3 tools/effectiveness_sim.py --cards 800 --days 180 --seed 7
"""
from __future__ import annotations

import argparse
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boil.srs.fsrs import Scheduler  # noqa: E402

LEARN_DAYS = 5          # 前 5 天把新卡学完，每天引入一批
CHECKPOINTS = (30, 60, 90)
TARGET = 0.9            # 「还稳得住」的可提取性门槛，与调度器的目标保留率一致
DAY = 86400.0

LABELS = {
    "fsrs": "到期才复习（本项目）",
    "fixed3": "每 3 天全量翻一遍",
    "daily": "每天全量翻一遍",
    "none": "学一遍就不管",
}
ORDER = ("fsrs", "fixed3", "daily", "none")


def _dt(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, timezone.utc)


def _apply(card: dict, result) -> None:
    card["state"] = result.state
    card["step"] = result.step
    card["stability"] = result.stability
    card["difficulty"] = result.difficulty
    card["due_ts"] = result.due
    card["last_review_ts"] = result.reviewed_at
    card["reps"] = result.reps
    card["lapses"] = result.lapses


def rate_from_memory(rng: random.Random, retrievability: float, latent: float, state: str) -> int:
    """按模型给出的可提取性抽一次「想起来了没」，再决定评分。

    抽成功/失败就是 retrievability 的定义本身；评分档位反映人的体感：
    完全想不起 → 忘了(1)，想起来但吃力 → 模糊(2)，轻松 → 记得(3)/很简单(4)。
    """
    if state in ("new", "learning", "relearning"):
        return 3 if rng.random() < 0.85 else 2      # 短程步骤里内容还在眼前
    if rng.random() >= retrievability:
        return 1
    if rng.random() < (0.55 - 0.4 * retrievability) * (1.0 + latent):
        return 2
    return 4 if (retrievability > 0.97 and latent < 0.0) else 3


def simulate(strategy: str, cards: int, days: int, seed: int, compliance: float) -> dict:
    # 不用内置 hash()：它每个进程都随机化，同种子会跑出不同结果
    rng = random.Random(seed + sum(ord(c) for c in strategy) + int(compliance * 100))
    sched = Scheduler()
    step_days = {"fsrs": None, "fixed3": 3.0, "daily": 1.0, "none": float("inf")}[strategy]

    deck = []
    per_day = max(1, cards // LEARN_DAYS)
    for i in range(cards):
        intro = min(days - 1, i // per_day)
        deck.append({
            "card": {"state": "new", "step": 0, "stability": 0.0, "difficulty": 0.0,
                     "last_review_ts": intro * DAY, "due_ts": intro * DAY, "reps": 0, "lapses": 0},
            "latent": rng.gauss(0.0, 0.35),        # 这张卡本来就难 / 本来就简单
            "introduced": intro,
            "reviews": 0,
            "learned": False,
        })

    points = {c: [] for c in CHECKPOINTS if c <= days}
    for day in range(days + 1):
        ts = day * DAY
        for item in deck:
            card = item["card"]
            if day < item["introduced"]:
                continue
            if not item["learned"]:                                    # 第一天：把卡学会
                _apply(card, sched.schedule(card, 3, _dt(ts)))
                item["learned"] = True
                item["reviews"] += 1
                continue
            if strategy == "none":
                continue
            if step_days is None:                                      # 跟调度器算出的 due 走
                if card["due_ts"] / DAY > day:
                    continue
                if rng.random() >= compliance:                         # 今天没做 → 顺延
                    card["due_ts"] += (1 + rng.randint(0, 1)) * DAY
                    continue
            else:                                                      # 固定周期，不管到没到期
                if day - card["last_review_ts"] / DAY + 1e-9 < step_days:
                    continue
                if rng.random() >= compliance:
                    card["due_ts"] += DAY
                    continue
            elapsed = max(0.0, (ts - float(card["last_review_ts"])) / DAY)
            r = sched.retrievability(float(card["stability"]), elapsed) if card["stability"] else 0.0
            _apply(card, sched.schedule(
                card, rate_from_memory(rng, r, item["latent"], card["state"]), _dt(ts)))
            item["reviews"] += 1
        if day in points:
            points[day] = [
                sched.retrievability(float(i["card"]["stability"]),
                                     max(0.0, (ts - float(i["card"]["last_review_ts"])) / DAY))
                for i in deck if i["learned"]
            ]

    learned = [i for i in deck if i["learned"]]
    live = [i["card"] for i in deck if i["card"]["state"] == "review"]
    reviews = sum(i["reviews"] for i in deck)
    out = {
        "strategy": strategy,
        "reviews": reviews,
        "reviews_per_card": round(reviews / max(1, len(learned)), 2),
        "mean_interval_days": round(sum(max(0.0, (c["due_ts"] - c["last_review_ts"]) / DAY)
                                        for c in live) / len(live), 1) if live else 0.0,
        "lapses": sum(1 for i in deck if i["card"]["lapses"]),
        "points": {day: {"retention": round(sum(v) / len(v), 3),
                         "solid": round(sum(1 for x in v if x >= TARGET) / len(v), 3)}
                   for day, v in points.items() if v},
    }
    return out


def _table(rows: dict, compliance: float, days: int) -> str:
    head = f"执行率 {compliance:.0%}  ·  {'策略':<20}{'复习次数':>9}{'次/卡':>8}{'平均间隔':>10}{'遗忘卡':>8}"
    for c in CHECKPOINTS:
        if c <= days:
            head += f"{f'{c}天R':>8}{f'{c}天稳':>8}"
    lines = [head, "-" * len(head)]
    for key in ORDER:
        r = rows[key]
        line = f"{'':>4}{LABELS[key]:<18}{r['reviews']:>10}{r['reviews_per_card']:>9}" \
               f"{r['mean_interval_days']:>9.1f}天{r['lapses']:>9}"
        for c in CHECKPOINTS:
            p = r["points"].get(c)
            line += f"{p['retention']:>8.2f}{p['solid']:>8.0%}" if p else f"{'—':>8}{'—':>8}"
        lines.append(line)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="用项目自己的 FSRS 推演不同复习策略的代价")
    parser.add_argument("--cards", type=int, default=400)
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--seed", type=int, default=20261024)
    parser.add_argument("--compliance", type=float, default=0.9)
    args = parser.parse_args()
    days = max(max(CHECKPOINTS), args.days)

    print(f"\n模型推演（不是真人实测）· {args.cards} 张卡 · {days} 天 · 种子 {args.seed}")
    print(f"『稳』= 考察点上当下想得起的概率 ≥ {TARGET:.0%} 的卡片比例\n")
    tables = {}
    for compliance in (args.compliance, 0.6):
        tables[compliance] = {key: simulate(key, args.cards, days, args.seed, compliance)
                              for key in ORDER}
        print(_table(tables[compliance], compliance, days))
        print("")

    key = max(c for c in CHECKPOINTS if c in tables[args.compliance]["fsrs"]["points"])
    hi = tables[args.compliance]
    fs, fi, da = hi["fsrs"], hi["fixed3"], hi["daily"]
    fs_lo = tables[0.6]["fsrs"] if args.compliance != 0.6 else fs
    print(f"""读法（以 {key} 天为参考点，执行率 {args.compliance:.0%}）：
  · 保持率：到期才复习 {fs['points'][key]['retention']:.2f}（稳 {fs['points'][key]['solid']:.0%}）·
            每 3 天全量 {fi['points'][key]['retention']:.2f}（稳 {fi['points'][key]['solid']:.0%}）·
            每天全量 {da['points'][key]['retention']:.2f}（稳 {da['points'][key]['solid']:.0%}）
  · 代价：到期才复习 {fs['reviews']} 次，每天全量 {da['reviews']} 次 —— 少做 {da['reviews'] / max(1, fs['reviews']):.1f} 倍的功，
    代价是 R 低 {max(0.0, da['points'][key]['retention'] - fs['points'][key]['retention']):.2f}。这就是 request_retention=0.9 这个默认值在做的事：
    不是「记得最牢」，而是「够用的前提下少做很多功」。调到 0.95 会更牢，但复习量还要涨。
  · 漏一天会怎样：执行率 {args.compliance:.0%} → 60%，到期才复习的 {key} 天保持率 {fs['points'][key]['retention']:.2f} → {fs_lo['points'][key]['retention']:.2f}
    （稳住的卡 {fs['points'][key]['solid']:.0%} → {fs_lo['points'][key]['solid']:.0%}）。调度器再准也救不了不复习的人，
    所以产品重心必须是「今天这几张卡」这种小到能完成的承诺，而不是更聪明的算法。

这不是效果证明。要证明「这样学真的更记得住」需要：
  1. 跨月留存数据：真实用户第 30/90 天的**实测**回忆率，而不是模型给的 R；
  2. 策略间对照：同一批用户/卡片比较两种安排的结果（单用户本地产品做不到盲对照）；
  3. 校准检验：模型预测的 R 与实测回忆率的偏差 —— 即 FSRS 默认参数在这份数据上是否可信。
本项目目前只有 (0)：机制完整、可审计、可复现，长期证据为空白。
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
