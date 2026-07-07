"""Tests for .MEAS log parsing."""

from __future__ import annotations

from pathlib import Path

from benchgate.providers.meas_log import merge_metrics, parse_meas_file, parse_meas_log


LTSPICE_LOG = """
Measurement: vout_avg
     vout_avg: AVG(V(out))=19.977545910036646 FROM 0.04 TO 0.05
Measurement: ripple_pp
     ripple_pp: PP(V(out))=0.0154321 FROM 0.04 TO 0.05
"""

NGSPICE_LOG = """
Note: Simulation executed from .control section
vout_avg =  1.234567e+00
ic_q1 = 5.6789e-02
"""

MIXED_LOG = """
eff_pct=88.5
vout_avg: AVG(v(vout))=12.3 FROM 0 TO 1
"""


def test_parse_ltspice_meas_log() -> None:
    m = parse_meas_log(LTSPICE_LOG)
    assert m["vout_avg"] == 19.977545910036646
    assert m["ripple_pp"] == 0.0154321


def test_parse_ngspice_meas_log() -> None:
    m = parse_meas_log(NGSPICE_LOG)
    assert m["vout_avg"] == 1.234567
    assert m["ic_q1"] == 0.056789


def test_parse_meas_file(tmp_path: Path) -> None:
    path = tmp_path / "block.log"
    path.write_text(MIXED_LOG, encoding="utf-8")
    m = parse_meas_file(path)
    assert m["eff_pct"] == 88.5
    assert m["vout_avg"] == 12.3


def test_merge_metrics_override() -> None:
    a = {"eff_pct": 88.0, "vout_v": 5.0}
    b = {"eff_pct": 92.0}
    assert merge_metrics(a, b) == {"eff_pct": 92.0, "vout_v": 5.0}
