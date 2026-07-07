"""Unit tests for tran presets and coarse-to-fine refinement."""

from __future__ import annotations

import pytest

from benchgate.sim.tolerance_sim import (
    TranPreset,
    ToleranceSimConfig,
    apply_tran_preset,
    load_tolerance_sim_config,
    merge_preset_with_overrides,
    needs_fine_simulation,
)


def test_apply_tran_preset_replaces_tran_line():
    text = "* test\n.options maxstep=1u\n.control\ntran 0.5u 50m\n.endc\n.end\n"
    preset = TranPreset(id="coarse", tran_step="2u", tran_stop="35m", maxstep="10u")
    out = apply_tran_preset(text, preset)
    assert "tran 2u 35m" in out
    assert "maxstep=10u" in out


def test_merge_preset_with_overrides():
    base = TranPreset(id="coarse", tran_step="2u", tran_stop="35m")
    merged = merge_preset_with_overrides(base, tran_stop="40m")
    assert merged is not None
    assert merged.tran_step == "2u"
    assert merged.tran_stop == "40m"


def test_load_tolerance_sim_config_from_blocks():
    blocks = {
        "tolerance_sim": {
            "coarse": {"tran_step": "2u", "tran_stop": "35m"},
            "fine": {"tran_step": "0.5u", "tran_stop": "50m"},
            "refine_margin_pct": 3,
            "tier": "auto",
        }
    }
    cfg = load_tolerance_sim_config(blocks, {})
    assert cfg.coarse is not None
    assert cfg.coarse.tran_step == "2u"
    assert cfg.refine_margin_pct == 3.0
    assert cfg.resolve_tier() == "auto"


def test_needs_fine_simulation_near_bound():
    checks = [{"alias": "vout", "gte": 10.0, "lte": 12.0}]
    metrics_ok = {"vout": 11.0}
    metrics_edge = {"vout": 10.1}
    metrics_fail = {"vout": 9.0}

    def key_fn(chk, _):
        return str(chk["alias"])

    assert needs_fine_simulation(metrics_ok, checks, margin_pct=5.0, metric_key_fn=key_fn) is False
    assert needs_fine_simulation(metrics_edge, checks, margin_pct=5.0, metric_key_fn=key_fn) is True
    assert needs_fine_simulation(metrics_fail, checks, margin_pct=5.0, metric_key_fn=key_fn) is True


def test_resolve_tier_invalid():
    cfg = ToleranceSimConfig()
    with pytest.raises(ValueError, match="unknown sim tier"):
        cfg.resolve_tier("bogus")
