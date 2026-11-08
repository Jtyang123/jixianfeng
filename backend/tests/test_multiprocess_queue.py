from __future__ import annotations

import json
import os
import subprocess
import sys
import time
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
    outputs = [tmp_path / "worker-1.jsonl", tmp_path / "worker-2.jsonl"]
    backend_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    started = time.perf_counter()
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

    wall_seconds = time.perf_counter() - started
    records = []
    for output in outputs:
        records.extend(json.loads(line) for line in output.read_text(encoding="utf-8").splitlines() if line.strip())
    claimed_ids = [item["run_id"] for item in records]
    latencies = sorted((item["completed_at"] - item["claimed_at"]) * 1000 for item in records)
    events = sorted(
        [(item["claimed_at"], 1) for item in records] + [(item["completed_at"], -1) for item in records],
        key=lambda item: (item[0], item[1]),
    )
    active = peak = 0
    for _, delta in events:
        active += delta
        peak = max(peak, active)
    throughput = len(records) / wall_seconds if wall_seconds else 0
    p95_latency = latencies[max(0, int(len(latencies) * 0.95) - 1)] if latencies else 0

    assert len(records) == 30
    assert len(set(claimed_ids)) == 30
    assert all(db.get_run(run_id)["status"] == "awaiting_approval" for run_id in claimed_ids)
    assert 1 < peak <= 2
    assert throughput > 0
    assert p95_latency > 0
    assert wall_seconds < 10
    print({
        "samples": len(records), "wall_seconds": round(wall_seconds, 4),
        "throughput_runs_per_second": round(throughput, 2), "p95_latency_ms": round(p95_latency, 2),
        "peak_concurrency": peak,
    })
