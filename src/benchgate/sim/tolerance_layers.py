"""Hierarchical / block-level Monte Carlo layers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class McLayer:
    id: str
    scope: str  # design | block
    block_ref: str | None = None
    source: str | None = None
    sim_name: str | None = None
    tolerances: list[dict[str, Any]] | None = None
    environment: list[dict[str, Any]] | None = None
    circuit_spec: dict[str, Any] | None = None
    enabled: bool = True


def load_mc_layers(blocks: dict[str, Any]) -> list[McLayer]:
    """Load explicit ``mc_layers`` or synthesize from legacy top-level fields."""
    raw_layers = blocks.get("mc_layers")
    if isinstance(raw_layers, list) and raw_layers:
        out: list[McLayer] = []
        for item in raw_layers:
            if not isinstance(item, dict):
                continue
            out.append(
                McLayer(
                    id=str(item.get("id", "layer")),
                    scope=str(item.get("scope", "design")),
                    block_ref=str(item["block_ref"]) if item.get("block_ref") else None,
                    source=str(item["source"]) if item.get("source") else None,
                    sim_name=str(item["sim_name"]) if item.get("sim_name") else None,
                    tolerances=list(item["tolerances"]) if item.get("tolerances") else None,
                    environment=list(item["environment"]) if item.get("environment") else None,
                    circuit_spec=dict(item["circuit_spec"]) if item.get("circuit_spec") else None,
                    enabled=bool(item.get("enabled", True)),
                )
            )
        return [layer for layer in out if layer.enabled]

    # Legacy: single full-design layer from top-level keys.
    tol = blocks.get("tolerances") or []
    env = blocks.get("environment") or []
    spec = blocks.get("circuit_spec")
    if not tol and not env:
        return []
    return [
        McLayer(
            id="full",
            scope="design",
            tolerances=[dict(t) for t in tol] if isinstance(tol, list) else None,
            environment=[dict(e) for e in env] if isinstance(env, list) else None,
            circuit_spec=dict(spec) if isinstance(spec, dict) else None,
        )
    ]


def synthesize_block_layers_from_blocks_list(
    blocks: dict[str, Any],
    models_dir: Path,
) -> list[McLayer]:
    """Optional block layers from ``blocks[]`` entries that declare ``tolerances``."""
    layers: list[McLayer] = []
    for item in blocks.get("blocks") or []:
        if not isinstance(item, dict) or not item.get("tolerances"):
            continue
        ref = str(item.get("reference") or item.get("sim_name") or item.get("kicad_key"))
        source = str(item.get("source", ""))
        layers.append(
            McLayer(
                id=f"block:{ref}",
                scope="block",
                block_ref=ref,
                source=str(models_dir / source) if source else None,
                sim_name=str(item.get("sim_name")) if item.get("sim_name") else None,
                tolerances=[dict(t) for t in item["tolerances"]],
                environment=[dict(e) for e in item.get("environment") or []],
                circuit_spec=dict(item["circuit_spec"]) if item.get("circuit_spec") else None,
            )
        )
    return layers


def merge_layer_plan(blocks: dict[str, Any], models_dir: Path) -> list[McLayer]:
    """Block layers first, then explicit/full-design layers (dedupe by id)."""
    seen: set[str] = set()
    ordered: list[McLayer] = []
    for layer in synthesize_block_layers_from_blocks_list(blocks, models_dir):
        if layer.id not in seen:
            seen.add(layer.id)
            ordered.append(layer)
    for layer in load_mc_layers(blocks):
        if layer.id not in seen:
            seen.add(layer.id)
            ordered.append(layer)
    return ordered
