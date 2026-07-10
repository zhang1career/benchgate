"""End-to-end simulation pipeline."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from benchgate.io.manifest import load_manifest
from benchgate.kicad.cli_export import export_spice_netlist
from benchgate.kicad.project import KiCadProject
from benchgate.sim.analysis import (
    SimCheckReport,
    analyze_raw_file,
    analyze_raw_stress,
    load_profile_checks,
)
from benchgate.sim.netlist import prepare_netlist
from benchgate.sim.preflight import run_preflight, write_preflight_report
from benchgate.sim.profile import (
    infer_operating_point,
    load_profile_block,
    load_profile_stress,
)
from benchgate.sim.runner import SimResult, run_ngspice


@dataclass
class SimRunReport:
    success: bool
    project: str
    netlist: str
    prepared_netlist: str
    log_path: str | None
    raw_output: str | None
    ran_at: str
    ngspice_ok: bool = True
    preflight: dict | None = None
    checks: dict | None = None
    stress: dict | None = None
    operating_point: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def run_project_sim(
    design_dir: Path,
    manifest_path: Path,
    output_dir: Path,
    *,
    sim_profile_path: Path | None = None,
    profile: str = "default",
    fail_on_preflight_error: bool = False,
) -> tuple[SimRunReport, SimResult]:
    project = KiCadProject.load(design_dir)
    manifest = load_manifest(manifest_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    exported = output_dir / "exported.net"
    prepared = output_dir / "prepared.cir"

    export_spice_netlist(project.schematic, exported)
    exported_text = exported.read_text(encoding="utf-8", errors="replace")
    prepare_netlist(
        exported,
        manifest_path,
        prepared,
        sim_profile_path=sim_profile_path,
        profile=profile,
    )
    prepared_text = prepared.read_text(encoding="utf-8", errors="replace")

    preflight = run_preflight(exported_text, manifest, prepared_text=prepared_text)
    write_preflight_report(preflight, output_dir / "preflight.json")

    if fail_on_preflight_error and not preflight.passed:
        report = SimRunReport(
            success=False,
            project=str(project.project_file),
            netlist=str(exported),
            prepared_netlist=str(prepared),
            log_path=None,
            raw_output=None,
            ran_at=datetime.now(timezone.utc).isoformat(),
            ngspice_ok=False,
            preflight=preflight.to_dict(),
        )
        report_path = output_dir / "sim_report.json"
        report_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        return report, SimResult(
            success=False,
            stdout="",
            stderr="preflight failed",
            log_path=None,
            raw_output=None,
        )

    result = run_ngspice(prepared, work_dir=output_dir)

    profile_block = load_profile_block(sim_profile_path, profile) if sim_profile_path else {}
    check_report: SimCheckReport | None = None
    stress_report = None

    if sim_profile_path and result.raw_output:
        checks = load_profile_checks(sim_profile_path, profile)
        if checks:
            check_report = analyze_raw_file(result.raw_output, checks)

        stress_block = load_profile_stress(sim_profile_path, profile)
        if stress_block:
            stress_report = analyze_raw_stress(result.raw_output, stress_block)

    check_values: dict[str, float] = {}
    if check_report:
        for item in check_report.checks:
            key = f"{item.signal}:{item.metric}"
            check_values[key] = item.value
            if item.alias:
                check_values[item.alias] = item.value

    operating_point = infer_operating_point(profile_block, check_values=check_values)
    op_path = output_dir / "operating_point.json"
    if operating_point:
        op_path.write_text(json.dumps(operating_point, indent=2), encoding="utf-8")

    ngspice_ok = result.success
    checks_ok = check_report.passed if check_report else True
    stress_ok = stress_report.passed if stress_report else True
    overall_ok = ngspice_ok and checks_ok and stress_ok

    report = SimRunReport(
        success=overall_ok,
        project=str(project.project_file),
        netlist=str(exported),
        prepared_netlist=str(prepared),
        log_path=str(result.log_path) if result.log_path else None,
        raw_output=str(result.raw_output) if result.raw_output else None,
        ran_at=datetime.now(timezone.utc).isoformat(),
        ngspice_ok=ngspice_ok,
        preflight=preflight.to_dict(),
        checks=check_report.to_dict() if check_report else None,
        stress=stress_report.to_dict() if stress_report else None,
        operating_point=operating_point or None,
    )
    report_path = output_dir / "sim_report.json"
    report_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")

    if result.raw_output and sim_profile_path:
        from benchgate.bench_compare import export_sim_waveforms, load_merged_compare_specs

        compare_specs = load_merged_compare_specs(
            manifest,
            sim_profile_path=sim_profile_path,
            profile=profile,
        )
        if not compare_specs:
            checks = load_profile_checks(sim_profile_path, profile)
            if checks:
                first = checks[0]
                signal = str(first.get("expr") or first.get("signal") or "")
                if signal:
                    from benchgate.bench_compare import BenchCompareSpec

                    compare_specs = [
                        BenchCompareSpec(
                            id="primary",
                            signal=signal,
                            sim_metric=str(first.get("metric", "avg")),
                        )
                    ]
        if compare_specs:
            export_sim_waveforms(result.raw_output, compare_specs, output_dir)

    return report, result


def run_preflight_only(
    design_dir: Path,
    manifest_path: Path,
    output_dir: Path,
    *,
    sim_profile_path: Path | None = None,
    profile: str = "default",
) -> dict:
    """Export + prepare netlist and return preflight report without running ngspice."""
    project = KiCadProject.load(design_dir)
    manifest = load_manifest(manifest_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    exported = output_dir / "exported.net"
    prepared = output_dir / "prepared.cir"

    export_spice_netlist(project.schematic, exported)
    exported_text = exported.read_text(encoding="utf-8", errors="replace")
    prepare_netlist(
        exported,
        manifest_path,
        prepared,
        sim_profile_path=sim_profile_path,
        profile=profile,
    )
    prepared_text = prepared.read_text(encoding="utf-8", errors="replace")
    preflight = run_preflight(exported_text, manifest, prepared_text=prepared_text)
    write_preflight_report(preflight, output_dir / "preflight.json")
    return preflight.to_dict()
