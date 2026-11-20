import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.config import Config

BUSY_TIMEOUT_MS = 15000

SCHEMA = """
CREATE TABLE IF NOT EXISTS lake_files (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  path TEXT NOT NULL UNIQUE,
  table_name TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('碎文件','已入计划','已合并','作废')),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS compact_plans (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  plan_code TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('草稿','已发布','执行中','已完成','已作废')),
  target_table TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS plan_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  plan_id INTEGER NOT NULL REFERENCES compact_plans(id),
  file_id INTEGER NOT NULL REFERENCES lake_files(id),
  UNIQUE(plan_id, file_id)
);

CREATE TABLE IF NOT EXISTS rewrite_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  plan_id INTEGER NOT NULL REFERENCES compact_plans(id),
  status TEXT NOT NULL CHECK (status IN ('待执行','执行中','成功','失败')),
  merged_bytes INTEGER NOT NULL DEFAULT 0,
  message TEXT NOT NULL DEFAULT '',
  started_at TEXT,
  finished_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS volume_reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  plan_id INTEGER NOT NULL,
  before_bytes INTEGER NOT NULL,
  after_bytes INTEGER NOT NULL,
  file_count INTEGER NOT NULL,
  summary TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 合并成功切面：独立快照表，冻住成功那一刻的文件与体积，晋升碎件态前必先落这。
CREATE TABLE IF NOT EXISTS merge_success_snapshots (
  plan_id INTEGER NOT NULL,
  job_id INTEGER NOT NULL,
  file_id INTEGER NOT NULL,
  size_bytes INTEGER NOT NULL,
  frozen_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (job_id, file_id)
);

-- 成功回写徽记：合并队列读这一行判「已回写」，与快照同事务落下。
CREATE TABLE IF NOT EXISTS merge_writebacks (
  plan_id INTEGER PRIMARY KEY,
  job_id INTEGER NOT NULL,
  file_count INTEGER NOT NULL,
  merged_bytes INTEGER NOT NULL,
  chip TEXT NOT NULL,
  written_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS lab_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  label TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


def connect(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or Config.database()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    return conn


@contextmanager
def get_db(db_path: str | None = None):
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def write_txn(db_path: str | None = None):
    """写事务：BEGIN IMMEDIATE 先拿写锁，杜绝两名仓管纠偏双写互相覆盖。"""
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema(db_path: str | None = None) -> None:
    with get_db(db_path) as conn:
        conn.executescript(SCHEMA)
