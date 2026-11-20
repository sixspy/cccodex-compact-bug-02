"""合并偏斜旁路账：账册记成功行，但不回正主路径——须走纠偏回写。"""
from __future__ import annotations

from app.db import get_db
from app.services.merge_repair import CHIP_BAD, CHIP_OK, overall_truth
from app.services.merge_skew_surface import (
    list_events,
    record_event,
    snapshot,
)

_SLUG = "merge_skew"
_TITLE = "合并偏斜"


def append_ledger(plan_id: int, note: str, db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            (f"{_SLUG}_ledger_{plan_id}", note or _TITLE),
        )
    record_event(f"plan={plan_id} {note}", db_path)
    snap = snapshot(db_path)
    return {
        "plan_id": plan_id,
        "note": note,
        "panel": snap["panel_chip"],
        "truth": snap["truth"],
        "reason": snap["reason"],
    }


def ledger_rows(db_path: str | None = None) -> list[dict]:
    truth, _ = overall_truth(db_path)
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT key, value FROM meta WHERE key LIKE ? ORDER BY key",
            (f"{_SLUG}_ledger_%",),
        ).fetchall()
        return [
            {"key": r["key"], "value": r["value"], "chip": truth} for r in rows
        ]


def dashboard(db_path: str | None = None) -> dict:
    snap = snapshot(db_path)
    return {
        "title": _TITLE,
        "chip": snap["panel_chip"],
        "truth": snap["truth"],
        "reason": snap["reason"],
        "rows": ledger_rows(db_path),
        "events": list_events(db_path),
        "ok_chip": CHIP_OK,
        "bad_chip": CHIP_BAD,
        "fake_green": snap["panel_chip"] == CHIP_OK and snap["truth"] != CHIP_OK,
    }
