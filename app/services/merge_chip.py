"""合并成功展示芯片：队列亮成功，碎件态可不同步。"""
from __future__ import annotations

from app.db import get_db


def mark_job_success_chip(job_id: int, db_path: str | None = None) -> None:
    with get_db(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            (f"merge_chip_{job_id}", "成功"),
        )


def file_status_chip(file_id: int, db_path: str | None = None) -> str:
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT status FROM lake_files WHERE id=?", (file_id,)
        ).fetchone()
        return row["status"] if row else "未知"


def promote_files_half(plan_id: int, db_path: str | None = None) -> int:
    """Half-fix: only flips chip meta, leaves lake_files status."""
    with get_db(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            (f"merge_promote_display_{plan_id}", "已合并"),
        )
        return 0
