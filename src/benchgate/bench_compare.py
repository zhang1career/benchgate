"""Bench ↔ simulation comparison configuration and waveform export."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from benchgate.instruments.types import Waveform
from benchgate.schemas import ComponentMapping, MappingManifest
from benchgate.sim.analysis import _compute_metric, _parse_window, _resolve_series, parse_ngspice_raw
from benchgate.sim.profile import load_profile_bench_compare


@dataclass
class BenchCompareSpec:
    """One probe/channel pairing for bench vs sim comparison."""

    id: str
    signal: str
    bench_channel: str = "scope_ch1"
    component_ref: str | None = None
    sim_metric: str | None = None
    bench_metric: str | None = None
    window: list[str | float] | None = None
    tolerance_pct: float | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BenchCompareSpec:
        return cls(
            id=str(data["id"]),
            signal=str(data.get("signal") or data.get("expr") or ""),
            bench_channel=str(data.get("bench_channel", "scope_ch1")),
            component_ref=data.get("component_ref"),
            sim_metric=data.get("sim_metric"),
            bench_metric=data.get("bench_metric"),
            window=data.get("window"),
            tolerance_pct=data.get("tolerance_pct"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


DEFAULT_RMSE_WARN_V = 0.1
DEFAULT_RMSE_FAIL_V = 0.2
DEFAULT_CORR_WARN = 0.9
DEFAULT_CORR_FAIL = 0.8

WATCH_TRIGGER_TAGS = frozenset({"anomaly", "baseline", "characterize", "compare"})


def merge_bench_compare_specs(
    profile_specs: list[dict[str, Any] | BenchCompareSpec],
    manifest: MappingManifest,
) -> list[BenchCompareSpec]:
    """Profile defaults plus per-entry manifest overrides (by component_ref)."""
    parsed_profile = [
        BenchCompareSpec.from_dict(s) if isinstance(s, dict) else s for s in profile_specs
    ]
    by_ref: dict[str | None, list[BenchCompareSpec]] = {}
    for spec in parsed_profile:
        by_ref.setdefault(spec.component_ref, []).append(spec)

    for entry in manifest.entries:
        overrides = getattr(entry, "bench_compare", None) or []
        if not overrides:
            continue
        ref = entry.reference
        parsed = [BenchCompareSpec.from_dict(o) if isinstance(o, dict) else o for o in overrides]
        for spec in parsed:
            if spec.component_ref is None:
                spec.component_ref = ref
        by_ref[ref] = parsed

    out: list[BenchCompareSpec] = []
    seen_ids: set[str] = set()
    for specs in by_ref.values():
        for spec in specs:
            if spec.id in seen_ids:
                continue
            seen_ids.add(spec.id)
            out.append(spec)
    return out


def load_merged_compare_specs(
    manifest: MappingManifest,
    *,
    sim_profile_path: Path | None,
    profile: str = "default",
) -> list[BenchCompareSpec]:
    profile_raw: list[dict] = []
    if sim_profile_path and sim_profile_path.is_file():
        profile_raw = load_profile_bench_compare(sim_profile_path, profile)
    return merge_bench_compare_specs(profile_raw, manifest)


def _window_mask(time_s: np.ndarray, window: list[str | float] | None) -> np.ndarray:
    if not window or len(window) < 2:
        return np.ones(time_s.shape, dtype=bool)
    lo = _parse_window(window[0])
    hi = _parse_window(window[1])
    return (time_s >= lo) & (time_s <= hi)


def export_sim_waveforms(
    raw_path: Path,
    specs: list[BenchCompareSpec],
    output_dir: Path,
) -> dict[str, Any]:
    """Extract probe waveforms from ngspice raw → CSV + manifest JSON."""
    output_dir.mkdir(parents=True, exist_ok=True)
    if not raw_path.is_file() or not specs:
        return {"probes": [], "primary": None}

    time_s, signals = parse_ngspice_raw(raw_path)
    probes: list[dict[str, Any]] = []
    primary_id: str | None = None

    for spec in specs:
        series, resolved = _resolve_series(signals, {"signal": spec.signal})
        if series is None:
            probes.append(
                {
                    "id": spec.id,
                    "signal": spec.signal,
                    "resolved": resolved,
                    "csv": None,
                    "error": "signal not found in sim.raw",
                }
            )
            continue

        mask = _window_mask(time_s, spec.window)
        t = time_s[mask]
        v = series[mask]
        if t.size == 0:
            probes.append(
                {
                    "id": spec.id,
                    "signal": spec.signal,
                    "csv": None,
                    "error": "empty window",
                }
            )
            continue

        csv_path = output_dir / f"sim_waveform_{spec.id}.csv"
        np.savetxt(csv_path, np.column_stack([t, v]), header="t,v", comments="", delimiter=",")

        scalar: float | None = None
        if spec.sim_metric:
            scalar = _compute_metric(v, spec.sim_metric)

        probe = {
            "id": spec.id,
            "signal": spec.signal,
            "resolved": resolved,
            "component_ref": spec.component_ref,
            "bench_channel": spec.bench_channel,
            "csv": str(csv_path.name),
            "samples": int(t.size),
            "sim_scalar": scalar,
            "sim_metric": spec.sim_metric,
            "bench_metric": spec.bench_metric,
        }
        probes.append(probe)
        if primary_id is None:
            primary_id = spec.id

    manifest = {"probes": probes, "primary": primary_id}
    manifest_path = output_dir / "bench_compare.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    if primary_id:
        primary_csv = output_dir / f"sim_waveform_{primary_id}.csv"
        legacy = output_dir / "sim_waveform.csv"
        if primary_csv.is_file():
            legacy.write_text(primary_csv.read_text(encoding="utf-8"), encoding="utf-8")

    return manifest


def load_bench_compare_manifest(sim_dir: Path) -> dict[str, Any] | None:
    path = sim_dir / "bench_compare.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def load_sim_waveform_csv(path: Path) -> Waveform | None:
    if not path.is_file():
        return None
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    if len(lines) < 2:
        return None
    delim = "," if "," in lines[1] else None
    data = np.loadtxt(path, skiprows=1, delimiter=delim)
    if data.ndim != 2 or data.shape[1] < 2:
        return None
    from datetime import datetime, timezone

    return Waveform(
        time_s=np.asarray(data[:, 0], dtype=float),
        voltage_v=np.asarray(data[:, 1], dtype=float),
        channel=1,
        timestamp=datetime.now(timezone.utc),
    )


def load_sim_waveforms(sim_dir: Path, manifest: dict[str, Any] | None = None) -> dict[str, Waveform]:
    """Load per-probe sim waveforms keyed by compare id."""
    bm = manifest or load_bench_compare_manifest(sim_dir)
    out: dict[str, Waveform] = {}
    if bm:
        for probe in bm.get("probes") or []:
            csv_name = probe.get("csv")
            pid = probe.get("id")
            if not csv_name or not pid:
                continue
            wf = load_sim_waveform_csv(sim_dir / csv_name)
            if wf is not None:
                out[str(pid)] = wf
    legacy = load_sim_waveform_csv(sim_dir / "sim_waveform.csv")
    if legacy is not None:
        out.setdefault("default", legacy)
    return out


def specs_for_entry(
    entry: ComponentMapping,
    all_specs: list[BenchCompareSpec],
) -> list[BenchCompareSpec]:
    ref = entry.reference
    matched = [s for s in all_specs if s.component_ref == ref]
    if matched:
        return matched
    if entry.measured:
        return [s for s in all_specs if s.component_ref is None]
    return []
