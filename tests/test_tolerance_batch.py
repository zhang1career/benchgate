"""Unit tests for parallel tolerance batch helpers."""

from __future__ import annotations

from benchgate.sim.tolerance_batch import resolve_job_count, run_parallel_tasks


def test_resolve_job_count():
    assert resolve_job_count(-1) == 1
    assert resolve_job_count(4) == 4
    assert resolve_job_count(0) >= 1


def test_run_parallel_tasks_serial():
    tasks = [{"index": i, "x": i} for i in range(5)]

    def worker(task: dict) -> dict:
        return {"index": task["index"], "y": task["x"] * 2}

    results = run_parallel_tasks(tasks, worker, jobs=1)
    assert len(results) == 5
    assert [r["y"] for r in sorted(results, key=lambda r: r["index"])] == [0, 2, 4, 6, 8]


def _parallel_worker(task: dict) -> dict:
    return {"index": task["index"], "y": task["x"] + 1}


def test_run_parallel_tasks_preserves_batch_order_with_global_index():
    tasks = [{"index": 100 + i, "x": i} for i in range(4)]
    results = run_parallel_tasks(tasks, _parallel_worker, jobs=2)
    assert [r["index"] for r in results] == [100, 101, 102, 103]
    assert all(r["y"] == task["x"] + 1 for r, task in zip(results, tasks))
