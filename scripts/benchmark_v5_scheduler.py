"""Isolated scheduler/API load benchmark; no models, tools, or external targets.

Run: python3 scripts/benchmark_v5_scheduler.py --sizes 100 1000
JSON goes to stdout. Reported costs are deterministic fixture units.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient
import app
import final_core
from version import APP_VERSION


def summary(values):
    ordered = sorted(values)
    return {"count": len(values), "p50_ms": round(statistics.median(ordered), 3),
            "p95_ms": round(ordered[min(len(ordered)-1, int(len(ordered)*.95))], 3)}


def measure(size):
    with tempfile.TemporaryDirectory(prefix="fieldwork-scheduler-") as directory, ExitStack() as stack:
        root = Path(directory)
        database = root / "benchmark.db"
        for module, name, value in ((app, "DB", database), (app, "DATA", root),
                                     (final_core, "DB", database), (final_core, "LOCAL_DATA_ROOT", root)):
            stack.enter_context(patch.object(module, name, value))
        stack.enter_context(patch.dict(os.environ, {"PYTEST_CURRENT_TEST": "scheduler-benchmark"}))
        client = stack.enter_context(TestClient(app.app, base_url="http://127.0.0.1:8000"))

        def call(method, path, body=None):
            response = client.request(method, "/api/v1" + path, json=body)
            if response.status_code >= 400:
                raise RuntimeError(f"{method} {path}: {response.status_code} {response.text}")
            return response.json()

        engagement = call("POST", "/engagements", {"name": "Scheduler benchmark", "mode": "traditional",
                          "target": "https://scheduler.example.test", "scope": {}, "policy": {}})
        call("POST", f"/engagements/{engagement['id']}/confirm")
        campaign = call("POST", f"/engagements/{engagement['id']}/campaigns",
                        {"name": "Fixture only", "objective": "Measure scheduler behavior"})
        budget = size // 2 + 1  # Deliberately not a multiple of the eight-worker wave.
        group = call("POST", "/orchestration/groups", {"campaign_id": campaign["id"], "role": "coordinator",
                     "objective": "Bounded queue", "budget": {"max_concurrency": 8, "max_cost_micros": budget}})
        capsule = {"objective": "Inspect referenced fixture", "relevant_claim_ids": ["fixture-claim"],
                   "relevant_evidence_ids": ["fixture-evidence"], "known_failures": [], "open_questions": []}
        started = time.perf_counter()
        for index in range(size):
            call("POST", "/orchestration/tasks", {"campaign_id": campaign["id"], "group_id": group["id"],
                 "role": "researcher", "objective": "Measure fixture work", "context_capsule": capsule,
                 "idempotency_key": f"benchmark-{index}", "budget": {"max_cost_micros": 1}})
        creation_seconds = time.perf_counter() - started
        runners = [f"bench-runner-{index}" for index in range(4)]
        for rid in runners:
            call("PUT", f"/runners/{rid}", {"id": rid, "name": rid, "max_concurrency": 2})
        lease_times, completion_times, peak_runner, peak_group = [], [], 0, 0
        completed, recovered = set(), None

        def acquire(rid):
            started = time.perf_counter()
            result = call("POST", "/orchestration/lease", {"runner_id": rid, "lease_seconds": 300})
            return result["task"], (time.perf_counter() - started) * 1000

        def complete(item):
            started = time.perf_counter()
            result = call("POST", f"/orchestration/tasks/{item['id']}/complete", {
                "runner_id": item["lease_owner"], "lease_attempt": item["attempt"], "outcome": "succeeded",
                "result": {"fixture": True}, "usage": {"input_tokens": 13, "output_tokens": 7, "cost_micros": 1, "runtime_ms": 1}})
            assert result["status"] == "succeeded"
            return result["id"], (time.perf_counter() - started) * 1000

        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=16) as pool:
            for wave in range(size + 1):
                leased = list(pool.map(acquire, runners * 4))
                lease_times.extend(elapsed for _, elapsed in leased)
                tasks = [item for item, _ in leased if item]
                assert len({item["id"] for item in tasks}) == len(tasks)
                with final_core.connect() as db:
                    live = db.execute("SELECT COUNT(*) FROM agent_tasks WHERE status='running'").fetchone()[0]
                    per_runner = db.execute("SELECT MAX(active_jobs) FROM runner_registry_v5").fetchone()[0]
                peak_group, peak_runner = max(peak_group, live), max(peak_runner, per_runner)
                assert live <= 8 and per_runner <= 2
                if not tasks:
                    break
                if wave == 0:
                    abandoned, tasks = tasks[:4], tasks[4:]
                    with final_core.connect() as db:
                        db.executemany("UPDATE agent_tasks SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",
                                       [(item["id"],) for item in abandoned])
                    # Separate process reopens the persisted DB; no in-memory scheduler state.
                    recovery_start = time.perf_counter()
                    child = subprocess.run([sys.executable, "-c",
                        "import json,sys; from v5_orchestration import recover_expired_leases; print(json.dumps(recover_expired_leases(sys.argv[1])))",
                        str(database)], cwd=ROOT, capture_output=True, text=True, check=True, timeout=30)
                    recovered = {**json.loads(child.stdout), "process_ms": round((time.perf_counter()-recovery_start)*1000, 3)}
                    assert recovered["requeued"] == 4 and recovered["failed"] == 0
                for task_id, elapsed in pool.map(complete, tasks):
                    assert task_id not in completed
                    completed.add(task_id)
                    completion_times.append(elapsed)
            else:
                raise AssertionError("scheduler did not reach its budget stop")
        elapsed = time.perf_counter() - started
        with final_core.connect() as db:
            totals = dict(db.execute("SELECT COUNT(*) tasks,SUM(input_tokens) input_tokens,SUM(output_tokens) output_tokens,"
                                    "SUM(cost_micros) cost_micros FROM agent_task_usage").fetchone())
            states = dict(db.execute("SELECT status,COUNT(*) FROM agent_tasks GROUP BY status").fetchall())
            capsule_sizes = [len(row[0].encode()) for row in db.execute("SELECT context_capsule_json FROM agent_tasks")]
            retries = db.execute("SELECT COUNT(*) FROM agent_tasks WHERE attempt=2 AND status='succeeded'").fetchone()[0]
        assert len(completed) == totals["tasks"] == totals["cost_micros"] == budget
        assert states == {"queued": size-budget, "succeeded": budget}
        assert retries == 4 and max(capsule_sizes) < 1024
        assert totals["input_tokens"] == budget*13 and totals["output_tokens"] == budget*7
        return {"logical_tasks": size, "creation_seconds": round(creation_seconds, 3), "execution_seconds": round(elapsed, 3),
                "completed_per_second": round(budget/elapsed, 3), "completed": budget, "remaining_queued": size-budget,
                "peak_group_active": peak_group, "group_limit": 8, "peak_runner_active": peak_runner, "runner_limit": 2,
                "lease": summary(lease_times), "complete": summary(completion_times), "recovery": recovered,
                "recovered_tasks_completed": retries, "usage": totals, "group_cost_budget": budget,
                "max_capsule_bytes": max(capsule_sizes), "total_capsule_bytes": sum(capsule_sizes)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", type=int, nargs="+", default=[100, 1000])
    args = parser.parse_args()
    if any(size < 32 or size > 10000 for size in args.sizes):
        parser.error("sizes must be between 32 and 10000")
    report = {"version": APP_VERSION, "kind": "isolated_scheduler_fixture", "results": [],
              "limitations": ["Synthetic task usage, not real model cost or research quality", "In-process HTTP transport; real SQLite concurrency",
                  "Lease expiry is injected; recovery reopens DB in a separate process", "Unreported external work after expiry/cancel is not charged by this benchmark",
                  "Compact fixture capsules only; does not establish production capsule quality"]}
    for size in args.sizes:
        report["results"].append(measure(size))
        print(f"completed isolated scheduler benchmark: {size} tasks", file=sys.stderr, flush=True)
    print(json.dumps(report, ensure_ascii=False, indent=2))
