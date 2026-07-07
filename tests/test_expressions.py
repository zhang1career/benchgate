"""Tests for voltage expression evaluation."""

from __future__ import annotations

import numpy as np

from benchgate.sim.expressions import eval_voltage_expression, is_expression


def test_is_expression() -> None:
    assert is_expression("v(+12v) - v(net-_q1-e_)")
    assert is_expression("abs(v(a)-v(b))")
    assert not is_expression("vout")


def test_eval_difference() -> None:
    signals = {
        "v(+12v)": np.full(4, 12.0),
        "v(emit)": np.array([2.0, 4.0, 6.0, 8.0]),
    }
    out = eval_voltage_expression("v(+12v) - v(emit)", signals)
    assert out is not None
    assert np.allclose(out, [10.0, 8.0, 6.0, 4.0])


def test_eval_abs_wrapper() -> None:
    signals = {
        "v(a)": np.array([5.0, -3.0]),
        "v(b)": np.array([1.0, 2.0]),
    }
    out = eval_voltage_expression("abs(v(a)-v(b))", signals)
    assert out is not None
    assert np.allclose(out, [4.0, 5.0])
