"""File change detection and one-shot pipeline trigger."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from benchgate.gate.report import load_sim_report_context, write_gate_report
from benchgate.mapping.engine import mapping_status, sync_project
from benchgate.paths import benchgate_paths
from benchgate.pipeline.local_blocks import sync_local_blocks
from benchgate.sim.pipeline import run_project_sim
from benchgate.watch.auto_capture import run_auto_capture


WATCH_GLOBS = ("*.kicad_sch", "*.kicad_pro", "*.kicad_pcb")
PIPELINE_FILES = ("models/blocks.yaml",)
BLOCK_FILE_SUFFIXES = (".net", ".cir", ".asc", ".metrics.json")


@dataclass
class WatchState:
    files: dict[str, str]

    @classmethod
    def load(cls, path: Path) -> WatchState:
        if not path.exists():
            return cls(files={})
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(files=data.get("files", {}))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"files": self.files}, indent=2), encoding="utf-8")


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def design_files(design_dir: Path) -> list[Path]:
    files: list[Path] = []
    for pattern in WATCH_GLOBS:
        files.extend(design_dir.rglob(pattern))
    return sorted(files)


def pipeline_files(design_dir: Path) -> list[Path]:
    """Local-block sources watched for agent automation (blocks.yaml + blocks/*)."""
    files: list[Path] = []
    for rel in PIPELINE_FILES:
        path = design_dir / rel
        if path.is_file():
            files.append(path)
    blocks_dir = design_dir / "models" / "blocks"
    if blocks_dir.is_dir():
        for path in blocks_dir.rglob("*"):
            if path.is_file() and path.suffix.lower() in BLOCK_FILE_SUFFIXES:
                files.append(path)
    return sorted(files)


def watched_files(design_dir: Path) -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []
    for path in design_files(design_dir) + pipeline_files(design_dir):
        if path not in seen:
            seen.add(path)
            out.append(path)
    return out


def detect_changes(design_dir: Path, state_path: Path) -> list[Path]:
    state = WatchState.load(state_path)
    changed: list[Path] = []
    current: dict[str, str] = {}

    for path in watched_files(design_dir):
        key = str(path.relative_to(design_dir))
        digest = _file_hash(path)
        current[key] = digest
        if state.files.get(key) != digest:
            changed.append(path)

    state.files = current
    state.save(state_path)
    return changed


def watch_once(
    design_dir: Path,
    *,
    manifest_path: Path,
    models_dir: Path,
    reports_dir: Path,
    state_path: Path,
    sim_profile_path: Path | None = None,
    profile: str = "default",
    subckt_dir: Path,
    global_models_dir: Path,
    blocks_yaml: Path | None = None,
    tmp_dir: Path | None = None,
    run_pipeline: bool = True,
    run_sim: bool = True,
    run_gate: bool = True,
    run_auto_capture: bool = True,
    auto_capture_dry_run: bool = False,
) -> dict:
    changed = detect_changes(design_dir, state_path)
    operating_point: dict = {}
    result: dict = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "changed_files": [str(p) for p in changed],
    }

    if run_pipeline:
        pipeline = sync_local_blocks(
            models_dir=models_dir,
            manifest_path=manifest_path,
            subckt_dir=subckt_dir,
            global_models_dir=global_models_dir,
            blocks_yaml=blocks_yaml,
            tmp_dir=tmp_dir,
        )
        operating_point = pipeline.get("operating_point") or {}
        result["pipeline"] = pipeline

    manifest = sync_project(
        design_dir,
        manifest_path,
        models_dir,
        subckt_dir=subckt_dir,
        global_models_dir=global_models_dir,
    )
    status = mapping_status(manifest)
    result["mapping_status"] = status

    if run_auto_capture and status.get("pending"):
        paths = benchgate_paths(design_dir, manifest=manifest_path, reports=reports_dir)
        result["auto_capture"] = run_auto_capture(
            design_dir,
            manifest,
            models_dir=models_dir,
            lab_config=paths.lab_config,
            instruments_config=paths.instruments,
            dry_run=auto_capture_dry_run,
        )

    sim_dir = reports_dir / "sim"
    stress_sweep_path: Path | None = None
    if run_sim and not status.get("unmapped"):
        report, _ = run_project_sim(
            design_dir,
            manifest_path,
            sim_dir,
            sim_profile_path=sim_profile_path,
            profile=profile,
        )
        result["sim"] = report.to_dict()

        if sim_profile_path:
            from benchgate.sim.profile import load_profile_block
            from benchgate.sim.stress_sweep import run_stress_sweep

            block = load_profile_block(sim_profile_path, profile)
            if block.get("stress_sweep") and block.get("stress"):
                sweep_dir = reports_dir / "stress_sweep"
                sweep_report = run_stress_sweep(
                    design_dir,
                    manifest_path,
                    sweep_dir,
                    sim_profile_path=sim_profile_path,
                    profile=profile,
                )
                result["stress_sweep"] = sweep_report.to_dict()
                stress_sweep_path = Path(sweep_report.report_path) if sweep_report.report_path else None

    if run_gate:
        gate_path = reports_dir / "gate_report.json"
        sim_report = sim_dir / "sim_report.json"
        op = operating_point or None
        if sim_report.exists():
            inferred_op, _ = load_sim_report_context(sim_report)
            if inferred_op and not op:
                op = inferred_op
        gate = write_gate_report(
            manifest_path,
            gate_path,
            captured_dir=models_dir / "captured",
            sim_raw_path=sim_dir / "sim_waveform.csv" if sim_dir.exists() else None,
            operating_point=op,
            sim_report_path=sim_report if sim_report.exists() else None,
            stress_sweep_path=stress_sweep_path,
        )
        result["gate"] = gate.to_dict()
        result["operating_point"] = op

    return result
