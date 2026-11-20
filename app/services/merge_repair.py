"""合并成功回写：冻结成功切面 → 晋升碎件态 → 落回写徽记，一次事务内完成。

选定轨道：独立快照表（merge_success_snapshots）冻成功切面，再晋升
lake_files.status；回写徽记（merge_writebacks）与快照同事务提交。
禁止只改展示芯片的半修，禁止快照与晋升分成两轮并行写。
"""
from __future__ import annotations

import sqlite3

from app.db import get_db, write_txn
from app.services.errors import ServiceError
from app.services.merge_chip import mark_job_success_chip

CHIP_OK = "已回写"
CHIP_BAD = "芯片成功"
CHIP_NONE = "未回写"
STATUS_MERGED = "已合并"


def _plan(conn: sqlite3.Connection, plan_id: int):
    row = conn.execute(
        "SELECT * FROM compact_plans WHERE id=?", (plan_id,)
    ).fetchone()
    if not row:
        raise ServiceError("计划不存在", 404)
    return row


def latest_success_job(conn: sqlite3.Connection, plan_id: int):
    return conn.execute(
        "SELECT * FROM rewrite_jobs WHERE plan_id=? AND status='成功' "
        "ORDER BY id DESC LIMIT 1",
        (plan_id,),
    ).fetchone()


def plan_files(conn: sqlite3.Connection, plan_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT f.* FROM plan_items i JOIN lake_files f ON f.id=i.file_id "
        "WHERE i.plan_id=? AND f.status != '作废' ORDER BY f.id",
        (plan_id,),
    ).fetchall()


def freeze_success_slice(
    conn: sqlite3.Connection, plan_id: int, job_id: int
) -> int:
    """把成功那一刻的文件与体积冻进独立快照表，返回冻结字节数。"""
    files = plan_files(conn, plan_id)
    conn.execute(
        "DELETE FROM merge_success_snapshots WHERE job_id=?", (job_id,)
    )
    total = 0
    for row in files:
        size = int(row["size_bytes"])
        conn.execute(
            "INSERT INTO merge_success_snapshots(plan_id, job_id, file_id, size_bytes) "
            "VALUES (?,?,?,?)",
            (plan_id, job_id, row["id"], size),
        )
        total += size
    return total


def promote_files(conn: sqlite3.Connection, plan_id: int) -> int:
    """按冻结的成功切面晋升碎件态；只推进未作废的计划内碎件。"""
    files = plan_files(conn, plan_id)
    for row in files:
        conn.execute(
            "UPDATE lake_files SET status=? WHERE id=?",
            (STATUS_MERGED, row["id"]),
        )
    return len(files)


def record_writeback(
    conn: sqlite3.Connection,
    plan_id: int,
    job_id: int,
    file_count: int,
    merged_bytes: int,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO merge_writebacks"
        "(plan_id, job_id, file_count, merged_bytes, chip) VALUES (?,?,?,?,?)",
        (plan_id, job_id, file_count, merged_bytes, CHIP_OK),
    )
    mark_job_success_chip(job_id, conn=conn)
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
        (f"merge_writeback_{plan_id}", CHIP_OK),
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
        ("merge_skew_panel_chip", CHIP_OK),
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
        ("merge_skew_truth", CHIP_OK),
    )


def _state_from_conn(conn: sqlite3.Connection, plan_id: int) -> dict:
    plan = conn.execute(
        "SELECT * FROM compact_plans WHERE id=?", (plan_id,)
    ).fetchone()
    if not plan:
        return {"plan_id": plan_id, "exists": False, "aligned": False,
                "reason": "计划不存在", "writeback": "", "chip": ""}
    job = latest_success_job(conn, plan_id)
    files = plan_files(conn, plan_id)
    wb = conn.execute(
        "SELECT * FROM merge_writebacks WHERE plan_id=?", (plan_id,)
    ).fetchone()
    snaps = conn.execute(
        "SELECT file_id, size_bytes FROM merge_success_snapshots WHERE plan_id=?",
        (plan_id,),
    ).fetchall()
    snap_ids = {int(r["file_id"]) for r in snaps}
    snap_bytes = sum(int(r["size_bytes"]) for r in snaps)
    actual_bytes = sum(int(r["size_bytes"]) for r in files)
    merged_count = sum(1 for r in files if r["status"] == STATUS_MERGED)
    problems: list[str] = []
    if not job:
        problems.append("没有成功的合并任务")
    if not files:
        problems.append("计划下没有可回写的碎文件")
    if job and not wb:
        problems.append("成功任务还没有回写徽记")
    if job and wb and int(wb["job_id"]) != int(job["id"]):
        problems.append("回写徽记指向的合并任务不是最新成功任务")
    if files and merged_count != len(files):
        problems.append(f"碎件态未晋升（{merged_count}/{len(files)} 已合并）")
    if files and snap_ids != {int(r["id"]) for r in files}:
        problems.append("成功快照与计划碎件切面不一致")
    if job and snap_bytes != actual_bytes:
        problems.append(
            f"体积不一致（快照 {snap_bytes} 字节 / 现行 {actual_bytes} 字节）"
        )
    if job and wb and int(wb["merged_bytes"]) != int(job["merged_bytes"]):
        problems.append("回写徽记体积与合并任务不一致")
    if job and int(job["merged_bytes"]) != actual_bytes:
        problems.append(
            f"合并任务体积与计划碎件不一致"
            f"（任务 {int(job['merged_bytes'])} / 计划 {actual_bytes}）"
        )
    aligned = not problems
    if not job:
        chip = CHIP_NONE
    else:
        chip = CHIP_OK if aligned else CHIP_BAD
    return {
        "plan_id": plan_id,
        "exists": True,
        "plan_code": plan["plan_code"],
        "plan_status": plan["status"],
        "job_id": int(job["id"]) if job else None,
        "job_status": job["status"] if job else None,
        "file_count": len(files),
        "merged_count": merged_count,
        "merged_bytes": int(job["merged_bytes"]) if job else 0,
        "snapshot_bytes": snap_bytes,
        "actual_bytes": actual_bytes,
        "writeback": wb["chip"] if wb else "",
        "chip": chip,
        "aligned": aligned,
        "reason": "；".join(problems),
    }


def plan_state(plan_id: int, db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        return _state_from_conn(conn, plan_id)


def write_success_inside_txn(
    conn: sqlite3.Connection, plan_id: int, job_id: int
) -> dict:
    """调用方已持写锁：冻结切面 → 晋升 → 回写，全部在同一次事务里。"""
    job = conn.execute(
        "SELECT * FROM rewrite_jobs WHERE id=?", (job_id,)
    ).fetchone()
    if not job:
        raise ServiceError("合并任务不存在", 404)
    frozen = freeze_success_slice(conn, plan_id, job_id)
    if int(job["merged_bytes"]) != frozen:
        raise ServiceError(
            f"回写失败：快照冻 {frozen} 字节，合并任务记 "
            f"{int(job['merged_bytes'])} 字节，体积对不上，未晋升碎件态"
        )
    count = promote_files(conn, plan_id)
    record_writeback(
        conn, plan_id, job_id, count, int(job["merged_bytes"])
    )
    return _state_from_conn(conn, plan_id)


def repair(plan_id: int, db_path: str | None = None) -> dict:
    """仓管点「纠偏回写」：同一写事务内选边落快照再切现行，失败说白话。

    两名仓管同时纠偏时，BEGIN IMMEDIATE 让后者排队；轮到它时切面已齐，
    直接幂等返回，不会双写碎件态，也不会互相覆盖徽记。
    """
    with write_txn(db_path) as conn:
        _plan(conn, plan_id)
        job = latest_success_job(conn, plan_id)
        if not job:
            raise ServiceError(
                "该计划还没有成功的合并任务：请先跑完重写合并，再点纠偏回写",
                409,
            )
        files = plan_files(conn, plan_id)
        if not files:
            raise ServiceError("计划下没有可回写的碎文件，纠偏无从下手", 409)
        actual = sum(int(r["size_bytes"]) for r in files)
        if actual != int(job["merged_bytes"]):
            raise ServiceError(
                f"纠偏失败：合并任务记 {int(job['merged_bytes'])} 字节，"
                f"计划碎件实际 {actual} 字节，体积对不上；"
                f"请先核对计划挂载再回写",
                409,
            )
        before = _state_from_conn(conn, plan_id)
        if before["aligned"] and before["writeback"] == CHIP_OK:
            return {**before, "changed": False,
                    "message": "成功切面已经对齐，无需重复回写"}
        state = write_success_inside_txn(conn, plan_id, int(job["id"]))
        # 收尾自检：晋升后再核一遍体积与数量才算回写完成
        if not state["aligned"]:
            raise ServiceError(
                f"纠偏后核对未通过：{state['reason']}；本次未提交", 409
            )
        if state["merged_count"] != state["file_count"]:
            raise ServiceError(
                "纠偏后核对未通过：碎件数量与成功快照不一致；本次未提交", 409
            )
        conn.execute(
            "UPDATE compact_plans SET status='已完成' WHERE id=? AND status != '已作废'",
            (plan_id,),
        )
        return {
            **state,
            "changed": True,
            "message": (
                f"已回写：{state['merged_count']} 个碎文件晋升为已合并，"
                f"合并队列亮已回写，体积 {state['merged_bytes']} 字节核对一致"
            ),
        }


def overall_truth(db_path: str | None = None) -> tuple[str, str]:
    """全局真相：所有有成功任务的计划都齐了才算已回写，否则是芯片假绿。"""
    with get_db(db_path) as conn:
        plan_ids = [
            int(r["plan_id"])
            for r in conn.execute(
                "SELECT DISTINCT plan_id FROM rewrite_jobs WHERE status='成功'"
            ).fetchall()
        ]
        if not plan_ids:
            return CHIP_NONE, "尚无成功合并任务"
        states = [_state_from_conn(conn, pid) for pid in plan_ids]
    bad = [s for s in states if not s["aligned"]]
    if bad:
        return CHIP_BAD, bad[0]["reason"]
    return CHIP_OK, ""
