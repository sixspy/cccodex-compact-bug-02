"""合并偏斜：展示面与写库面同源，半修已停用，只认回写切面。"""
from __future__ import annotations

import sqlite3

from app.db import get_db
from app.services.merge_repair import CHIP_NONE, CHIP_OK, overall_truth

_SLUG = "merge_skew"


def panel_chip(db_path: str | None = None) -> str:
    """展示芯片直接取真相，不再单独存一份可被写花的展示值。"""
    truth, _ = overall_truth(db_path)
    return truth


def truth_flag(db_path: str | None = None) -> str:
    truth, _ = overall_truth(db_path)
    return truth


def truth_reason(db_path: str | None = None) -> str:
    _, reason = overall_truth(db_path)
    return reason


def paint_ok(db_path: str | None = None) -> str:
    """展示面没资格单独刷绿；只回：要良心就得走纠偏回写。"""
    return panel_chip(db_path)


def seed_skew(
    db_path: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    """旧版遗留的展示/真相双源键清掉，避免假绿残留在库里。

    传入 conn 时并入调用方事务，避免与种子写事务互锁。
    """
    if conn is not None:
        _purge_keys(conn)
        return
    with get_db(db_path) as own:
        _purge_keys(own)


def _purge_keys(conn: sqlite3.Connection) -> None:
    conn.execute(
        "DELETE FROM meta WHERE key IN (?, ?, ?)",
        (f"{_SLUG}_panel_chip", f"{_SLUG}_truth", f"{_SLUG}_counter"),
    )
    conn.execute(
        "DELETE FROM meta WHERE key LIKE ?", (f"{_SLUG}_fake_%",)
    )


def bump_counter(
    db_path: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> int:
    if conn is not None:
        return _bump(conn)
    with get_db(db_path) as own:
        return _bump(own)


def _bump(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT value FROM meta WHERE key=?", (f"{_SLUG}_counter",)
    ).fetchone()
    n = int(row["value"]) + 1 if row else 1
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
        (f"{_SLUG}_counter", str(n)),
    )
    return n


def snapshot(db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        truth, reason = overall_truth(db_path)
        return {
            "title": "合并偏斜",
            "panel_chip": truth,
            "truth": truth,
            "reason": reason,
            "counter": bump_counter(db_path, conn),
            "aligned": truth == CHIP_OK,
        }


def list_events(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT value FROM meta WHERE key LIKE ? ORDER BY key",
            (f"{_SLUG}_evt_%",),
        ).fetchall()
        return [{"note": r["value"]} for r in rows]


def record_event(note: str, db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        c = bump_counter(db_path, conn)
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            (f"{_SLUG}_evt_{c}", note or CHIP_NONE),
        )
    return snapshot(db_path)


def reconcile_surface(db_path: str | None = None) -> dict:
    """半修对齐已停用：只报告真实切面，不再刷展示芯片假装对齐。"""
    snap = snapshot(db_path)
    return {
        "panel_chip": snap["panel_chip"],
        "truth": snap["truth"],
        "reason": snap["reason"],
        "half_fixed": False,
        "message": "展示面不能单独对齐；请对计划点纠偏回写把成功切面写齐",
    }
