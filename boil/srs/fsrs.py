"""FSRS-5 兼容的四档间隔重复调度器。

相比 mem 原实现：
- 使用 FSRS-5 的 19 个权重与短时记忆公式；
- 四档评分（1 忘了 / 2 模糊 / 3 记得 / 4 很简单）；
- new / learning / review / relearning 四状态显式建模；
- schedule() 与 preview() 共用同一套逻辑，UI 预览与真实落库不会漂移；
- 所有时间只在这一层换算，外部一律 ISO UTC。

算法参考：open-spaced-repetition/fsrs4anki（FSRS-5）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

UTC = timezone.utc

# FSRS-5 默认权重（w0..w18）
DEFAULT_WEIGHTS: tuple[float, ...] = (
    0.40255, 1.18385, 3.173, 15.69105, 7.1949, 0.5345, 1.4604, 0.0046,
    1.54575, 0.1192, 1.01925, 1.9395, 0.11, 0.29605, 2.2698, 0.2315,
    2.9898, 0.51655, 0.6621,
)

DECAY = -0.5
FACTOR = 19.0 / 81.0
MIN_STABILITY = 0.1
MAX_INTERVAL_DAYS = 36500

RATING_LABELS = {1: "忘了", 2: "模糊", 3: "记得", 4: "很简单"}
STATE_LABELS = {
    "new": "未学习",
    "learning": "学习中",
    "review": "复习中",
    "relearning": "重学中",
}


@dataclass
class MemoryState:
    stability: float = 0.0
    difficulty: float = 0.0
    last_review: float = 0.0


@dataclass
class ScheduleResult:
    state: str
    step: int
    stability: float
    difficulty: float
    due: float
    reviewed_at: float
    interval_seconds: float
    elapsed_days: float
    retention_before: float
    reps: int
    lapses: int

    @property
    def interval_days(self) -> float:
        return self.interval_seconds / 86400.0

    def due_iso(self) -> str:
        return datetime.fromtimestamp(self.due, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def interval_label(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return f"{max(1, int(round(seconds)))} 秒"
    if seconds < 3600:
        return f"{max(1, int(round(seconds / 60)))} 分钟"
    if seconds < 86400:
        return f"{max(1, int(round(seconds / 3600)))} 小时"
    days = seconds / 86400.0
    if days < 30:
        return f"{max(1, int(round(days)))} 天"
    if days < 365:
        return f"{max(1, int(round(days / 30)))} 个月"
    return f"{max(1, round(days / 365, 1))} 年"


class Scheduler:
    def __init__(self, weights: Iterable[float] = DEFAULT_WEIGHTS,
                 request_retention: float = 0.9):
        self.w = tuple(float(x) for x in weights)
        if len(self.w) < 19:
            raise ValueError("FSRS-5 需要 19 个权重")
        self.request_retention = _clamp(float(request_retention), 0.7, 0.97)

    # ---------- 基础公式 ----------
    def _init_stability(self, rating: int) -> float:
        return max(self.w[max(0, min(3, rating - 1))], MIN_STABILITY)

    def _init_difficulty(self, rating: int) -> float:
        d = self.w[4] - (rating - 3) * self.w[5]
        return _clamp(d, 1.0, 10.0)

    def _next_difficulty(self, difficulty: float, rating: int) -> float:
        d0_easy = self._init_difficulty(4)
        moved = difficulty - self.w[6] * (rating - 3)
        d = self.w[7] * d0_easy + (1.0 - self.w[7]) * moved
        return _clamp(d, 1.0, 10.0)

    def retrievability(self, stability: float, elapsed_days: float) -> float:
        if stability <= 0:
            return 0.0
        elapsed_days = max(0.0, elapsed_days)
        return float((1.0 + FACTOR * elapsed_days / stability) ** DECAY)

    def _next_recall_stability(self, difficulty: float, stability: float,
                               retention: float, rating: int) -> float:
        hard_penalty = self.w[15] if rating == 2 else 1.0
        easy_bonus = self.w[16] if rating == 4 else 1.0
        growth = (
            math.exp(self.w[8])
            * (11.0 - difficulty)
            * (stability ** (-self.w[9]))
            * (math.exp((1.0 - retention) * self.w[10]) - 1.0)
            * hard_penalty
            * easy_bonus
        )
        return max(stability * (1.0 + growth), MIN_STABILITY)

    def _next_forget_stability(self, difficulty: float, stability: float,
                               retention: float) -> float:
        value = (
            self.w[11]
            * (difficulty ** (-self.w[12]))
            * ((stability + 1.0) ** self.w[13] - 1.0)
            * math.exp((1.0 - retention) * self.w[14])
        )
        return max(min(value, stability), MIN_STABILITY)

    def _next_short_term_stability(self, stability: float, rating: int) -> float:
        value = stability * math.exp(self.w[17] * (rating - 3 + self.w[18]))
        return max(value, MIN_STABILITY)

    def _next_interval(self, stability: float) -> float:
        # I = S / FACTOR * (R^(1/DECAY) - 1)
        try:
            days = stability / FACTOR * (self.request_retention ** (1.0 / DECAY) - 1.0)
        except (OverflowError, ValueError, ZeroDivisionError):
            days = stability
        return _clamp(days, 1.0, MAX_INTERVAL_DAYS)

    # ---------- 状态机 ----------
    def schedule(self, card: dict, rating: int, now: datetime | None = None) -> ScheduleResult:
        rating = int(_clamp(int(rating), 1, 4))
        now_dt = now or datetime.now(UTC)
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=UTC)
        now_ts = now_dt.timestamp()

        state = (card.get("state") or "new").lower()
        step = int(card.get("step") or 0)
        stability = float(card.get("stability") or 0.0)
        difficulty = float(card.get("difficulty") or 0.0)
        reps = int(card.get("reps") or 0) + 1
        lapses = int(card.get("lapses") or 0)
        last_review = float(card.get("last_review_ts") or 0.0)
        if not last_review and card.get("last_review"):
            try:
                last_review = datetime.fromisoformat(
                    str(card["last_review"]).replace("Z", "+00:00")
                ).timestamp()
            except ValueError:
                last_review = 0.0

        elapsed_days = max(0.0, (now_ts - last_review) / 86400.0) if last_review else 0.0
        retention_before = self.retrievability(stability, elapsed_days) if stability else 0.0

        if state == "new" or stability <= 0:
            stability = self._init_stability(rating)
            difficulty = self._init_difficulty(rating)
            if rating == 1:
                due = now_ts + 60
                new_state, new_step = "learning", 0
            elif rating == 2:
                due = now_ts + 300
                new_state, new_step = "learning", 0
            elif rating == 3:
                due = now_ts + 600
                new_state, new_step = "learning", 1
            else:
                due = now_ts + max(4.0, self._next_interval(stability)) * 86400
                new_state, new_step = "review", 0
            return self._result(new_state, new_step, stability, difficulty, due,
                                now_ts, elapsed_days, retention_before, reps, lapses)

        if state in ("learning", "relearning"):
            stability = self._next_short_term_stability(stability, rating)
            difficulty = self._next_difficulty(difficulty, rating)
            if rating == 1:
                due = now_ts + (60 if state == "learning" else 600)
                new_state, new_step = state, 0
            elif rating == 2:
                due = now_ts + (300 if state == "learning" else 600)
                new_state, new_step = state, step
            elif rating == 3:
                if state == "learning" and step < 1:
                    due = now_ts + 600
                    new_state, new_step = "learning", step + 1
                else:
                    days = max(1.0, self._next_interval(stability)) if state == "relearning" else self._next_interval(stability)
                    due = now_ts + days * 86400
                    new_state, new_step = "review", 0
            else:
                days = max(2.0, self._next_interval(stability))
                due = now_ts + days * 86400
                new_state, new_step = "review", 0
            return self._result(new_state, new_step, stability, difficulty, due,
                                now_ts, elapsed_days, retention_before, reps, lapses)

        # review
        new_difficulty = self._next_difficulty(difficulty, rating)
        if rating == 1:
            stability = self._next_forget_stability(difficulty, stability, retention_before)
            due = now_ts + 600
            new_state, new_step = "relearning", 0
            lapses += 1
        else:
            stability = self._next_recall_stability(difficulty, stability, retention_before, rating)
            days = self._next_interval(stability)
            if rating == 2:
                days = max(1.0, min(days, max(1.0, self._next_interval(stability) * 1.0)))
            elif rating == 4:
                days = max(days, 2.0)
            due = now_ts + days * 86400
            new_state, new_step = "review", 0
        return self._result(new_state, new_step, stability, new_difficulty, due,
                            now_ts, elapsed_days, retention_before, reps, lapses)

    def _result(self, state: str, step: int, stability: float, difficulty: float,
                due: float, now_ts: float, elapsed_days: float,
                retention_before: float, reps: int, lapses: int) -> ScheduleResult:
        return ScheduleResult(
            state=state,
            step=step,
            stability=float(stability),
            difficulty=float(difficulty),
            due=float(due),
            reviewed_at=now_ts,
            interval_seconds=max(0.0, due - now_ts),
            elapsed_days=float(elapsed_days),
            retention_before=float(retention_before),
            reps=reps,
            lapses=lapses,
        )

    def preview(self, card: dict, now: datetime | None = None) -> dict[int, ScheduleResult]:
        return {r: self.schedule(card, r, now) for r in (1, 2, 3, 4)}

    def preview_labels(self, card: dict, now: datetime | None = None) -> dict[str, str]:
        out = {}
        for rating, result in self.preview(card, now).items():
            out[str(rating)] = interval_label(result.interval_seconds)
        return out


def card_from_row(row: dict) -> dict:
    """把数据库卡片行转换成 scheduler 需要的形状。"""
    return {
        "state": row.get("state") or "new",
        "step": row.get("step") or 0,
        "stability": row.get("stability") or 0.0,
        "difficulty": row.get("difficulty") or 0.0,
        "last_review": row.get("last_review"),
        "reps": row.get("reps") or 0,
        "lapses": row.get("lapses") or 0,
    }


scheduler = Scheduler()
