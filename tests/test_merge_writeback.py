import sqlite3
import threading

import pytest

from app.db import get_db
from app.services import files, merge_repair, plans, reconcile, rewrite
from app.services.errors import ServiceError


def _make_successful_plan(db):
    p = plans.create_plan("CP-WB", "回写计划", "dw_payments", db_path=db)
    plans.attach_loose_files(p["id"], db_path=db)
    plans.publish_plan(p["id"], db_path=db)
    job = rewrite.start_rewrite(p["id"], db_path=db)
    assert job["status"] == "成功"
    return p["id"], job


def _break_like_legacy_bug(db, plan_id):
    """复刻旧缺陷：任务成功、芯片亮，但碎件态停在已入计划、无快照。"""
    with get_db(db) as conn:
        conn.execute(
            "UPDATE lake_files SET status='已入计划' "
            "WHERE id IN (SELECT file_id FROM plan_items WHERE plan_id=?)",
            (plan_id,),
        )
        conn.execute("DELETE FROM merge_success_snapshots WHERE plan_id=?", (plan_id,))
        conn.execute("DELETE FROM merge_writebacks WHERE plan_id=?", (plan_id,))


def test_success_rewrite_promotes_and_writes_back(db):
    plan_id, job = _make_successful_plan(db)

    merged = [f for f in files.list_files("dw_payments", db_path=db)
              if f["status"] == "已合并"]
    assert merged
    assert all(f["writeback_chip"] == "已回写" for f in merged)

    with get_db(db) as conn:
        snap = conn.execute(
            "SELECT COUNT(*) c, SUM(size_bytes) s FROM merge_success_snapshots "
            "WHERE plan_id=?", (plan_id,)
        ).fetchone()
        wb = conn.execute(
            "SELECT * FROM merge_writebacks WHERE plan_id=?", (plan_id,)
        ).fetchone()
    assert snap["c"] == len(merged)
    assert snap["s"] == job["merged_bytes"]
    assert wb["job_id"] == job["id"]
    assert wb["chip"] == "已回写"

    state = merge_repair.plan_state(plan_id, db_path=db)
    assert state["aligned"] is True
    # 队列与清单同轮可读
    assert rewrite.list_jobs(db_path=db)[0]["chip"] == "已回写"


def test_reconcile_refuses_half_state(db):
    plan_id, _ = _make_successful_plan(db)
    _break_like_legacy_bug(db, plan_id)

    with pytest.raises(ServiceError) as exc:
        reconcile.run_reconcile(plan_id, db_path=db)
    assert "纠偏回写" in exc.value.message
    # 拒绝时不得落报告
    assert reconcile.list_reports(db_path=db) == []


def test_repair_promotes_files_and_clears_chip(db):
    plan_id, _ = _make_successful_plan(db)
    _break_like_legacy_bug(db, plan_id)

    state = merge_repair.plan_state(plan_id, db_path=db)
    assert state["aligned"] is False
    assert "已合并" in state["reason"]

    out = merge_repair.repair(plan_id, db_path=db)
    assert out["changed"] is True
    assert out["merged_count"] == out["file_count"]

    merged = [f for f in files.list_files("dw_payments", db_path=db)
              if f["status"] == "已合并"]
    assert len(merged) == out["file_count"]
    assert all(f["writeback_chip"] == "已回写" for f in merged)

    report = reconcile.run_reconcile(plan_id, db_path=db)
    assert report["before_bytes"] == report["after_bytes"]


def test_repair_twice_is_idempotent(db):
    plan_id, _ = _make_successful_plan(db)
    _break_like_legacy_bug(db, plan_id)
    first = merge_repair.repair(plan_id, db_path=db)
    second = merge_repair.repair(plan_id, db_path=db)
    assert first["changed"] is True
    assert second["changed"] is False
    assert second["aligned"] is True

    with get_db(db) as conn:
        n = conn.execute(
            "SELECT COUNT(*) c FROM merge_success_snapshots WHERE plan_id=?",
            (plan_id,),
        ).fetchone()["c"]
    assert n == first["file_count"]


def test_repair_rejects_volume_mismatch(db):
    plan_id, _ = _make_successful_plan(db)
    _break_like_legacy_bug(db, plan_id)
    with get_db(db) as conn:
        conn.execute(
            "UPDATE lake_files SET size_bytes = size_bytes + 1 "
            "WHERE id IN (SELECT file_id FROM plan_items WHERE plan_id=?)",
            (plan_id,),
        )

    with pytest.raises(ServiceError) as exc:
        merge_repair.repair(plan_id, db_path=db)
    assert "体积对不上" in exc.value.message

    # 拒绝后碎件态不得被改写
    with get_db(db) as conn:
        stuck = conn.execute(
            "SELECT COUNT(*) c FROM lake_files WHERE status='已入计划' AND id IN "
            "(SELECT file_id FROM plan_items WHERE plan_id=?)", (plan_id,)
        ).fetchone()["c"]
    assert stuck > 0


def test_concurrent_repair_does_not_double_write(db):
    plan_id, _ = _make_successful_plan(db)
    _break_like_legacy_bug(db, plan_id)

    results, errors = [], []

    def worker():
        try:
            results.append(merge_repair.repair(plan_id, db_path=db))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    assert sum(1 for r in results if r["changed"]) == 1
    assert all(r["aligned"] for r in results)

    with get_db(db) as conn:
        snap = conn.execute(
            "SELECT COUNT(*) c FROM merge_success_snapshots WHERE plan_id=?",
            (plan_id,),
        ).fetchone()["c"]
        wb = conn.execute(
            "SELECT COUNT(*) c FROM merge_writebacks WHERE plan_id=?", (plan_id,)
        ).fetchone()["c"]
    assert wb == 1
    assert snap == results[0]["file_count"]
