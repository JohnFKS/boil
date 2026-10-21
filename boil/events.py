"""追加式事件日志：可审计、可回放、可用于活动流与同步。"""
from __future__ import annotations

from typing import Any

from .db import Database, json_dumps
from .utils import new_id, now_iso, today_str


def emit(db: Database, event_type: str, payload: dict[str, Any] | None = None,
         user_id: str = "local") -> dict[str, Any]:
    event = {
        "id": new_id("ev"),
        "user_id": user_id,
        "type": event_type,
        "payload_json": json_dumps(payload or {}),
        "created_at": now_iso(),
    }
    db.insert("events", event)
    return event


def recent(db: Database, user_id: str = "local", limit: int = 30,
           after: int = 0) -> list[dict[str, Any]]:
    rows = db.query(
        "SELECT seq, id, type, payload_json, created_at FROM events "
        "WHERE user_id=? AND seq>? ORDER BY seq DESC LIMIT ?",
        (user_id, after, min(max(limit, 1), 200)),
    )
    import json
    for r in rows:
        try:
            r["payload"] = json.loads(r.pop("payload_json") or "{}")
        except ValueError:
            r["payload"] = {}
    return rows


def touch_study_day(db: Database, user_id: str, minutes: int = 0,
                    sessions: int = 0, reviews: int = 0) -> None:
    day = today_str()
    row = db.one("SELECT * FROM study_days WHERE user_id=? AND day=?", (user_id, day))
    if row:
        db.execute(
            "UPDATE study_days SET minutes=minutes+?, sessions=sessions+?, reviews=reviews+? "
            "WHERE user_id=? AND day=?",
            (max(0, minutes), max(0, sessions), max(0, reviews), user_id, day),
        )
    else:
        db.insert("study_days", {
            "user_id": user_id, "day": day, "minutes": max(0, minutes),
            "sessions": max(0, sessions), "reviews": max(0, reviews),
        })
