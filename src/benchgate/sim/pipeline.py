"""End-to-end simulation pipeline."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from benchgate.kicad.cli_export import export_spice_netlist
from benchgate.kicad.project import KiCadProject
from benchgate.sim.netlist import prepare_netlist
from benchgate.sim.analysis import SimCheckReport, analyze_raw_file, load_profile_checks
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
    checks: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def run_project_sim(
    design_dir: Path,
    manifest_path: Path,
    output_dir: Path,
    *,
    sim_profile_path: Path | None = None,
    profile: str = "default",
) -> tuple[SimRunReport, SimResult]:
    project = KiCadProject.load(design_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    exported = output_dir / "exported.net"
    prepared = output_dir / "prepared.cir"

    export_spice_netlist(project.schematic, exported)
    prepare_netlist(
        exported,
        manifest_path,
        prepared,
        sim_profile_path=sim_profile_path,
        profile=profile,
    )
    result = run_ngspice(prepared, work_dir=output_dir)

    check_report: SimCheckReport | None = None
    if sim_profile_path:
        checks = load_profile_checks(sim_profile_path, profile)
        if checks and result.raw_output:
            check_report = analyze_raw_file(result.raw_output, checks)

    ngspice_ok = result.success
    checks_ok = check_report.passed if check_report else True
    overall_ok = ngspice_ok and checks_ok

    report = SimRunReport(
        success=overall_ok,
        project=str(project.project_file),
        netlist=str(exported),
        prepared_netlist=str(prepared),
        log_path=str(result.log_path) if result.log_path else None,
        raw_output=str(result.raw_output) if result.raw_output else None,
        ran_at=datetime.now(timezone.utc).isoformat(),
        ngspice_ok=ngspice_ok,
        checks=check_report.to_dict() if check_report else None,
    )
    report_path = output_dir / "sim_report.json"
    report_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    return report, result
