"""Bench vs simulation quality reports."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from benchgate.io.manifest import load_manifest
from benchgate.instruments.types import Waveform
from benchgate.lab.analyze import compare_waveforms
from benchgate.lab.store import LabDataStore
from benchgate.schemas import ComponentMapping, MappingManifest, MeasuredParams


@dataclass
class GateEntry:
    reference: str
    kicad_key: str
    has_bench: bool
    has_sim: bool
    rmse: float | None = None
    notes: str = ""


@dataclass
class GateReport:
    generated_at: str
    entries: list[GateEntry]
    summary: dict[str, int]

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "summary": self.summary,
            "entries": [asdict(e) for e in self.entries],
        }


def _load_sim_csv(path: Path) -> tuple[np.ndarray, np.ndarray] | None:
    if not path.exists():
        return None
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    if len(lines) < 2:
        return None
    delim = "," if "," in lines[1] else None
    data = np.loadtxt(path, skiprows=1, delimiter=delim)
    if data.ndim != 2 or data.shape[1] < 2:
        return None
    return data[:, 0], data[:, 1]


def load_bench_waveform(
    measured: MeasuredParams,
    *,
    captured_dir: Path,
    channel: str = "scope_ch1",
) -> Waveform | None:
    """Load bench waveform from the session store (NPZ)."""
    try:
        return LabDataStore(captured_dir).load_waveform(measured.session_id, channel)
    except (FileNotFoundError, KeyError, OSError):
        return None


def _waveform_from_arrays(time_s: np.ndarray, voltage_v: np.ndarray) -> Waveform:
    return Waveform(
        time_s=np.asarray(time_s, dtype=float),
        voltage_v=np.asarray(voltage_v, dtype=float),
        channel=1,
        timestamp=datetime.now(timezone.utc),
    )


def evaluate_entry(
    entry: ComponentMapping,
    *,
    captured_dir: Path,
    sim_waveform: Waveform | None,
) -> GateEntry:
    ref = entry.reference or entry.kicad_key
    bench_wf = load_bench_waveform(entry.measured, captured_dir=captured_dir) if entry.measured else None

    rmse = None
    notes = ""
    if bench_wf and sim_waveform:
        rmse = compare_waveforms(bench_wf, sim_waveform).rmse
    elif bench_wf:
        notes = "bench only"
    elif sim_waveform:
        notes = "sim only"

    return GateEntry(
        reference=ref,
        kicad_key=entry.kicad_key,
        has_bench=bench_wf is not None,
        has_sim=sim_waveform is not None,
        rmse=rmse,
        notes=notes,
    )


def build_gate_report(
    manifest: MappingManifest,
    *,
    captured_dir: Path,
    sim_raw_path: Path | None = None,
) -> GateReport:
    sim_waveform: Waveform | None = None
    if sim_raw_path and sim_raw_path.exists():
        loaded = _load_sim_csv(sim_raw_path)
        if loaded:
            sim_waveform = _waveform_from_arrays(*loaded)

    entries: list[GateEntry] = []
    for item in manifest.entries:
        if not item.measured and item.spice_kind.value == "passive":
            continue
        entries.append(
            evaluate_entry(item, captured_dir=captured_dir, sim_waveform=sim_waveform)
        )

    with_bench = sum(1 for e in entries if e.has_bench)
    with_sim = sum(1 for e in entries if e.has_sim)
    compared = sum(1 for e in entries if e.rmse is not None)

    return GateReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        entries=entries,
        summary={
            "total": len(entries),
            "with_bench": with_bench,
            "with_sim": with_sim,
            "compared": compared,
        },
    )


def write_gate_report(
    manifest_path: Path,
    output_path: Path,
    *,
    captured_dir: Path,
    sim_raw_path: Path | None = None,
) -> GateReport:
    manifest = load_manifest(manifest_path)
    report = build_gate_report(
        manifest,
        captured_dir=captured_dir,
        sim_raw_path=sim_raw_path,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    return report
