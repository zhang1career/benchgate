"""Unit tests for sim sweep override helpers."""

from __future__ import annotations

import pytest

from benchgate.sim.sweep import apply_param, apply_set, parse_axis, parse_metric


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
