from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from app.database import Database
from app.run_queue import RunQueueService


def main() -> None:
    db_path, worker_id, output_path = sys.argv[1:4]
    db = Database(Path(db_path), database_url="")
    queue = RunQueueService(db)
    output = Path(output_path)
    while True:
        claimed = queue.claim_next(worker_id, lease_seconds=30)
        if claimed:
            claimed_at = time.time()
            time.sleep(0.015)
            db.update_run(claimed["id"], "awaiting_approval", {"worker": worker_id})
            completed_at = time.time()
            queue.finish(claimed["id"])
            with output.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "run_id": claimed["id"], "worker": worker_id,
                    "claimed_at": claimed_at, "completed_at": completed_at,
                }, ensure_ascii=False) + "\n")
            continue
        with db.connect() as conn:
            active = conn.execute("SELECT COUNT(*) AS c FROM runs WHERE status IN ('queued','running')").fetchone()["c"]
        if int(active) == 0:
            break
        time.sleep(0.01)


if __name__ == "__main__":
    main()
