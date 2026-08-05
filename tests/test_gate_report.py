"""Tests for gate report: bench data from session store + analyze RMSE."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from benchgate.gate.report import build_gate_report, evaluate_entry, load_bench_waveform
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


def _bench_provenance(meta) -> ModelProvenance:
    return ModelProvenance(
        source=ModelSource.BENCH,
        generated_at=meta.captured_at.isoformat(),
        measured=MeasuredParams(
            component_ref="C1",
            mpn="x",
            captured_at=meta.captured_at.isoformat(),
            session_id=meta.session_id,
        ),
    )


def _sine_waveform(offset=0.0):
    t = np.linspace(0, 1e-3, 100)
    v = np.sin(2 * np.pi * 1e3 * t) + offset
    return Waveform(t, v, 1, datetime.now(timezone.utc))


def test_gate_loads_session_npz(tmp_path):
    store = LabDataStore(tmp_path / "captured")
    meta = store.write_session(component_ref="C1", waveforms={"scope_ch1": _sine_waveform()})
    measured = MeasuredParams(
        component_ref="C1",
        mpn="x",
        captured_at=meta.captured_at.isoformat(),
        session_id=meta.session_id,
    )
    loaded = load_bench_waveform(measured, captured_dir=tmp_path / "captured")
    assert loaded is not None
    assert len(loaded) == 100


def test_gate_rmse_via_analyze(tmp_path):
    store = LabDataStore(tmp_path / "captured")
    bench_wf = _sine_waveform()
    meta = store.write_session(component_ref="C1", waveforms={"scope_ch1": bench_wf})
    entry = ComponentMapping(
        kicad_key="Device:C::x",
        reference="C1",
        spice_kind=SpiceModelKind.SUBCKT,
        provenance=_bench_provenance(meta),
    )
    t2 = np.linspace(0, 1e-3, 80)
    sim_wf = Waveform(t2, np.sin(2 * np.pi * 1e3 * t2), 1, datetime.now(timezone.utc))
    result = evaluate_entry(
        entry,
        captured_dir=tmp_path / "captured",
        compare_specs=[],
        sim_waveforms={"default": sim_wf},
        sim_report=None,
        bench_compare_manifest=None,
    )
    assert result.has_bench and result.has_sim
    assert result.rmse == pytest.approx(0.0, abs=1e-2)


def test_check_valid_range_flags_out_of_range():
    from benchgate.gate.report import check_valid_range

    vr = {"vsupply_v": [4.5, 5.5], "temp_c": [-10, 85], "freq_hz": [1.0, 1e6]}
    warns = check_valid_range(vr, {"vsupply_v": 6.0, "temp_c": 25})
    # vsupply above max, freq_hz unverifiable, temp ok
    assert any("vsupply_v" in w and "above" in w for w in warns)
    assert any("freq_hz" in w and "cannot verify" in w for w in warns)
    assert not any("temp_c" in w for w in warns)


def test_check_valid_range_open_bound_and_ok():
    from benchgate.gate.report import check_valid_range

    vr = {"load_ohm": [1e3, float("inf")]}
    assert check_valid_range(vr, {"load_ohm": 1e6}) == []
    assert any("below" in w for w in check_valid_range(vr, {"load_ohm": 100}))


def test_gate_report_surfaces_range_warnings(tmp_path):
    from benchgate.gate.report import build_gate_report
    from benchgate.schemas import ModelProvenance, ModelSource

    manifest = MappingManifest(
        entries=[
            ComponentMapping(
                kicad_key="Sim:X::BLK",
                reference="X1",
                spice_kind=SpiceModelKind.SUBCKT,
                sim_name="BLK",
                provenance=ModelProvenance(
                    source=ModelSource.LTSPICE,
                    valid_range={"vsupply_v": [4.5, 5.5]},
                ),
            )
        ]
    )
    report = build_gate_report(
        manifest, captured_dir=tmp_path / "captured", operating_point={"vsupply_v": 12.0}
    )
    assert report.summary["range_warnings"] == 1
    entry = report.entries[0]
    assert entry.source == "ltspice"
    assert any("above" in w for w in entry.range_warnings)


def test_check_spec_pass_fail_and_uncharacterized():
    from benchgate.gate.report import check_spec

    spec = {"eff_pct": [90, 100], "ripple_mv": [0, 15]}
    assert check_spec(spec, {"eff_pct": 92, "ripple_mv": 10}) == []
    fails = check_spec(spec, {"eff_pct": 85, "ripple_mv": 20})
    assert any("eff_pct" in f and "below" in f for f in fails)
    assert any("ripple_mv" in f and "above" in f for f in fails)
    # missing metric → not characterized
    nc = check_spec(spec, {"eff_pct": 92})
    assert any("ripple_mv" in f and "not characterized" in f for f in nc)


def test_check_spec_malformed_bounds_fails():
    from benchgate.gate.report import build_gate_report, check_spec
    from benchgate.schemas import ModelProvenance, ModelSource

    fails = check_spec({"eff_pct": 90}, {"eff_pct": 95})
    assert any("eff_pct" in f and "invalid spec bounds" in f for f in fails)

    report = build_gate_report(
        MappingManifest(
            entries=[
                ComponentMapping(
                    kicad_key="Sim:X::BLK",
                    spice_kind=SpiceModelKind.SUBCKT,
                    spec={"eff_pct": 90},
                    provenance=ModelProvenance(
                        source=ModelSource.LTSPICE,
                        metrics={"eff_pct": 95},
                    ),
                )
            ]
        ),
        captured_dir=Path("/tmp/unused"),
    )
    assert report.entries[0].spec_status == "fail"
    assert report.summary["spec_failures"] == 1


def test_check_valid_range_malformed_bounds_warns():
    from benchgate.gate.report import check_valid_range

    warns = check_valid_range({"vsupply_v": 5.0}, {"vsupply_v": 5.0})
    assert any("vsupply_v" in w and "invalid valid_range bounds" in w for w in warns)


def test_gate_spec_status_from_metrics(tmp_path):
    from benchgate.gate.report import build_gate_report
    from benchgate.schemas import ModelProvenance, ModelSource

    def _entry(metrics):
        return ComponentMapping(
            kicad_key="Sim:X::BLK",
            reference="X1",
            spice_kind=SpiceModelKind.SUBCKT,
            sim_name="BLK",
            spec={"eff_pct": [90, 100]},
            provenance=ModelProvenance(source=ModelSource.LTSPICE, metrics=metrics),
        )

    passing = build_gate_report(MappingManifest(entries=[_entry({"eff_pct": 93})]), captured_dir=tmp_path)
    assert passing.entries[0].spec_status == "pass"
    assert passing.summary["spec_failures"] == 0

    failing = build_gate_report(MappingManifest(entries=[_entry({"eff_pct": 80})]), captured_dir=tmp_path)
    assert failing.entries[0].spec_status == "fail"
    assert failing.summary["spec_failures"] == 1


def test_build_gate_report_loads_operating_point_from_blocks_yaml(tmp_path):
    from benchgate.gate.report import build_gate_report
    from benchgate.schemas import ModelProvenance, ModelSource

    blocks_yaml = tmp_path / "blocks.yaml"
    blocks_yaml.write_text(
        "operating_point:\n  vsupply_v: 11.0\n  temp_c: 25\nblocks: []\n",
        encoding="utf-8",
    )
    manifest = MappingManifest(
        entries=[
            ComponentMapping(
                kicad_key="Sim:X::BLK",
                reference="X1",
                spice_kind=SpiceModelKind.SUBCKT,
                sim_name="BLK",
                provenance=ModelProvenance(
                    source=ModelSource.LTSPICE,
                    valid_range={"vsupply_v": [2.7, 12.6], "temp_c": [-40, 125]},
                ),
            )
        ]
    )
    report = build_gate_report(
        manifest,
        captured_dir=tmp_path / "captured",
        blocks_yaml=blocks_yaml,
    )
    assert report.summary["range_warnings"] == 0
    assert report.entries[0].range_warnings == []


def test_build_gate_report(tmp_path):
    store = LabDataStore(tmp_path / "captured")
    meta = store.write_session(component_ref="C1", waveforms={"scope_ch1": _sine_waveform()})
    manifest = MappingManifest(
        entries=[
            ComponentMapping(
                kicad_key="k1",
                reference="C1",
                spice_kind=SpiceModelKind.SUBCKT,
                provenance=_bench_provenance(meta),
            )
        ]
    )
    sim_csv = tmp_path / "sim.csv"
    t = np.linspace(0, 1e-3, 100)
    np.savetxt(sim_csv, np.column_stack([t, np.sin(2 * np.pi * 1e3 * t)]), header="t,v", comments="")
    report = build_gate_report(manifest, captured_dir=tmp_path / "captured", sim_raw_path=sim_csv)
    assert report.summary["compared"] == 1
