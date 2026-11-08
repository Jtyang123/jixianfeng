from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.database import Database
from app.run_queue import RunQueueService


def test_two_os_processes_claim_each_queued_run_exactly_once(tmp_path: Path):
    db = Database(tmp_path / "multi-process.db", database_url="")
    db.initialize()
    service = RunQueueService(db)
    for index in range(30):
        run_id = f"RUN-MP-{index + 1:02d}"
        db.create_run(run_id, "rainstorm-ii", {"scenario_id": "rainstorm-ii"})
        service.enqueue(run_id, float(index))

    worker_script = Path(__file__).with_name("queue_process_worker.py")
    outputs = [tmp_path / "worker-1.txt", tmp_path / "worker-2.txt"]
    backend_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(backend_root) + os.pathsep + environment.get("PYTHONPATH", "")
    processes = [
        subprocess.Popen(
            [sys.executable, str(worker_script), str(db.path), f"mp-worker-{index + 1}", str(outputs[index])],
            cwd=backend_root,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index in range(2)
    ]
    for process in processes:
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, f"worker failed: {stdout}\n{stderr}"

    claimed = []
    for output in outputs:
        claimed.extend(output.read_text(encoding="utf-8").splitlines())
    assert len(claimed) == 30
    assert len(set(claimed)) == 30
    assert all(db.get_run(run_id)["status"] == "awaiting_approval" for run_id in claimed)
