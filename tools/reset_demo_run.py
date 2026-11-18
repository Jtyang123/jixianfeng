"""把指定运行复位到「待人工审批」状态，用于演示视频反复重录。

点过「人工审批通过」之后，系统会写入审批人、生成库存预留和执行任务，
而 `runResult.approvalDisplay` 变成已审批，"人工审批通过"按钮就消失了。
录第二遍时这个按钮点不到 —— 本脚本负责把它还原回来。

    python tools/reset_demo_run.py                          # 复位默认演示运行
    python tools/reset_demo_run.py --run-id RUN-XXX         # 复位指定运行
    python tools/reset_demo_run.py --dry-run                # 只看会改什么

复位内容：
1. runs.status -> awaiting_approval、approved -> 0、清空审批人与预留时间；
2. 删除该运行的 dispatch_tasks（审批后自动生成）；
3. 释放该运行的库存预留并把 locked_quantity 还回去，再删除预留记录；
4. 删除 agent_events 里的 human.approved 事件，避免重复计数。

保留不动：agent_events 其余事件、a2a_messages、llm_calls、agent_performance。
这些是演示素材，删了历史运行就不完整了。
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "backend" / "farmmatch.db"
DEFAULT_RUN = "RUN-20261114-210325-F769"
PENDING = "awaiting_approval"


def backup(db: Path) -> Path:
    target_dir = ROOT / "backend" / "backups"
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = target_dir / f"farmmatch-before-reset-{stamp}.db"
    shutil.copy2(db, target)
    return target


def reset(db_path: Path, run_id: str, dry_run: bool) -> int:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        run = conn.execute(
            "SELECT id, status, approved, approved_at, reserved_at FROM runs WHERE id=?",
            (run_id,),
        ).fetchone()
        if run is None:
            print(f"[x] 找不到运行 {run_id}，先确认 ID 是否正确。", file=sys.stderr)
            return 2

        print(f"运行 {run_id}")
        print(f"  当前状态 status={run['status']} approved={run['approved']} "
              f"approved_at={run['approved_at']}")

        active = conn.execute(
            "SELECT id, inventory_id, quantity FROM inventory_reservations "
            "WHERE run_id=? AND status='active'",
            (run_id,),
        ).fetchall()
        tasks = conn.execute(
            "SELECT COUNT(*) FROM dispatch_tasks WHERE run_id=?", (run_id,)
        ).fetchone()[0]
        approved_events = conn.execute(
            "SELECT COUNT(*) FROM agent_events WHERE run_id=? AND type='human.approved'",
            (run_id,),
        ).fetchone()[0]
        released = conn.execute(
            "SELECT COUNT(*) FROM inventory_reservations WHERE run_id=?", (run_id,)
        ).fetchone()[0]

        print(f"  待释放的活跃预留 {len(active)} 条（该运行预留记录共 {released} 条）")
        print(f"  待删除的执行任务 {tasks} 条")
        print(f"  待删除的审批事件 {approved_events} 条")

        needs_reset = (
            run["status"] != PENDING
            or run["approved"]
            or active
            or tasks
            or approved_events
        )
        if not needs_reset:
            print("[√] 已经是待审批状态，无需改动。")
            return 0

        if dry_run:
            print("[i] --dry-run：只打印，不写库。")
            return 0

        with conn:
            for row in active:
                conn.execute(
                    "UPDATE inventory SET locked_quantity=MAX(0, locked_quantity-?) WHERE id=?",
                    (row["quantity"], row["inventory_id"]),
                )
            conn.execute("DELETE FROM inventory_reservations WHERE run_id=?", (run_id,))
            conn.execute("DELETE FROM dispatch_tasks WHERE run_id=?", (run_id,))
            conn.execute(
                "DELETE FROM agent_events WHERE run_id=? AND type='human.approved'",
                (run_id,),
            )
            conn.execute(
                "UPDATE runs SET status=?, approved=0, approved_at=NULL, "
                "approved_by=NULL, approved_by_name='', approval_note='', "
                "reserved_at=NULL WHERE id=?",
                (PENDING, run_id),
            )

        after = conn.execute(
            "SELECT status, approved FROM runs WHERE id=?", (run_id,)
        ).fetchone()
        print(f"[√] 复位完成：status={after['status']} approved={after['approved']}")
        print("[!] 请刷新浏览器页面（或重新打开演示链接）后再录制。")
        return 0
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="把演示运行复位到待人工审批状态")
    parser.add_argument("--run-id", default=DEFAULT_RUN)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"[x] 数据库不存在：{db}", file=sys.stderr)
        return 2

    if not args.dry_run and not args.no_backup:
        print(f"[i] 备份到 {backup(db)}")

    return reset(db, args.run_id, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
