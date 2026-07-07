"""Unit tests for hierarchical MC layer planning."""

from __future__ import annotations

from pathlib import Path

from benchgate.sim.tolerance_layers import load_mc_layers, merge_layer_plan, synthesize_block_layers_from_blocks_list


def test_load_mc_layers_legacy_full():
    blocks = {
        "tolerances": [{"ref": "R1", "distribution": "uniform", "tolerance_pct": 1}],
        "environment": [{"id": "temp", "apply": "temp", "distribution": "uniform", "low": 0, "high": 50}],
    }
    layers = load_mc_layers(blocks)
    assert len(layers) == 1
    assert layers[0].id == "full"
    assert layers[0].scope == "design"


def test_synthesize_block_layers():
    blocks = {
        "blocks": [
            {
                "reference": "U_DRV",
                "source": "blocks/driver.net",
                "sim_name": "drv",
                "tolerances": [{"ref": "R3", "distribution": "uniform", "tolerance_pct": 5}],
            }
        ]
    }
    models = Path("/proj/pcb/models")
    layers = synthesize_block_layers_from_blocks_list(blocks, models)
    assert len(layers) == 1
    assert layers[0].id == "block:U_DRV"
    assert layers[0].scope == "block"
    assert "driver.net" in str(layers[0].source)


def test_merge_layer_plan_order_and_dedupe():
    blocks = {
        "blocks": [
            {
                "reference": "U_DRV",
                "source": "blocks/driver.net",
                "tolerances": [{"ref": "R3", "distribution": "uniform", "tolerance_pct": 5}],
            }
        ],
        "tolerances": [{"ref": "R1", "distribution": "uniform", "tolerance_pct": 1}],
    }
    models = Path("/proj/pcb/models")
    plan = merge_layer_plan(blocks, models)
    assert [layer.id for layer in plan] == ["block:U_DRV", "full"]
