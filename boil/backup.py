"""完整备份导入/导出。

导出不包含 llm 配置（由调用方另行脱敏）；导入按表白名单过滤，避免任意 SQL/字段注入。
导入前先把当前库快照到 data/backups/，替换导入失败时还能回到导入前的状态。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config
from .db import Database, json_loads
from .utils import now_iso

SNAPSHOTS_KEPT = 10


def backup_dir() -> Path:
    """运行时读取 config.DATA_DIR，测试里换了数据目录不会写到仓库的 data/。"""
    return config.DATA_DIR / "backups"


def snapshot_db(db: Database, reason: str = "pre_import") -> str | None:
    """用 SQLite 备份 API 而不是复制文件：WAL 下直接复制会拿到不一致的快照。"""
    directory = backup_dir()
    directory.mkdir(parents=True, exist_ok=True)
    # 带微秒：同一秒内连续两次导入不能互相覆盖快照
    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S_%f")
    target = directory / f"{reason}_{stamp}.db"
    try:
        with sqlite3.connect(str(db.path)) as src:
            dest = sqlite3.connect(str(target))
            try:
                src.backup(dest)
            finally:
                dest.close()
    except (sqlite3.Error, OSError):
        # 快照失败不能阻断导入本身；但要把文件清干净，避免留下半成品
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    prune_snapshots(reason)
    return target.name


def prune_snapshots(reason: str = "pre_import") -> int:
    removed = 0
    directory = backup_dir()
    if not directory.exists():
        return 0
    files = sorted(directory.glob(f"{reason}_*.db"), key=lambda p: p.name, reverse=True)
    for old in files[SNAPSHOTS_KEPT:]:
        try:
            old.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def list_snapshots() -> list[dict[str, Any]]:
    directory = backup_dir()
    if not directory.exists():
        return []
    out = []
    for p in sorted(directory.glob("*.db"), key=lambda x: x.name, reverse=True):
        try:
            out.append({"name": p.name, "bytes": p.stat().st_size})
        except OSError:
            continue
    return out

# 顺序：先父后子；没有外键，但顺序仍然让导入结果符合直觉。
IMPORT_TABLES: list[tuple[str, bool]] = [
    ("packs", False),
    ("goals", True),
    ("plans", False),
    ("goal_packs", False),
    ("units", False),
    ("relations", False),
    ("probes", False),
    ("cards", True),
    ("sessions", True),
    ("messages", False),
    ("attempts", True),
    ("evidence", True),
    ("mastery", True),
    ("review_logs", True),
    ("explanations", True),
    ("boil_records", True),
    ("events", True),
    ("study_days", True),
    ("unit_notes", True),
    ("notes", True),
]


def _table_columns(db: Database, table: str) -> list[str]:
    return [r["name"] for r in db.query(f"PRAGMA table_info({table})")]


def import_backup(db: Database, user_id: str, payload: dict[str, Any],
                  mode: str = "merge") -> dict[str, Any]:
    if mode not in ("merge", "replace"):
        raise ValueError("mode 必须是 merge 或 replace")
    data = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
    if not isinstance(data, dict):
        raise ValueError("备份必须是 JSON 对象")
    if not isinstance(data.get("goals"), list) and not isinstance(data.get("units"), list):
        raise ValueError("备份缺少 goals/units，无法识别为 Boil 备份")

    from . import services
    snapshot = snapshot_db(db, "pre_import")
    # 清库与写入放进同一个事务：中途任何一条炸掉都整体回滚，
    # 不会出现「旧的已删、新的没进来」的空库。
    with db.transaction():
        if mode == "replace":
            services.reset_user(db, user_id, commit=False)
        services.ensure_user(db, user_id)

        counts: dict[str, int] = {}
        for table, has_user in IMPORT_TABLES:
            rows = data.get(table) or []
            if not isinstance(rows, list):
                continue
            cols = _table_columns(db, table)
            imported = 0
            for raw in rows:
                if not isinstance(raw, dict):
                    continue
                row = {k: v for k, v in raw.items() if k in cols}
                if not row:
                    continue
                if has_user:
                    row["user_id"] = user_id
                if table == "events":
                    row.pop("seq", None)
                cols_use = list(row.keys())
                placeholders = ", ".join("?" for _ in cols_use)
                sql = f"INSERT OR REPLACE INTO {table} ({', '.join(cols_use)}) VALUES ({placeholders})"
                db.execute(sql, [row[c] for c in cols_use])
                imported += 1
            counts[table] = imported
    return {
        "mode": mode,
        "counts": counts,
        "imported_at": now_iso(),
        "snapshot": snapshot,
        "goals": int(counts.get("goals", 0)),
        "units": int(counts.get("units", 0)),
        "cards": int(counts.get("cards", 0)),
        "records": int(counts.get("boil_records", 0)),
        "notes": int(counts.get("unit_notes", 0)),
        "inspirations": int(counts.get("notes", 0)),
    }


def backup_info(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    source = data.get("data") if isinstance(data.get("data"), dict) else data
    return {
        "app": source.get("app") or "Boil",
        "version": source.get("version"),
        "exported_at": source.get("exported_at"),
        "goals": len(source.get("goals") or []),
        "units": len(source.get("units") or []),
        "cards": len(source.get("cards") or []),
        "sessions": len(source.get("sessions") or []),
    }
