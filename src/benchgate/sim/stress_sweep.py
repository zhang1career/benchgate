"""Worst-case stress sweep over profile-defined parameter grids."""

from __future__ import annotations

import itertools
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchgate.kicad.cli_export import export_spice_netlist
from benchgate.kicad.project import KiCadProject
from benchgate.sim.analysis import parse_ngspice_raw
from benchgate.sim.netlist import prepare_netlist
from benchgate.sim.profile import load_profile_block, load_profile_stress
from benchgate.sim.runner import run_ngspice
from benchgate.sim.stress import StressReport, evaluate_stress, merge_worst_stress
from benchgate.sim.sweep import apply_param, apply_set, parse_axis


@dataclass
class StressSweepPoint:
    overrides: dict[str, str]
    stress_passed: bool
    ngspice_ok: bool
    peak: dict[str, float]


@dataclass
class StressSweepReport:
    profile: str
    worst: dict
    points: list[dict]
    ran_at: str
    report_path: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _parse_sweep_axes(block: dict[str, Any]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    params: dict[str, list[str]] = {}
    sets: dict[str, list[str]] = {}
    for name, raw in (block.get("params") or block.get("param") or {}).items():
        if isinstance(raw, list):
            params[str(name)] = [str(v) for v in raw]
        else:
            _, values = parse_axis(f"{name}={raw}")
            params[str(name)] = values
    for name, raw in (block.get("sets") or block.get("set") or {}).items():
        if isinstance(raw, list):
            sets[str(name)] = [str(v) for v in raw]
        else:
            _, values = parse_axis(f"{name}={raw}")
            sets[str(name)] = values
    return params, sets


def run_stress_sweep(
    design_dir: Path,
    manifest_path: Path,
    output_dir: Path,
    *,
    sim_profile_path: Path,
    profile: str,
    limits_catalog_path: Path | None = None,
) -> StressSweepReport:
    block = load_profile_block(sim_profile_path, profile)
    sweep_cfg = block.get("stress_sweep") or {}
    stress_block = load_profile_stress(sim_profile_path, profile)
    if not stress_block:
        raise ValueError(f"profile {profile!r} has no stress block for stress_sweep")

    params, sets = _parse_sweep_axes(sweep_cfg)
    output_dir.mkdir(parents=True, exist_ok=True)

    project = KiCadProject.load(design_dir)
    exported = output_dir / "exported.net"
    base_prepared = output_dir / "stress_sweep_base.cir"
    export_spice_netlist(project.schematic, exported)
    prepare_netlist(
        exported,
        manifest_path,
        base_prepared,
        sim_profile_path=sim_profile_path,
        profile=profile,
    )
    base_text = base_prepared.read_text(encoding="utf-8")

    axis_names = [("param", n) for n in params] + [("set", n) for n in sets]
    axis_values = [params[n] for n in params] + [sets[n] for n in sets]
    combos = list(itertools.product(*axis_values)) if axis_values else [()]

    point_reports: list[StressReport] = []
    points: list[StressSweepPoint] = []

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

        stress_report: StressReport | None = None
        peak: dict[str, float] = {}
        if result.raw_output and result.raw_output.exists():
            time, signals = parse_ngspice_raw(result.raw_output)
            stress_report = evaluate_stress(
                time,
                signals,
                stress_block,
                limits_catalog_path=limits_catalog_path,
            )
            if stress_report:
                point_reports.append(stress_report)
                for item in stress_report.results:
                    peak[f"{item.reference}.{item.quantity}"] = item.value

        points.append(
            StressSweepPoint(
                overrides=overrides,
                stress_passed=stress_report.passed if stress_report else False,
                ngspice_ok=result.success,
                peak=peak,
            )
        )

    worst = merge_worst_stress(point_reports)
    for item in worst.results:
        item.worst_case = None  # filled below from winning point
    # Annotate which sweep point produced each worst value
    for item in worst.results:
        best_override: dict[str, str] | None = None
        best_val = float("-inf")
        for pt, rep in zip(points, point_reports):
            for r in rep.results:
                if r.reference == item.reference and r.quantity == item.quantity:
                    if r.value >= best_val:
                        best_val = r.value
                        best_override = pt.overrides
        item.worst_case = best_override

    report = StressSweepReport(
        profile=profile,
        worst=worst.to_dict(),
        points=[asdict(p) for p in points],
        ran_at=datetime.now(timezone.utc).isoformat(),
    )
    report_path = output_dir / "stress_sweep_report.json"
    report_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    report.report_path = str(report_path)
    return report
