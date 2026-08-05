"""Run a block testbench once and extract declared measures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from benchgate.sim.analysis import evaluate_checks
from benchgate.sim.runner import run_ngspice
from benchgate.sim.sweep import absolutize_includes


def _measure_checks(measures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for item in measures:
        alias = item.get("alias") or item.get("name")
        signal = item.get("signal") or item.get("expr")
        metric = item.get("metric")
        if not alias or not signal or not metric:
            raise ValueError(f"measure entry needs alias, signal, metric: {item!r}")
        check: dict[str, Any] = {
            "alias": alias,
            "metric": metric,
        }
        if item.get("expr"):
            check["expr"] = item["expr"]
        else:
            check["signal"] = signal
        for key in (
            "window_after",
            "window_before",
            "settle_pct",
            "settle_to",
            "abs",
        ):
            if key in item:
                check[key] = item[key]
        checks.append(check)
    return checks


def run_block_measures(
    *,
    testbench: Path,
    measures: list[dict[str, Any]],
    output_dir: Path,
) -> dict[str, float]:
    """Run one ``.cir`` testbench and return scalar metrics keyed by measure alias."""
    if not testbench.is_file():
        raise FileNotFoundError(f"testbench not found: {testbench}")
    output_dir.mkdir(parents=True, exist_ok=True)
    text = absolutize_includes(testbench.read_text(encoding="utf-8"), testbench.parent)
    cir = output_dir / "measures.cir"
    cir.write_text(text, encoding="utf-8")
    result = run_ngspice(cir, work_dir=output_dir)
    if not result.raw_output or not result.raw_output.exists():
        raise RuntimeError(f"ngspice produced no raw output for {testbench}")

    from benchgate.sim.analysis import parse_ngspice_raw

    axis, signals = parse_ngspice_raw(result.raw_output)
    report = evaluate_checks(axis, signals, _measure_checks(measures))
    alias_scale = {
        str(m.get("alias") or m.get("name")): float(m.get("scale", 1.0)) for m in measures
    }
    out: dict[str, float] = {}
    for check in report.checks:
        key = check.alias or f"{check.signal}:{check.metric}"
        scale = alias_scale.get(key, 1.0)
        out[key] = check.value * scale
    summary = {
        "testbench": str(testbench),
        "ngspice_ok": result.success,
        "metrics": out,
        "checks": [c.to_dict() for c in report.checks],
    }
    (output_dir / "block_measures.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )
    return out


def write_metrics_file(path: Path, metrics: dict[str, float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
