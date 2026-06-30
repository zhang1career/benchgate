"""Tests for gate report: bench data from session store + analyze RMSE."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from benchgate.gate.report import build_gate_report, evaluate_entry, load_bench_waveform
from benchgate.instruments.types import Waveform
from benchgate.lab.store import LabDataStore
from benchgate.schemas import ComponentMapping, MappingManifest, MeasuredParams, SpiceModelKind


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
        measured=MeasuredParams(
            component_ref="C1",
            mpn="x",
            captured_at=meta.captured_at.isoformat(),
            session_id=meta.session_id,
        ),
    )
    t2 = np.linspace(0, 1e-3, 80)
    sim_wf = Waveform(t2, np.sin(2 * np.pi * 1e3 * t2), 1, datetime.now(timezone.utc))
    result = evaluate_entry(entry, captured_dir=tmp_path / "captured", sim_waveform=sim_wf)
    assert result.has_bench and result.has_sim
    assert result.rmse == pytest.approx(0.0, abs=1e-2)


def test_build_gate_report(tmp_path):
    store = LabDataStore(tmp_path / "captured")
    meta = store.write_session(component_ref="C1", waveforms={"scope_ch1": _sine_waveform()})
    manifest = MappingManifest(
        entries=[
            ComponentMapping(
                kicad_key="k1",
                reference="C1",
                spice_kind=SpiceModelKind.SUBCKT,
                measured=MeasuredParams(
                    component_ref="C1",
                    mpn="x",
                    captured_at=meta.captured_at.isoformat(),
                    session_id=meta.session_id,
                ),
            )
        ]
    )
    sim_csv = tmp_path / "sim.csv"
    t = np.linspace(0, 1e-3, 100)
    np.savetxt(sim_csv, np.column_stack([t, np.sin(2 * np.pi * 1e3 * t)]), header="t,v", comments="")
    report = build_gate_report(manifest, captured_dir=tmp_path / "captured", sim_raw_path=sim_csv)
    assert report.summary["compared"] == 1
