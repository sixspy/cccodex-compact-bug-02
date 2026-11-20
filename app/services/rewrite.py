"""重写合并：按计划执行合并任务，成功即同事务冻结切面并晋升碎件态。"""
from __future__ import annotations

from app.db import get_db, write_txn
from app.services.errors import ServiceError
from app.services.merge_repair import write_success_inside_txn


def list_jobs(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT j.*, p.plan_code, p.target_table, w.chip AS writeback_chip "
            "FROM rewrite_jobs j "
            "JOIN compact_plans p ON p.id=j.plan_id "
            "LEFT JOIN merge_writebacks w ON w.plan_id=j.plan_id AND w.job_id=j.id "
            "ORDER BY j.id DESC"
        ).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            item["chip"] = "已回写" if item.pop("writeback_chip") else "未回写"
            out.append(item)
        return out


def start_rewrite(plan_id: int, db_path: str | None = None) -> dict:
    with write_txn(db_path) as conn:
        plan = conn.execute(
            "SELECT * FROM compact_plans WHERE id=?", (plan_id,)
        ).fetchone()
        if not plan:
            raise ServiceError("计划不存在", 404)
        if plan["status"] not in ("已发布", "执行中"):
            raise ServiceError("仅已发布计划可启动合并", 409)
        running = conn.execute(
            "SELECT id FROM rewrite_jobs WHERE plan_id=? AND status IN ('待执行','执行中')",
            (plan_id,),
        ).fetchone()
        if running:
            raise ServiceError("已有在途合并任务", 409)
        items = conn.execute(
            "SELECT f.* FROM plan_items i JOIN lake_files f ON f.id=i.file_id "
            "WHERE i.plan_id=? AND f.status IN ('已入计划','已合并') ORDER BY f.id",
            (plan_id,),
        ).fetchall()
        if not items:
            raise ServiceError("计划下无可合并碎文件", 409)
        total = sum(int(x["size_bytes"]) for x in items)
        cur = conn.execute(
            "INSERT INTO rewrite_jobs(plan_id, status, merged_bytes, message, started_at) "
            "VALUES (?, '执行中', 0, '合并进行中', datetime('now'))",
            (plan_id,),
        )
        job_id = cur.lastrowid
        conn.execute(
            "UPDATE compact_plans SET status='执行中' WHERE id=?", (plan_id,)
        )
        # 成功：任务行、成功快照、碎件态晋升、回写徽记同事务落库，不留半态
        conn.execute(
            "UPDATE rewrite_jobs SET status='成功', merged_bytes=?, message=?, "
            "finished_at=datetime('now') WHERE id=?",
            (total, f"已合并 {len(items)} 个碎文件", job_id),
        )
        state = write_success_inside_txn(conn, plan_id, job_id)
        conn.execute(
            "UPDATE compact_plans SET status='已完成' WHERE id=?", (plan_id,)
        )
        return {
            **dict(
                conn.execute(
                    "SELECT * FROM rewrite_jobs WHERE id=?", (job_id,)
                ).fetchone()
            ),
            "chip": "已回写" if state["aligned"] else "未回写",
            "promoted": state["merged_count"],
        }


def fail_open_job(job_id: int, reason: str, db_path: str | None = None) -> dict:
    """预留：标记失败（基线测试与切口）。"""
    with get_db(db_path) as conn:
        job = conn.execute(
            "SELECT * FROM rewrite_jobs WHERE id=?", (job_id,)
        ).fetchone()
        if not job:
            raise ServiceError("任务不存在", 404)
        conn.execute(
            "UPDATE rewrite_jobs SET status='失败', message=?, finished_at=datetime('now') "
            "WHERE id=?",
            (reason or "合并失败", job_id),
        )
        return dict(
            conn.execute(
                "SELECT * FROM rewrite_jobs WHERE id=?", (job_id,)
            ).fetchone()
        )
