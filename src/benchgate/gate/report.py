"""Bench vs simulation quality reports."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from numbers import Real
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
    source: str | None = None
    range_warnings: list[str] = field(default_factory=list)
    spec_status: str = "n/a"  # pass | fail | n/a
    spec_failures: list[str] = field(default_factory=list)


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


def _interval_bounds(dim: str, bounds: object, *, label: str) -> tuple[object, object] | str:
    """Parse ``[min, max]`` interval bounds, or return an error message."""
    if isinstance(bounds, (list, tuple)) and len(bounds) == 2:
        return bounds[0], bounds[1]
    return f"{dim}: invalid {label} bounds (expected [min, max])"


def check_valid_range(
    valid_range: dict,
    operating_point: dict | None,
) -> list[str]:
    """Warn when the operating point falls outside a model's declared valid_range.

    Each ``valid_range`` dimension is a closed interval ``[min, max]`` (use
    ``null``/``None`` or ``.inf`` for an open bound). A dimension with no
    operating-point value yields an "unverifiable" warning (never silent).
    """
    warnings: list[str] = []
    op = operating_point or {}
    for dim, bounds in valid_range.items():
        parsed = _interval_bounds(dim, bounds, label="valid_range")
        if isinstance(parsed, str):
            warnings.append(parsed)
            continue
        lo, hi = parsed
        val = op.get(dim)
        if not isinstance(val, Real):
            warnings.append(f"{dim}: cannot verify valid_range (no operating-point value)")
            continue
        if isinstance(lo, Real) and val < lo:
            warnings.append(f"{dim}={val:g} below valid_range min {lo:g}")
        if isinstance(hi, Real) and val > hi:
            warnings.append(f"{dim}={val:g} above valid_range max {hi:g}")
    return warnings


def check_spec(spec: dict, metrics: dict | None) -> list[str]:
    """Return spec failures: achieved ``metrics`` vs required ``spec`` intervals.

    Each ``spec`` dimension is a closed interval ``[min, max]`` (open bound via
    ``null``/``.inf``). A spec dimension with no achieved metric is reported as
    "not characterized" (counts as a failure — can't prove it's met).
    Malformed bounds count as failures (never silent pass).
    """
    failures: list[str] = []
    m = metrics or {}
    for dim, bounds in spec.items():
        parsed = _interval_bounds(dim, bounds, label="spec")
        if isinstance(parsed, str):
            failures.append(parsed)
            continue
        lo, hi = parsed
        val = m.get(dim)
        if not isinstance(val, Real):
            failures.append(f"{dim}: not characterized (no metric for spec)")
            continue
        if isinstance(lo, Real) and val < lo:
            failures.append(f"{dim}={val:g} below spec min {lo:g}")
        if isinstance(hi, Real) and val > hi:
            failures.append(f"{dim}={val:g} above spec max {hi:g}")
    return failures


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
    operating_point: dict | None = None,
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

    range_warnings: list[str] = []
    if entry.provenance and entry.provenance.valid_range:
        range_warnings = check_valid_range(entry.provenance.valid_range, operating_point)

    spec_status = "n/a"
    spec_failures: list[str] = []
    if entry.spec:
        metrics = entry.provenance.metrics if entry.provenance else None
        spec_failures = check_spec(entry.spec, metrics)
        spec_status = "fail" if spec_failures else "pass"

    return GateEntry(
        reference=ref,
        kicad_key=entry.kicad_key,
        has_bench=bench_wf is not None,
        has_sim=sim_waveform is not None,
        rmse=rmse,
        notes=notes,
        source=entry.provenance.source.value if entry.provenance else None,
        range_warnings=range_warnings,
        spec_status=spec_status,
        spec_failures=spec_failures,
    )


def build_gate_report(
    manifest: MappingManifest,
    *,
    captured_dir: Path,
    sim_raw_path: Path | None = None,
    operating_point: dict | None = None,
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
            evaluate_entry(
                item,
                captured_dir=captured_dir,
                sim_waveform=sim_waveform,
                operating_point=operating_point,
            )
        )

    with_bench = sum(1 for e in entries if e.has_bench)
    with_sim = sum(1 for e in entries if e.has_sim)
    compared = sum(1 for e in entries if e.rmse is not None)
    range_warnings = sum(1 for e in entries if e.range_warnings)
    spec_failures = sum(1 for e in entries if e.spec_status == "fail")

    return GateReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        entries=entries,
        summary={
            "total": len(entries),
            "with_bench": with_bench,
            "with_sim": with_sim,
            "compared": compared,
            "range_warnings": range_warnings,
            "spec_failures": spec_failures,
        },
    )


def write_gate_report(
    manifest_path: Path,
    output_path: Path,
    *,
    captured_dir: Path,
    sim_raw_path: Path | None = None,
    operating_point: dict | None = None,
) -> GateReport:
    manifest = load_manifest(manifest_path)
    report = build_gate_report(
        manifest,
        captured_dir=captured_dir,
        sim_raw_path=sim_raw_path,
        operating_point=operating_point,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    return report
