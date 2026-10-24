"""SQLite 数据层。

设计原则：
- 每个线程一个连接（ThreadingHTTPServer 每请求一个线程），WAL + busy_timeout。
- 所有 SQL 参数化；不拼接用户输入。
- 写入使用短事务；迁移用 schema_version 记录。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from . import config

SCHEMA_VERSION = 8

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  email TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS goals (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  title TEXT NOT NULL,
  subject TEXT NOT NULL DEFAULT '',
  target TEXT NOT NULL DEFAULT '',
  deadline TEXT,
  daily_minutes INTEGER NOT NULL DEFAULT 40,
  level TEXT NOT NULL DEFAULT 'beginner',
  status TEXT NOT NULL DEFAULT 'active',
  pack_id TEXT,
  meta_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plans (
  id TEXT PRIMARY KEY,
  goal_id TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  plan_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS packs (
  id TEXT PRIMARY KEY,
  version TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'subject',
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  manifest_json TEXT NOT NULL,
  installed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS goal_packs (
  goal_id TEXT NOT NULL,
  pack_id TEXT NOT NULL,
  version TEXT NOT NULL,
  installed_at TEXT NOT NULL,
  PRIMARY KEY (goal_id, pack_id)
);

CREATE TABLE IF NOT EXISTS units (
  id TEXT PRIMARY KEY,
  goal_id TEXT NOT NULL,
  pack_id TEXT NOT NULL,
  pack_unit_id TEXT NOT NULL,
  title TEXT NOT NULL,
  essence TEXT NOT NULL DEFAULT '',
  analogy TEXT NOT NULL DEFAULT '',
  example TEXT NOT NULL DEFAULT '',
  steps_json TEXT NOT NULL DEFAULT '[]',
  boundaries_json TEXT NOT NULL DEFAULT '[]',
  misconceptions_json TEXT NOT NULL DEFAULT '[]',
  faq_json TEXT NOT NULL DEFAULT '[]',
  tags_json TEXT NOT NULL DEFAULT '[]',
  key_points_json TEXT NOT NULL DEFAULT '[]',
  difficulty INTEGER NOT NULL DEFAULT 3,
  importance INTEGER NOT NULL DEFAULT 3,
  estimated_minutes INTEGER NOT NULL DEFAULT 45,
  order_index INTEGER NOT NULL DEFAULT 0,
  angle TEXT NOT NULL DEFAULT '',
  merged_into TEXT,
  merged_reason TEXT NOT NULL DEFAULT '',
  merged_op TEXT,
  source_ref_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(goal_id, pack_id, pack_unit_id)
);

CREATE TABLE IF NOT EXISTS relations (
  id TEXT PRIMARY KEY,
  goal_id TEXT NOT NULL,
  from_unit TEXT NOT NULL,
  to_unit TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'prerequisite'
);

CREATE TABLE IF NOT EXISTS probes (
  id TEXT PRIMARY KEY,
  unit_id TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'recall',
  prompt TEXT NOT NULL,
  answer TEXT NOT NULL DEFAULT '',
  rubric_json TEXT NOT NULL DEFAULT '[]',
  hints_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cards (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  unit_id TEXT NOT NULL,
  probe_id TEXT,
  front TEXT NOT NULL,
  back TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'new',
  step INTEGER NOT NULL DEFAULT 0,
  stability REAL NOT NULL DEFAULT 0,
  difficulty REAL NOT NULL DEFAULT 0,
  due_at TEXT,
  last_review TEXT,
  reps INTEGER NOT NULL DEFAULT 0,
  lapses INTEGER NOT NULL DEFAULT 0,
  mistake_tags_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  goal_id TEXT,
  unit_id TEXT,
  kind TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  stage TEXT NOT NULL DEFAULT '',
  turn INTEGER NOT NULL DEFAULT 0,
  meta_json TEXT NOT NULL DEFAULT '{}',
  started_at TEXT NOT NULL,
  ended_at TEXT
);

CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  meta_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attempts (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  unit_id TEXT NOT NULL,
  probe_id TEXT,
  kind TEXT NOT NULL DEFAULT 'recall',
  response TEXT NOT NULL DEFAULT '',
  score REAL NOT NULL DEFAULT 0,
  confidence REAL NOT NULL DEFAULT 0,
  hints_used INTEGER NOT NULL DEFAULT 0,
  feedback_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  unit_id TEXT NOT NULL,
  facet TEXT NOT NULL,
  score REAL NOT NULL,
  confidence REAL NOT NULL DEFAULT 0.6,
  weight REAL NOT NULL DEFAULT 1,
  source TEXT NOT NULL,
  session_id TEXT,
  attempt_id TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mastery (
  user_id TEXT NOT NULL,
  unit_id TEXT NOT NULL,
  facet TEXT NOT NULL,
  score REAL NOT NULL DEFAULT 0,
  confidence REAL NOT NULL DEFAULT 0,
  reps INTEGER NOT NULL DEFAULT 0,
  lapses INTEGER NOT NULL DEFAULT 0,
  last_evidence_at TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (user_id, unit_id, facet)
);

CREATE TABLE IF NOT EXISTS review_logs (
  id TEXT PRIMARY KEY,
  card_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  rating INTEGER NOT NULL,
  reviewed_at TEXT NOT NULL,
  elapsed_days REAL NOT NULL DEFAULT 0,
  scheduled_days REAL NOT NULL DEFAULT 0,
  stability_before REAL NOT NULL DEFAULT 0,
  stability_after REAL NOT NULL DEFAULT 0,
  difficulty_before REAL NOT NULL DEFAULT 0,
  difficulty_after REAL NOT NULL DEFAULT 0,
  retention_before REAL NOT NULL DEFAULT 0,
  state_before TEXT NOT NULL DEFAULT 'new',
  state_after TEXT NOT NULL DEFAULT 'new',
  mistake_tags_json TEXT NOT NULL DEFAULT '[]',
  missed_points INTEGER NOT NULL DEFAULT 0,
  source TEXT NOT NULL DEFAULT 'web'
);

CREATE TABLE IF NOT EXISTS explanations (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  unit_id TEXT NOT NULL,
  session_id TEXT,
  mode TEXT NOT NULL DEFAULT 'peer',
  text TEXT NOT NULL,
  score_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  id TEXT NOT NULL UNIQUE,
  user_id TEXT NOT NULL,
  type TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS study_days (
  user_id TEXT NOT NULL,
  day TEXT NOT NULL,
  minutes INTEGER NOT NULL DEFAULT 0,
  sessions INTEGER NOT NULL DEFAULT 0,
  reviews INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, day)
);

CREATE TABLE IF NOT EXISTS unit_notes (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  unit_id TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'note',
  content TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(user_id, unit_id, kind)
);

CREATE TABLE IF NOT EXISTS notes (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  goal_id TEXT,
  unit_id TEXT,
  kind TEXT NOT NULL DEFAULT 'inspiration',
  quote TEXT NOT NULL DEFAULT '',
  content TEXT NOT NULL DEFAULT '',
  anchor_json TEXT NOT NULL DEFAULT '{}',
  source TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS boil_records (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  unit_id TEXT NOT NULL,
  mode TEXT NOT NULL,
  level INTEGER NOT NULL DEFAULT 0,
  result_json TEXT NOT NULL DEFAULT '{}',
  heat_before REAL NOT NULL DEFAULT 0,
  heat_after REAL NOT NULL DEFAULT 0,
  pn_diff_json TEXT NOT NULL DEFAULT '[]',
  advice_id TEXT,
  accepted INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_units_goal ON units(goal_id, order_index);
CREATE INDEX IF NOT EXISTS idx_boil_records_user ON boil_records(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_unit_notes_user ON unit_notes(user_id, unit_id);
CREATE INDEX IF NOT EXISTS idx_relations_goal ON relations(goal_id);
CREATE INDEX IF NOT EXISTS idx_probes_unit ON probes(unit_id);
CREATE INDEX IF NOT EXISTS idx_cards_user_due ON cards(user_id, due_at);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id, started_at);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_evidence_user_unit ON evidence(user_id, unit_id);
CREATE INDEX IF NOT EXISTS idx_mastery_user ON mastery(user_id, unit_id);
CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_id, seq);
CREATE INDEX IF NOT EXISTS idx_notes_user ON notes(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notes_unit ON notes(user_id, unit_id);
"""

# 旧库升级用：新库由 SCHEMA 直接带上，旧库启动时幂等补列。
EXTRA_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "units": [("angle", "TEXT NOT NULL DEFAULT ''"),
              ("merged_into", "TEXT"), ("merged_reason", "TEXT NOT NULL DEFAULT ''"),
              ("merged_op", "TEXT")],
    "cards": [("mistake_tags_json", "TEXT NOT NULL DEFAULT '[]'")],
    "review_logs": [("mistake_tags_json", "TEXT NOT NULL DEFAULT '[]'"),
                    ("missed_points", "INTEGER NOT NULL DEFAULT 0")],
    "notes": [("anchor_json", "TEXT NOT NULL DEFAULT '{}'")],
}


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


WRITE_RETRY = 5
WRITE_RETRY_SECONDS = 0.05


def _retryable(exc: sqlite3.OperationalError) -> bool:
    text = str(exc).lower()
    return "locked" in text or "busy" in text


def _retry_write(fn, *args):
    """跨进程写冲突（MCP 服务、两个 boil 实例共用一个库）时重试几次再报错。"""
    for attempt in range(WRITE_RETRY):
        try:
            return fn(*args)
        except sqlite3.OperationalError as exc:
            if not _retryable(exc) or attempt == WRITE_RETRY - 1:
                raise
            time.sleep(WRITE_RETRY_SECONDS * (attempt + 1))


class Database:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or config.DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._write_lock = threading.RLock()
        self.init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=8000")
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
            self._local.conn = conn
        return conn

    def init_schema(self) -> None:
        with self._write_lock:
            conn = self._connect()
            try:
                conn.executescript(SCHEMA)
                for table, columns in EXTRA_COLUMNS.items():
                    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
                    for name, ddl in columns:
                        if name not in existing:
                            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
                conn.execute(
                    "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(SCHEMA_VERSION),),
                )
            finally:
                conn.close()

    # ---------- 基础查询 ----------
    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        cur = self.conn.execute(sql, tuple(params))
        return [dict(r) for r in cur.fetchall()]

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        cur = self.conn.execute(sql, tuple(params))
        return _row_to_dict(cur.fetchone())

    def scalar(self, sql: str, params: Iterable[Any] = (), default: Any = None) -> Any:
        row = self.conn.execute(sql, tuple(params)).fetchone()
        return row[0] if row is not None else default

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._write_lock:
            return _retry_write(self.conn.execute, sql, tuple(params))

    def executemany(self, sql: str, rows: Iterable[Iterable[Any]]) -> None:
        with self._write_lock:
            _retry_write(self.conn.executemany, sql, [tuple(r) for r in rows])

    def insert(self, table: str, data: dict[str, Any]) -> None:
        cols = list(data.keys())
        placeholders = ", ".join("?" for _ in cols)
        sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
        self.execute(sql, [data[c] for c in cols])

    def upsert(self, table: str, data: dict[str, Any], conflict: str) -> None:
        cols = list(data.keys())
        placeholders = ", ".join("?" for _ in cols)
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in conflict.split(","))
        sql = (
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT({conflict}) DO UPDATE SET {updates}"
        )
        self.execute(sql, [data[c] for c in cols])

    def transaction(self):
        return _Transaction(self)

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None


class _Transaction:
    def __init__(self, db: Database):
        self.db = db

    def __enter__(self):
        # 事务必须和单条写共用这把锁：RLock 只在同一线程内可重入，
        # 否则"线程 A 在事务里读改写、线程 B 直接 execute 写"就是 WAL 下的经典 BUSY 死锁，
        # 用户那边表现为连点两下评分，第二下 500。
        self.db._write_lock.acquire()
        for attempt in range(WRITE_RETRY):
            try:
                self.db.conn.execute("BEGIN IMMEDIATE")
                return self.db
            except sqlite3.OperationalError as exc:
                if not _retryable(exc) or attempt == WRITE_RETRY - 1:
                    self.db._write_lock.release()
                    raise
                time.sleep(WRITE_RETRY_SECONDS * (attempt + 1))
        raise sqlite3.OperationalError("无法开始写事务")

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.db.conn.execute("COMMIT")
            else:
                self.db.conn.execute("ROLLBACK")
        finally:
            self.db._write_lock.release()
        return False


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def json_loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return default
