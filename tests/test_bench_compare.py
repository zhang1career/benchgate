"""Tests for bench_compare waveform export and gate integration."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from benchgate.bench_compare import (
    BenchCompareSpec,
    export_sim_waveforms,
    merge_bench_compare_specs,
)
from benchgate.gate.report import build_gate_report, waveform_status_from_comparison
from benchgate.instruments.types import Waveform
from benchgate.lab.store import LabDataStore
from benchgate.schemas import (
    ComponentMapping,
    MappingManifest,
    MeasuredParams,
    ModelProvenance,
    ModelSource,
    SpiceModelKind,
)


def _write_fake_raw(path: Path, t: np.ndarray, signals: dict[str, np.ndarray]) -> None:
    names = ["time-sweep"] + list(signals.keys())
    nvars = len(names)
    npts = len(t)
    header = f"Title: test\nNo. Variables: {nvars}\nNo. Points: {npts}\n"
    for i, name in enumerate(names):
        header += f"{i}\t{name}\n"
    header += "Binary:\n"
    cols = [t] + [signals[k] for k in signals]
    matrix = np.column_stack(cols).astype("<f8")
    path.write_bytes(header.encode("latin-1") + matrix.tobytes())


def test_export_sim_waveforms(tmp_path):
    t = np.linspace(0, 1e-3, 50)
    raw = tmp_path / "sim.raw"
    _write_fake_raw(raw, t, {"v(vout)": np.sin(2 * np.pi * 1e3 * t)})

    specs = [
        BenchCompareSpec(id="vout", signal="v(vout)", sim_metric="avg", bench_channel="scope_ch1"),
    ]
    manifest = export_sim_waveforms(raw, specs, tmp_path)
    assert manifest["primary"] == "vout"
    assert (tmp_path / "sim_waveform_vout.csv").is_file()
    assert (tmp_path / "sim_waveform.csv").is_file()
    assert manifest["probes"][0]["sim_scalar"] == pytest.approx(0.0, abs=0.05)


def test_merge_manifest_override():
    profile = [BenchCompareSpec(id="board", signal="v(vout)", component_ref=None)]
    manifest = MappingManifest(
        entries=[
            ComponentMapping(
                kicad_key="Device:C::100n",
                reference="C1",
                spice_kind=SpiceModelKind.SUBCKT,
                bench_compare=[
                    {"id": "c1", "signal": "v(n_c1)", "bench_channel": "scope_ch1", "component_ref": "C1"},
                ],
            )
        ]
    )
    merged = merge_bench_compare_specs(profile, manifest)
    ids = {s.id for s in merged}
    assert "board" in ids
    assert "c1" in ids


def test_waveform_status_builtin_thresholds():
    assert waveform_status_from_comparison({"rmse": 0.05, "correlation": 0.99}) == "pass"
    assert waveform_status_from_comparison({"rmse": 0.15, "correlation": 0.99}) == "warn"
    assert waveform_status_from_comparison({"rmse": 0.25, "correlation": 0.99}) == "fail"
    assert waveform_status_from_comparison({"rmse": 0.05, "correlation": 0.7}) == "fail"


def test_build_gate_report_with_comparisons(tmp_path):
    store = LabDataStore(tmp_path / "captured")
    t = np.linspace(0, 1e-3, 100)
    bench_wf = Waveform(t, np.sin(2 * np.pi * 1e3 * t), 1, datetime.now(timezone.utc))
    meta = store.write_session(component_ref="C1", waveforms={"scope_ch1": bench_wf}, tags=["anomaly"])

    sim_dir = tmp_path / "sim"
    sim_dir.mkdir()
    sim_csv = sim_dir / "sim_waveform_vout.csv"
    np.savetxt(sim_csv, np.column_stack([t, np.sin(2 * np.pi * 1e3 * t)]), header="t,v", comments="")
    (sim_dir / "sim_waveform.csv").write_text(sim_csv.read_text())
    (sim_dir / "bench_compare.json").write_text(
        '{"probes":[{"id":"vout","signal":"v(vout)","csv":"sim_waveform_vout.csv","sim_scalar":0.0}],"primary":"vout"}',
        encoding="utf-8",
    )
    sim_report = {
        "checks": {
            "checks": [{"signal": "v(vout)", "metric": "avg", "value": 0.0, "passed": True}],
        }
    }
    (sim_dir / "sim_report.json").write_text(__import__("json").dumps(sim_report), encoding="utf-8")

    manifest = MappingManifest(
        entries=[
            ComponentMapping(
                kicad_key="Device:C::x",
                reference="C1",
                spice_kind=SpiceModelKind.SUBCKT,
                provenance=ModelProvenance(
                    source=ModelSource.BENCH,
                    measured=MeasuredParams(
                        component_ref="C1",
                        mpn="x",
                        captured_at=meta.captured_at.isoformat(),
                        session_id=meta.session_id,
                    ),
                ),
            )
        ]
    )

    profile_path = tmp_path / "sim_profiles.yaml"
    profile_path.write_text(
        """
default:
  bench_compare:
    - id: vout
      signal: v(vout)
      bench_channel: scope_ch1
      component_ref: C1
      sim_metric: avg
""".strip(),
        encoding="utf-8",
    )

    report = build_gate_report(
        manifest,
        captured_dir=tmp_path / "captured",
        sim_dir=sim_dir,
        sim_report_path=sim_dir / "sim_report.json",
        sim_profile_path=profile_path,
    )
    assert report.summary["compared"] == 1
    assert report.entries[0].waveform_status == "pass"
    assert report.entries[0].waveform_comparison is not None
