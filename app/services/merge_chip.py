"""合并成功芯片：与碎件态同源，禁止只碰展示的半修。"""
from __future__ import annotations

import sqlite3

from app.db import get_db


def mark_job_success_chip(
    job_id: int,
    db_path: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    """写成功芯片。传入 conn 时并入调用方事务，绝不另开连接。"""
    if conn is not None:
        _write_chip(conn, job_id)
        return
    with get_db(db_path) as own:
        _write_chip(own, job_id)


def _write_chip(conn: sqlite3.Connection, job_id: int) -> None:
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
    """已废弃的半修路径：只写展示徽记不晋升碎件态，必然被对账拒绝。

    保留函数只为让旧调用点显式失败，禁止再把这里当成回写实现。
    """
    raise RuntimeError("半修路径已停用：成功回写必须落快照并晋升碎件态")
