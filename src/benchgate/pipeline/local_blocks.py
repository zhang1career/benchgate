"""Sync ``models/blocks.yaml`` → manifest (spec, metrics, LTspice/local subckt models).

Agent workflow: drop ``blocks/*.net`` (or ``.asc`` when LTspice is available),
edit ``blocks.yaml`` with spec/metrics, run ``benchgate pipeline sync`` or
``benchgate watch once`` — no manual ``model build`` / ``spec set`` steps.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from benchgate.io.manifest import load_manifest, save_manifest
from benchgate.providers.base import register_model
from benchgate.providers.ltspice import LtspiceModelProvider, resolve_spice_source
from benchgate.schemas import ComponentMapping, MappingManifest


def load_blocks_config(blocks_yaml: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Load ``operating_point`` and ``blocks`` list from ``models/blocks.yaml``."""
    if not blocks_yaml.exists():
        return {}, []
    data = yaml.safe_load(blocks_yaml.read_text(encoding="utf-8")) or {}
    operating_point = data.get("operating_point") or {}
    blocks = data.get("blocks") or []
    if not isinstance(blocks, list):
        raise ValueError("blocks.yaml: 'blocks' must be a list")
    return operating_point, blocks


def _parse_pins(raw: str | list[str] | None) -> list[str] | None:
    if raw is None:
        return None
    if isinstance(raw, list):
        return [str(p) for p in raw]
    return str(raw).split()


def _load_metrics(block: dict[str, Any], models_dir: Path) -> dict[str, float]:
    from benchgate.providers.meas_log import merge_metrics, parse_meas_file

    metrics: dict[str, float] = {}
    inline = block.get("metrics")
    if isinstance(inline, dict):
        for key, val in inline.items():
            if isinstance(val, (int, float)):
                metrics[key] = float(val)
            elif isinstance(val, list):
                continue
            else:
                raise ValueError(
                    f"blocks.yaml metrics[{key!r}] must be numeric; "
                    f"store sweep tables in a separate *_sweeps.json sidecar"
                )
    metrics_file = block.get("metrics_file")
    if metrics_file:
        path = models_dir / metrics_file
        if not path.is_file():
            raise FileNotFoundError(f"metrics_file not found: {path}")
        if path.suffix.lower() in {".log", ".meas", ".txt"}:
            metrics = merge_metrics(metrics, parse_meas_file(path))
        else:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError(f"metrics_file must be a JSON object: {path}")
            for key, val in data.items():
                if isinstance(val, (int, float)):
                    metrics[key] = float(val)
                elif isinstance(val, list):
                    continue
                else:
                    raise TypeError(
                        f"metrics_file {path}: key {key!r} is {type(val).__name__}, "
                        "expected number (use a *_sweeps.json sidecar for tables)"
                    )
    return metrics


def _merge_provenance_dict(
    block_val: dict | None,
    existing_val: dict | None,
) -> dict:
    fallback = existing_val if isinstance(existing_val, dict) else {}
    if block_val is None:
        return fallback
    if not isinstance(block_val, dict):
        return fallback
    return block_val


def sync_local_blocks(
    *,
    models_dir: Path,
    manifest_path: Path,
    subckt_dir: Path,
    global_models_dir: Path,
    blocks_yaml: Path | None = None,
    tmp_dir: Path | None = None,
) -> dict[str, Any]:
    """Apply ``blocks.yaml``: spec + metrics + local model build for each block."""
    blocks_yaml = blocks_yaml or (models_dir / "blocks.yaml")
    operating_point, block_defs = load_blocks_config(blocks_yaml)
    if not block_defs:
        return {
            "skipped": True,
            "reason": "no blocks.yaml or empty blocks list",
            "operating_point": operating_point,
            "blocks": [],
        }

    manifest = (
        load_manifest(manifest_path, global_models_dir=global_models_dir)
        if manifest_path.exists()
        else MappingManifest()
    )
    workdir = (tmp_dir or models_dir / ".pipeline_tmp").resolve()
    results: list[dict[str, Any]] = []

    for block in block_defs:
        kicad_key = block.get("kicad_key")
        if not kicad_key:
            results.append({"error": "block missing kicad_key", "block": block})
            continue
        try:
            results.append(
                _sync_one_block(
                    block,
                    kicad_key=kicad_key,
                    manifest=manifest,
                    models_dir=models_dir,
                    subckt_dir=subckt_dir,
                    workdir=workdir,
                )
            )
        except Exception as exc:
            results.append({"kicad_key": kicad_key, "ok": False, "error": str(exc)})

    save_manifest(manifest, manifest_path, global_models_dir=global_models_dir)
    ok_count = sum(1 for r in results if r.get("ok"))
    return {
        "skipped": False,
        "operating_point": operating_point,
        "blocks_synced": ok_count,
        "blocks": results,
    }


def _iter_testbench_runs(block: dict[str, Any]) -> list[tuple[str, list[dict[str, Any]]]]:
    """Return (relative_path, measures) pairs from a block definition."""
    runs: list[tuple[str, list[dict[str, Any]]]] = []
    multi = block.get("testbenches")
    if isinstance(multi, list):
        for entry in multi:
            if not isinstance(entry, dict):
                continue
            path = entry.get("path") or entry.get("testbench")
            measures = entry.get("measures")
            if path and measures:
                runs.append((str(path), measures))
    path = block.get("testbench")
    measures = block.get("measures")
    if path and measures:
        runs.append((str(path), measures))
    return runs


def _sync_one_block(
    block: dict[str, Any],
    *,
    kicad_key: str,
    manifest: MappingManifest,
    models_dir: Path,
    subckt_dir: Path,
    workdir: Path,
) -> dict[str, Any]:
    source_rel = block.get("source")
    if not source_rel:
        raise ValueError(f"block {kicad_key!r} missing 'source' path")

    entry = manifest.find(kicad_key) or ComponentMapping(kicad_key=kicad_key)
    if block.get("reference"):
        entry.reference = block["reference"]
    if "spec" in block and block["spec"] is not None:
        entry.spec = block["spec"]

    existing = entry.provenance
    valid_range = _merge_provenance_dict(block.get("valid_range"), existing.valid_range if existing else None)
    metrics = _load_metrics(block, models_dir)

    tb_runs = _iter_testbench_runs(block)
    if tb_runs:
        from benchgate.sim.block_measures import run_block_measures, write_metrics_file

        block_work = workdir / kicad_key.replace(":", "_")
        for idx, (testbench_rel, measures) in enumerate(tb_runs):
            tb_path = (models_dir / testbench_rel).resolve()
            measured = run_block_measures(
                testbench=tb_path,
                measures=measures,
                output_dir=block_work / f"measures_{idx:02d}",
            )
            metrics.update(measured)
        metrics_out = block.get("metrics_file")
        if metrics_out:
            write_metrics_file((models_dir / metrics_out).resolve(), metrics)

    if not metrics and existing and existing.metrics:
        metrics = dict(existing.metrics)

    source_path = (models_dir / source_rel).resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"block source not found: {source_path}")

    net_path = resolve_spice_source(source_path, workdir=workdir / kicad_key.replace(":", "_"))
    sim_name = block.get("sim_name") or source_path.stem.upper()
    pins = _parse_pins(block.get("pins"))

    provider = LtspiceModelProvider(
        net_path=net_path,
        sim_name=sim_name,
        pins=pins,
        valid_range=valid_range,
        metrics=metrics,
        notes=block.get("notes"),
    )
    artifact = provider.build(entry, workdir=subckt_dir)
    register_model(manifest, entry, artifact)

    return {
        "ok": True,
        "kicad_key": kicad_key,
        "reference": entry.reference,
        "source": str(source_path),
        "netlist": str(net_path),
        "sim_name": artifact.sim_name,
        "sim_pins": artifact.sim_pins,
        "metrics": artifact.provenance.metrics,
        "spec": entry.spec,
    }
