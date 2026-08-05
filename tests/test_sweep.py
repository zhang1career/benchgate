"""Unit tests for sim sweep override helpers and standalone block sweeps."""

from __future__ import annotations

import math
import shutil
from pathlib import Path

import pytest

from benchgate.sim.sweep import (
    absolutize_includes,
    apply_param,
    apply_set,
    parse_axis,
    parse_metric,
    parse_metric_specs,
    run_block_sweep,
)

HAS_NGSPICE = shutil.which("ngspice") is not None


def test_parse_axis_basic():
    assert parse_axis("DUTY=0.3,0.5,0.9") == ("DUTY", ["0.3", "0.5", "0.9"])


def test_parse_axis_strips_whitespace():
    assert parse_axis(" R11 = 10, 100 , 1k ") == ("R11", ["10", "100", "1k"])


def test_parse_axis_requires_equals():
    with pytest.raises(ValueError):
        parse_axis("DUTY")


def test_parse_axis_requires_values():
    with pytest.raises(ValueError):
        parse_axis("DUTY=")


def test_parse_metric_full():
    assert parse_metric("v(n_hdr):min:250u") == ("v(n_hdr)", "min", "250u")


def test_parse_metric_defaults_metric_min():
    assert parse_metric("v(vout)") == ("v(vout)", "min", None)


def test_apply_param_replaces_existing():
    text = ".title x\n.param DUTY=0.5\nV1 a b DC 1\n"
    out = apply_param(text, "DUTY", "0.9")
    assert ".param DUTY=0.9" in out
    assert ".param DUTY=0.5" not in out


def test_apply_param_injects_when_missing():
    text = ".title x\nV1 a b DC 1\n"
    out = apply_param(text, "VIN", "24")
    assert ".param VIN=24" in out
    # injected right after the title line
    assert out.splitlines()[1] == ".param VIN=24"


def test_apply_set_replaces_value():
    text = ".title x\nR11 Net-_D1-A_ VOUT 1k\nR7 a b 10\n"
    out = apply_set(text, "R11", "100")
    assert "R11 Net-_D1-A_ VOUT 100" in out
    assert "R11 Net-_D1-A_ VOUT 1k" not in out
    # unrelated element untouched
    assert "R7 a b 10" in out


def test_apply_set_missing_ref_raises():
    with pytest.raises(ValueError):
        apply_set(".title x\nR7 a b 10\n", "R11", "100")


def test_parse_metric_specs_named_and_bare():
    specs = parse_metric_specs(["bw=v(com):bw_3db", "v(o):peaking_db"])
    assert specs == {
        "bw": ("v(com)", "bw_3db", None),
        "v(o):peaking_db": ("v(o)", "peaking_db", None),
    }
    # order is preserved, because the first metric decides pass/fail
    assert next(iter(specs)) == "bw"


def test_parse_metric_specs_equals_after_colon_is_not_a_name():
    """A '=' inside a signal must not be mistaken for a name prefix."""
    specs = parse_metric_specs(["v(a):max:1u=2"])
    assert specs == {"v(a):max:1u=2": ("v(a)", "max", "1u=2")}


def test_parse_metric_specs_rejects_empty_and_duplicates():
    with pytest.raises(ValueError):
        parse_metric_specs([])
    with pytest.raises(ValueError):
        parse_metric_specs(["bw="])
    with pytest.raises(ValueError):
        parse_metric_specs(["bw=v(a):max", "bw=v(b):min"])


def test_absolutize_includes(tmp_path: Path):
    lib = tmp_path / "sub" / "opa.lib"
    lib.parent.mkdir()
    lib.write_text("* model\n", encoding="utf-8")
    text = (
        '.include "sub/opa.lib"\n'
        ".inc sub/opa.lib\n"
        ".lib sub/opa.lib typical\n"
        '.include "sub/missing.lib"\n'
    )
    out = absolutize_includes(text, tmp_path).splitlines()

    assert out[0] == f'.include "{lib.resolve()}"'
    assert out[1] == f'.inc "{lib.resolve()}"'
    # a .lib section name has to survive the rewrite
    assert out[2] == f'.lib "{lib.resolve()}" typical'
    # a path benchgate cannot resolve is left for ngspice's own search path
    assert out[3] == '.include "sub/missing.lib"'


RC_TESTBENCH = """RC block sweep testbench
.param RS=1k
.include "rc_block.lib"
V1 in 0 DC 0 AC 1
X1 in out RCLP
.control
ac dec 200 10 1meg
write sim.raw all
.endc
.end
"""

RC_BLOCK = """* one-pole RC, corner set by the caller's RS
.subckt RCLP a b
R1 a b {RS}
C1 b 0 159.155n
.ends
"""


@pytest.mark.skipif(not HAS_NGSPICE, reason="ngspice not installed")
def test_run_block_sweep_ac_over_a_param(tmp_path: Path):
    """A block testbench sweeps and yields AC metrics with no KiCad project.

    R = 1k with C = 159.155 nF puts the corner at 1.0 kHz, so doubling R has to
    halve the measured bandwidth. Nothing here touches a schematic or a profile.
    """
    design = tmp_path / "design"
    blocks = design / "models" / "blocks"
    blocks.mkdir(parents=True)
    (blocks / "rc_block.lib").write_text(RC_BLOCK, encoding="utf-8")
    tb = blocks / "rc_tb.cir"
    tb.write_text(RC_TESTBENCH, encoding="utf-8")

    report = run_block_sweep(
        tb,
        design / "reports" / "block_sweep",
        metrics=["bw=v(out):bw_3db", "peak=v(out):peaking_db"],
        params={"RS": ["1k", "2k"]},
        pass_gte=900.0,
    )

    payload = report.to_dict()
    assert payload["metrics"] == ["bw", "peak"]
    assert Path(payload["report_path"]).is_file()
    assert len(payload["points"]) == 2

    lo, hi = payload["points"]
    assert lo["overrides"] == {"RS": "1k"} and hi["overrides"] == {"RS": "2k"}
    assert all(pt["ngspice_ok"] for pt in payload["points"])

    assert math.isclose(lo["metrics"]["bw"], 1000.0, rel_tol=0.02)
    assert math.isclose(hi["metrics"]["bw"], 500.0, rel_tol=0.02)
    # a single pole cannot peak
    assert abs(lo["metrics"]["peak"]) < 1e-6

    # pass/fail follows the first metric only
    assert lo["passed"] is True
    assert hi["passed"] is False
    # the legacy scalar field still carries the primary metric
    assert lo["metric"] == lo["metrics"]["bw"]


@pytest.mark.skipif(not HAS_NGSPICE, reason="ngspice not installed")
def test_run_block_sweep_resolves_includes_from_netlist_dir(tmp_path: Path):
    """The include is relative to the testbench, not to the cwd or the run dir."""
    blocks = tmp_path / "models" / "blocks"
    blocks.mkdir(parents=True)
    (blocks / "rc_block.lib").write_text(RC_BLOCK, encoding="utf-8")
    tb = blocks / "rc_tb.cir"
    tb.write_text(RC_TESTBENCH, encoding="utf-8")

    report = run_block_sweep(tb, tmp_path / "out", metrics=["v(out):bw_3db"])
    point = report.to_dict()["points"][0]
    assert point["ngspice_ok"] is True
    assert math.isclose(point["metric"], 1000.0, rel_tol=0.02)


def test_run_block_sweep_missing_netlist(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        run_block_sweep(tmp_path / "nope.cir", tmp_path / "out", metrics=["v(o):max"])
