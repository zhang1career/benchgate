"""Tests for stress limits catalog."""

from __future__ import annotations

from pathlib import Path

from benchgate.sim.limits_catalog import load_limits_catalog, merge_stress_limits


def test_load_limits_catalog_from_examples() -> None:
    examples = Path(__file__).resolve().parents[1] / "docs" / "examples" / "stress_limits.yaml"
    catalog = load_limits_catalog(examples)
    assert catalog["SS8050"]["vceo"] == 25.0
    assert catalog["SS8050"]["pd_max"] == 0.3


def test_merge_stress_limits_profile_overrides_catalog() -> None:
    catalog = {"SS8050": {"vceo": 25.0, "vebo": 5.0}}
    merged = merge_stress_limits({"part": "SS8050", "limits": {"vceo": 20.0}}, catalog)
    assert merged["vceo"] == 20.0
    assert merged["vebo"] == 5.0
