"""Parallel execution helpers for tolerance Monte Carlo batches."""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any, Callable


def resolve_job_count(jobs: int) -> int:
    """``jobs=0`` → ``max(1, cpu_count-1)``; ``jobs<0`` → 1."""
    if jobs == 0:
        n = os.cpu_count() or 1
        return max(1, n - 1)
    return max(1, jobs)


def run_parallel_tasks(
    tasks: list[dict[str, Any]],
    worker: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    jobs: int = 1,
) -> list[dict[str, Any]]:
    """Run picklable tasks; preserve input order in results."""
    if not tasks:
        return []
    n_workers = resolve_job_count(jobs)
    if n_workers == 1 or len(tasks) == 1:
        return [worker(task) for task in tasks]

    ordered: list[dict[str, Any] | None] = [None] * len(tasks)
    with ProcessPoolExecutor(max_workers=min(n_workers, len(tasks))) as pool:
        futures = {pool.submit(worker, task): i for i, task in enumerate(tasks)}
        for fut in as_completed(futures):
            slot = futures[fut]
            ordered[slot] = fut.result()
    return [r for r in ordered if r is not None]
