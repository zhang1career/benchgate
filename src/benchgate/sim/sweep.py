"""Parameter sweep: run a sim profile over a grid of overrides, collect one metric per run.

A sweep axis is either:
  * ``--param NAME=v1,v2,...`` : overrides a ``.param NAME=`` line (feeds {NAME} expansion)
  * ``--set REF=v1,v2,...``    : overrides the value (last field) of element line ``REF ...``

The netlist is exported + prepared once (with the profile's stimulus/excludes), then each
grid point patches that base text, runs ngspice, and evaluates a metric on the raw output.
"""

from __future__ import annotations

import itertools
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from benchgate.kicad.cli_export import export_spice_netlist
from benchgate.kicad.project import KiCadProject
from benchgate.sim.analysis import (
    _compute_metric,
    _resolve_signal,
    _window_slice,
    parse_ngspice_raw,
)
from benchgate.sim.netlist import prepare_netlist
from benchgate.sim.runner import run_ngspice


@dataclass
class SweepPoint:
    overrides: dict[str, str]
    metric: float
    passed: bool | None
    ngspice_ok: bool


@dataclass
class SweepReport:
    metric: str
    profile: str
    points: list[dict]
    ran_at: str
    report_path: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def parse_axis(spec: str) -> tuple[str, list[str]]:
    name, sep, vals = spec.partition("=")
    if not sep:
        raise ValueError(f"sweep axis must be NAME=v1,v2,... (got {spec!r})")
    values = [v.strip() for v in vals.split(",") if v.strip()]
    if not values:
        raise ValueError(f"sweep axis {name!r} has no values")
    return name.strip(), values


def parse_metric(spec: str) -> tuple[str, str, str | None]:
    """'signal[:metric[:window_after]]' -> (signal, metric, window)."""
    parts = spec.split(":")
    signal = parts[0].strip()
    metric = parts[1].strip() if len(parts) > 1 and parts[1].strip() else "min"
    window = parts[2].strip() if len(parts) > 2 and parts[2].strip() else None
    return signal, metric, window


def apply_param(text: str, name: str, value: str) -> str:
    pat = re.compile(rf"^\.param\s+{re.escape(name)}\s*=.*$", re.MULTILINE | re.IGNORECASE)
    replacement = f".param {name}={value}"
    if pat.search(text):
        return pat.sub(replacement, text, count=1)
    # Not declared yet: inject after the .title line (or at the very top).
    if re.search(r"^\.title\b.*$", text, flags=re.MULTILINE | re.IGNORECASE):
        return re.sub(r"^(\.title\b.*)$", r"\1\n" + replacement, text, count=1, flags=re.MULTILINE | re.IGNORECASE)
    return replacement + "\n" + text


def apply_set(text: str, ref: str, value: str) -> str:
    pat = re.compile(rf"^({re.escape(ref)}\s+.*\s)(\S+)\s*$", re.MULTILINE)
    new_text, n = pat.subn(lambda m: f"{m.group(1)}{value}", text, count=1)
    if n == 0:
        raise ValueError(f"sweep --set: element {ref!r} not found in netlist")
    return new_text


def evaluate_metric(raw_path: Path, signal: str, metric: str, window: str | None) -> float:
    time, signals = parse_ngspice_raw(raw_path)
    series = _resolve_signal(signals, signal)
    if series is None:
        return float("nan")
    mask = _window_slice(time, window)
    return _compute_metric(series[mask], metric)


def run_sweep(
    design_dir: Path,
    manifest_path: Path,
    output_dir: Path,
    *,
    sim_profile_path: Path,
    profile: str,
    metric_spec: str,
    params: dict[str, list[str]] | None = None,
    sets: dict[str, list[str]] | None = None,
    pass_gte: float | None = None,
    pass_lte: float | None = None,
) -> SweepReport:
    params = params or {}
    sets = sets or {}
    output_dir.mkdir(parents=True, exist_ok=True)

    project = KiCadProject.load(design_dir)
    exported = output_dir / "exported.net"
    base_prepared = output_dir / "sweep_base.cir"
    export_spice_netlist(project.schematic, exported)
    prepare_netlist(
        exported,
        manifest_path,
        base_prepared,
        sim_profile_path=sim_profile_path,
        profile=profile,
    )
    base_text = base_prepared.read_text(encoding="utf-8")

    signal, metric, window = parse_metric(metric_spec)

    # Axis order: params first, then sets (stable, deterministic grid).
    axis_names = [("param", n) for n in params] + [("set", n) for n in sets]
    axis_values = [params[n] for n in params] + [sets[n] for n in sets]

    points: list[SweepPoint] = []
    combos = list(itertools.product(*axis_values)) if axis_values else [()]
    for idx, combo in enumerate(combos):
        text = base_text
        overrides: dict[str, str] = {}
        for (kind, name), value in zip(axis_names, combo):
            overrides[name] = value
            text = apply_param(text, name, value) if kind == "param" else apply_set(text, name, value)

        run_dir = output_dir / f"pt{idx:03d}"
        run_dir.mkdir(parents=True, exist_ok=True)
        cir = run_dir / "point.cir"
        cir.write_text(text, encoding="utf-8")
        result = run_ngspice(cir, work_dir=run_dir)

        value = float("nan")
        if result.raw_output:
            try:
                value = evaluate_metric(result.raw_output, signal, metric, window)
            except Exception:
                value = float("nan")

        passed: bool | None = None
        if pass_gte is not None or pass_lte is not None:
            passed = True
            if pass_gte is not None and not (value >= pass_gte):
                passed = False
            if pass_lte is not None and not (value <= pass_lte):
                passed = False

        points.append(
            SweepPoint(
                overrides=overrides,
                metric=value,
                passed=passed,
                ngspice_ok=result.success,
            )
        )

    report = SweepReport(
        metric=metric_spec,
        profile=profile,
        points=[asdict(p) for p in points],
        ran_at=datetime.now(timezone.utc).isoformat(),
    )
    report_path = output_dir / "sweep_report.json"
    report_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    report.report_path = str(report_path)
    return report
