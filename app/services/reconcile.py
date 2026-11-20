"""体积对账：对照计划挂载体积与合并结果，成功切面未对齐即拒绝出报告。"""
from __future__ import annotations

from app.db import get_db, write_txn
from app.services.errors import ServiceError
from app.services.merge_repair import _state_from_conn, latest_success_job


def list_reports(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT r.*, p.plan_code, p.target_table FROM volume_reports r "
            "JOIN compact_plans p ON p.id=r.plan_id ORDER BY r.id DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def run_reconcile(plan_id: int, db_path: str | None = None) -> dict:
    """对账前先核成功芯片与碎件态是否齐：偏了立即拒绝，不出半修报告。"""
    with write_txn(db_path) as conn:
        plan = conn.execute(
            "SELECT * FROM compact_plans WHERE id=?", (plan_id,)
        ).fetchone()
        if not plan:
            raise ServiceError("计划不存在", 404)
        job = latest_success_job(conn, plan_id)
        if job:
            state = _state_from_conn(conn, plan_id)
            if not state["aligned"]:
                raise ServiceError(
                    f"对账被拒：成功芯片与碎件态未对齐（{state['reason']}）；"
                    f"请先点纠偏回写把成功切面写齐，再跑体积对账",
                    409,
                )
        before = conn.execute(
            "SELECT COALESCE(SUM(f.size_bytes),0) AS b, COUNT(*) AS c "
            "FROM plan_items i JOIN lake_files f ON f.id=i.file_id "
            "WHERE i.plan_id=? AND f.status != '作废'",
            (plan_id,),
        ).fetchone()
        after = int(job["merged_bytes"]) if job else 0
        before_b = int(before["b"])
        summary = (
            f"计划#{plan_id} {plan['plan_code']}：挂载前 {before_b} 字节 / "
            f"{before['c']} 文件，合并后记 {after} 字节"
        )
        if job and before_b != after:
            summary += "；体积不一致须人工核对"
        cur = conn.execute(
            "INSERT INTO volume_reports(plan_id, before_bytes, after_bytes, file_count, summary) "
            "VALUES (?,?,?,?,?)",
            (plan_id, before_b, after, int(before["c"]), summary),
        )
        return dict(
            conn.execute(
                "SELECT * FROM volume_reports WHERE id=?", (cur.lastrowid,)
            ).fetchone()
        )
