"""File change detection and one-shot pipeline trigger."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from benchgate.gate.report import write_gate_report
from benchgate.mapping.engine import mapping_status, sync_project
from benchgate.sim.pipeline import run_project_sim


WATCH_GLOBS = ("*.kicad_sch", "*.kicad_pro", "*.kicad_pcb")


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


def detect_changes(design_dir: Path, state_path: Path) -> list[Path]:
    state = WatchState.load(state_path)
    changed: list[Path] = []
    current: dict[str, str] = {}

    for path in design_files(design_dir):
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
    subckt_dir: Path,
    global_models_dir: Path,
    run_sim: bool = True,
) -> dict:
    changed = detect_changes(design_dir, state_path)
    manifest = sync_project(
        design_dir,
        manifest_path,
        models_dir,
        subckt_dir=subckt_dir,
        global_models_dir=global_models_dir,
    )
    status = mapping_status(manifest)
    result: dict = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "changed_files": [str(p) for p in changed],
        "mapping_status": status,
    }

    if run_sim and status.get("unmapped") == []:
        sim_dir = reports_dir / "sim"
        report, _ = run_project_sim(
            design_dir,
            manifest_path,
            sim_dir,
            sim_profile_path=sim_profile_path,
        )
        result["sim"] = report.to_dict()
        gate_path = reports_dir / "gate_report.json"
        gate = write_gate_report(
            manifest_path,
            gate_path,
            captured_dir=models_dir / "captured",
            sim_raw_path=sim_dir / "sim_waveform.csv",
        )
        result["gate"] = gate.to_dict()
    return result
