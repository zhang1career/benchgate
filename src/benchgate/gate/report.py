"""Bench vs simulation quality reports."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from numbers import Real
from pathlib import Path
from typing import Any

import numpy as np

from benchgate.bench_compare import (
    DEFAULT_CORR_FAIL,
    DEFAULT_CORR_WARN,
    DEFAULT_RMSE_FAIL_V,
    DEFAULT_RMSE_WARN_V,
    BenchCompareSpec,
    load_bench_compare_manifest,
    load_merged_compare_specs,
    load_sim_waveforms,
    specs_for_entry,
)
from benchgate.io.manifest import load_manifest
from benchgate.instruments.types import Waveform
from benchgate.lab.analyze import compare_waveforms
from benchgate.lab.store import LabDataStore
from benchgate.rules.evaluate import RuleContext, evaluate_rule_packs
from benchgate.rules.loader import load_rule_packs
from benchgate.schemas import ComponentMapping, MappingManifest, MeasuredParams
from benchgate.sim.analysis import _compute_metric


@dataclass
class GateEntry:
    reference: str
    kicad_key: str
    has_bench: bool
    has_sim: bool
    rmse: float | None = None
    notes: str = ""
    source: str | None = None
    range_warnings: list[str] = field(default_factory=list)
    spec_status: str = "n/a"  # pass | fail | n/a
    spec_failures: list[str] = field(default_factory=list)
    waveform_comparison: dict[str, Any] | None = None
    waveform_status: str = "n/a"  # pass | warn | fail | n/a
    scalar_comparisons: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class GateReport:
    generated_at: str
    entries: list[GateEntry]
    summary: dict[str, int | float | bool | list | dict | None]

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "summary": self.summary,
            "entries": [asdict(e) for e in self.entries],
        }


def load_sim_report_context(sim_report_path: Path) -> tuple[dict | None, dict | None]:
    """Return (operating_point, stress_summary) from ``sim_report.json`` if present."""
    if not sim_report_path.exists():
        return None, None
    try:
        data = json.loads(sim_report_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None, None
    op = data.get("operating_point")
    stress = data.get("stress")
    stress_summary = None
    if stress:
        failures = [r for r in stress.get("results", []) if not r.get("passed")]
        stress_summary = {
            "passed": stress.get("passed"),
            "derating": stress.get("derating"),
            "total": len(stress.get("results", [])),
            "failures": failures,
            "warnings": stress.get("warnings") or [],
        }
    return op, stress_summary


def load_bench_waveform(
    measured: MeasuredParams,
    *,
    captured_dir: Path,
    channel: str = "scope_ch1",
) -> Waveform | None:
    """Load bench waveform from the session store (NPZ)."""
    try:
        return LabDataStore(captured_dir).load_waveform(measured.session_id, channel)
    except (FileNotFoundError, KeyError, OSError):
        return None


def load_session_derived(
    session_id: str,
    *,
    captured_dir: Path,
) -> dict[str, float]:
    try:
        meta = LabDataStore(captured_dir).get_session(session_id)
        return dict(meta.derived)
    except (FileNotFoundError, KeyError, OSError):
        return {}


def _interval_bounds(dim: str, bounds: object, *, label: str) -> tuple[object, object] | str:
    if isinstance(bounds, (list, tuple)) and len(bounds) == 2:
        return bounds[0], bounds[1]
    return f"{dim}: invalid {label} bounds (expected [min, max])"


def check_valid_range(
    valid_range: dict,
    operating_point: dict | None,
) -> list[str]:
    warnings: list[str] = []
    op = operating_point or {}
    for dim, bounds in valid_range.items():
        parsed = _interval_bounds(dim, bounds, label="valid_range")
        if isinstance(parsed, str):
            warnings.append(parsed)
            continue
        lo, hi = parsed
        val = op.get(dim)
        if not isinstance(val, Real):
            warnings.append(f"{dim}: cannot verify valid_range (no operating-point value)")
            continue
        if isinstance(lo, Real) and val < lo:
            warnings.append(f"{dim}={val:g} below valid_range min {lo:g}")
        if isinstance(hi, Real) and val > hi:
            warnings.append(f"{dim}={val:g} above valid_range max {hi:g}")
    return warnings


def check_spec(spec: dict, metrics: dict | None) -> list[str]:
    failures: list[str] = []
    m = metrics or {}
    for dim, bounds in spec.items():
        parsed = _interval_bounds(dim, bounds, label="spec")
        if isinstance(parsed, str):
            failures.append(parsed)
            continue
        lo, hi = parsed
        val = m.get(dim)
        if not isinstance(val, Real):
            failures.append(f"{dim}: not characterized (no metric for spec)")
            continue
        if isinstance(lo, Real) and val < lo:
            failures.append(f"{dim}={val:g} below spec min {lo:g}")
        if isinstance(hi, Real) and val > hi:
            failures.append(f"{dim}={val:g} above spec max {hi:g}")
    return failures


def _sim_check_value(sim_report: dict | None, signal: str, metric: str) -> float | None:
    if not sim_report:
        return None
    checks = (sim_report.get("checks") or {}).get("checks") or []
    for item in checks:
        if item.get("signal") == signal and item.get("metric") == metric:
            val = item.get("value")
            if isinstance(val, Real):
                return float(val)
    return None


def waveform_status_from_comparison(
    cmp: dict[str, Any],
    *,
    rmse_warn: float = DEFAULT_RMSE_WARN_V,
    rmse_fail: float = DEFAULT_RMSE_FAIL_V,
    corr_warn: float = DEFAULT_CORR_WARN,
    corr_fail: float = DEFAULT_CORR_FAIL,
) -> str:
    rmse = cmp.get("rmse")
    corr = cmp.get("correlation")
    if rmse is None or (isinstance(rmse, float) and np.isnan(rmse)):
        return "n/a"
    status = "pass"
    if isinstance(rmse, Real) and float(rmse) > rmse_fail:
        return "fail"
    if isinstance(corr, Real) and float(corr) < corr_fail:
        return "fail"
    if isinstance(rmse, Real) and float(rmse) > rmse_warn:
        status = "warn"
    if isinstance(corr, Real) and float(corr) < corr_warn:
        status = "warn"
    return status


def _bench_scalar(
    bench_wf: Waveform | None,
    derived: dict[str, float],
    spec: BenchCompareSpec,
) -> float | None:
    if spec.bench_metric:
        val = derived.get(spec.bench_metric)
        if isinstance(val, Real):
            return float(val)
    if bench_wf is not None and spec.sim_metric:
        return _compute_metric(bench_wf.voltage_v, spec.sim_metric)
    return None


def _sim_scalar(
    spec: BenchCompareSpec,
    sim_report: dict | None,
    probe_meta: dict[str, Any] | None,
) -> float | None:
    if probe_meta and probe_meta.get("sim_scalar") is not None:
        return float(probe_meta["sim_scalar"])
    if spec.sim_metric:
        return _sim_check_value(sim_report, spec.signal, spec.sim_metric)
    return None


def evaluate_compare_spec(
    spec: BenchCompareSpec,
    *,
    entry: ComponentMapping | None,
    captured_dir: Path,
    sim_waveforms: dict[str, Waveform],
    sim_report: dict | None,
    probe_meta: dict[str, Any] | None,
    operating_point: dict | None = None,
) -> dict[str, Any]:
    """Evaluate one bench_compare probe (waveform + optional scalar)."""
    bench_wf: Waveform | None = None
    derived: dict[str, float] = {}
    if entry and entry.measured:
        bench_wf = load_bench_waveform(
            entry.measured,
            captured_dir=captured_dir,
            channel=spec.bench_channel,
        )
        derived = load_session_derived(entry.measured.session_id, captured_dir=captured_dir)

    sim_wf = sim_waveforms.get(spec.id) or sim_waveforms.get("default")

    waveform_cmp: dict[str, Any] | None = None
    waveform_status = "n/a"
    if bench_wf and sim_wf:
        waveform_cmp = compare_waveforms(bench_wf, sim_wf).to_dict()
        waveform_status = waveform_status_from_comparison(waveform_cmp)

    bench_val = _bench_scalar(bench_wf, derived, spec)
    sim_val = _sim_scalar(spec, sim_report, probe_meta)
    scalar_row: dict[str, Any] | None = None
    scalar_status = "n/a"
    if bench_val is not None and sim_val is not None:
        delta = sim_val - bench_val
        rel = delta / bench_val if bench_val != 0 else float("nan")
        scalar_row = {
            "id": spec.id,
            "signal": spec.signal,
            "bench_metric": spec.bench_metric or spec.sim_metric,
            "bench_value": bench_val,
            "sim_value": sim_val,
            "delta": delta,
            "rel_error": rel,
        }
        tol = spec.tolerance_pct
        if tol is not None and bench_val != 0:
            rel_pct = abs(delta / bench_val) * 100.0
            scalar_status = "fail" if rel_pct > float(tol) else "pass"
        else:
            scalar_status = "pass" if abs(delta) < 1e-9 else "warn"

    notes = ""
    if bench_wf and not sim_wf:
        notes = "bench only"
    elif sim_wf and not bench_wf:
        notes = "sim only"

    return {
        "id": spec.id,
        "component_ref": spec.component_ref or (entry.reference if entry else None),
        "signal": spec.signal,
        "has_bench": bench_wf is not None,
        "has_sim": sim_wf is not None,
        "waveform": waveform_cmp,
        "waveform_status": waveform_status,
        "rmse": waveform_cmp.get("rmse") if waveform_cmp else None,
        "scalar": scalar_row,
        "scalar_status": scalar_status,
        "notes": notes,
    }


def evaluate_entry(
    entry: ComponentMapping,
    *,
    captured_dir: Path,
    compare_specs: list[BenchCompareSpec],
    sim_waveforms: dict[str, Waveform],
    sim_report: dict | None,
    bench_compare_manifest: dict | None,
    operating_point: dict | None = None,
) -> GateEntry:
    ref = entry.reference or entry.kicad_key
    entry_specs = specs_for_entry(entry, compare_specs)

    compare_results = []
    for spec in entry_specs:
        probe_meta = _probe_meta(bench_compare_manifest, spec.id)
        compare_results.append(
            evaluate_compare_spec(
                spec,
                entry=entry,
                captured_dir=captured_dir,
                sim_waveforms=sim_waveforms,
                sim_report=sim_report,
                probe_meta=probe_meta,
                operating_point=operating_point,
            )
        )

    primary = compare_results[0] if compare_results else None
    rmse = primary.get("rmse") if primary else None
    waveform_cmp = primary.get("waveform") if primary else None
    waveform_status = primary.get("waveform_status", "n/a") if primary else "n/a"
    notes = primary.get("notes", "") if primary else ""
    has_bench = any(r["has_bench"] for r in compare_results) or bool(entry.measured)
    has_sim = any(r["has_sim"] for r in compare_results) or bool(sim_waveforms)

    if not compare_results and entry.measured:
        bench_wf = load_bench_waveform(entry.measured, captured_dir=captured_dir)
        sim_wf = sim_waveforms.get("default")
        if bench_wf and sim_wf:
            waveform_cmp = compare_waveforms(bench_wf, sim_wf).to_dict()
            rmse = waveform_cmp.get("rmse")
            waveform_status = waveform_status_from_comparison(waveform_cmp)
        elif bench_wf:
            notes = "bench only"
        elif sim_wf:
            notes = "sim only"

    range_warnings: list[str] = []
    if entry.provenance and entry.provenance.valid_range:
        range_warnings = check_valid_range(entry.provenance.valid_range, operating_point)

    spec_status = "n/a"
    spec_failures: list[str] = []
    if entry.spec:
        metrics = entry.provenance.metrics if entry.provenance else None
        spec_failures = check_spec(entry.spec, metrics)
        spec_status = "fail" if spec_failures else "pass"

    scalar_rows = [r["scalar"] for r in compare_results if r.get("scalar")]

    return GateEntry(
        reference=ref,
        kicad_key=entry.kicad_key,
        has_bench=has_bench,
        has_sim=has_sim,
        rmse=rmse,
        notes=notes,
        source=entry.provenance.source.value if entry.provenance else None,
        range_warnings=range_warnings,
        spec_status=spec_status,
        spec_failures=spec_failures,
        waveform_comparison=waveform_cmp,
        waveform_status=waveform_status,
        scalar_comparisons=scalar_rows,
    )


def _probe_meta(manifest: dict | None, probe_id: str) -> dict[str, Any] | None:
    if not manifest:
        return None
    for probe in manifest.get("probes") or []:
        if probe.get("id") == probe_id:
            return probe
    return None


def build_gate_report(
    manifest: MappingManifest,
    *,
    captured_dir: Path,
    sim_dir: Path | None = None,
    sim_raw_path: Path | None = None,
    operating_point: dict | None = None,
    sim_report_path: Path | None = None,
    stress_sweep_path: Path | None = None,
    monte_carlo_path: Path | None = None,
    rule_pack_paths: list[Path] | None = None,
    sim_profile_path: Path | None = None,
    profile: str = "default",
    design_dir: Path | None = None,
    blocks_yaml: Path | None = None,
    erc_report_path: Path | None = None,
    block_sweep_report_path: Path | None = None,
) -> GateReport:
    sim_dir = sim_dir or (sim_raw_path.parent if sim_raw_path else None)
    bench_compare_manifest = load_bench_compare_manifest(sim_dir) if sim_dir else None
    sim_waveforms = load_sim_waveforms(sim_dir, bench_compare_manifest) if sim_dir else {}
    if not sim_waveforms and sim_raw_path and sim_raw_path.exists():
        from benchgate.bench_compare import load_sim_waveform_csv

        wf = load_sim_waveform_csv(sim_raw_path)
        if wf is not None:
            sim_waveforms["default"] = wf

    compare_specs = load_merged_compare_specs(
        manifest,
        sim_profile_path=sim_profile_path,
        profile=profile,
    )

    inferred_op, stress_summary = (
        load_sim_report_context(sim_report_path) if sim_report_path else (None, None)
    )
    sim_report_data: dict | None = None
    if sim_report_path and sim_report_path.exists():
        try:
            sim_report_data = json.loads(sim_report_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            sim_report_data = None
    if operating_point is None and inferred_op:
        operating_point = inferred_op
    if operating_point is None and blocks_yaml and blocks_yaml.is_file():
        from benchgate.pipeline.local_blocks import load_blocks_config

        op_from_blocks, _ = load_blocks_config(blocks_yaml)
        if op_from_blocks:
            operating_point = op_from_blocks

    monte_carlo_data: dict | None = None
    if monte_carlo_path and monte_carlo_path.exists():
        try:
            monte_carlo_data = json.loads(monte_carlo_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            monte_carlo_data = None

    block_sweep_data: dict | None = None
    if block_sweep_report_path and block_sweep_report_path.exists():
        try:
            block_sweep_data = json.loads(block_sweep_report_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            block_sweep_data = None
    elif sim_dir:
        sweep_candidate = sim_dir / "block_sweep_report.json"
        if sweep_candidate.exists():
            try:
                block_sweep_data = json.loads(sweep_candidate.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                block_sweep_data = None

    coverage_summary = None
    if design_dir and blocks_yaml and blocks_yaml.exists():
        from benchgate.gate.coverage import coverage_report

        try:
            coverage_summary = coverage_report(
                design_dir=design_dir,
                manifest=manifest,
                blocks_yaml=blocks_yaml,
            )
        except (FileNotFoundError, OSError):
            coverage_summary = None

    erc_summary = None
    if erc_report_path and erc_report_path.exists():
        from benchgate.kicad.erc import parse_erc_report

        erc_summary = parse_erc_report(erc_report_path).to_dict()
    elif design_dir:
        from benchgate.kicad.erc import find_erc_report, parse_erc_report

        erc_path = find_erc_report(design_dir)
        if erc_path:
            erc_summary = parse_erc_report(erc_path).to_dict()

    entries: list[GateEntry] = []
    board_comparisons: list[dict[str, Any]] = []
    refs_with_entry: set[str] = set()

    for item in manifest.entries:
        if not item.measured and item.spice_kind.value == "passive":
            continue
        if item.reference:
            refs_with_entry.add(item.reference)
        entries.append(
            evaluate_entry(
                item,
                captured_dir=captured_dir,
                compare_specs=compare_specs,
                sim_waveforms=sim_waveforms,
                sim_report=sim_report_data,
                bench_compare_manifest=bench_compare_manifest,
                operating_point=operating_point,
            )
        )

    for spec in compare_specs:
        if spec.component_ref and spec.component_ref in refs_with_entry:
            continue
        board_comparisons.append(
            evaluate_compare_spec(
                spec,
                entry=None,
                captured_dir=captured_dir,
                sim_waveforms=sim_waveforms,
                sim_report=sim_report_data,
                probe_meta=_probe_meta(bench_compare_manifest, spec.id),
                operating_point=operating_point,
            )
        )

    with_bench = sum(1 for e in entries if e.has_bench)
    with_sim = sum(1 for e in entries if e.has_sim)
    compared = sum(1 for e in entries if e.rmse is not None)
    range_warnings = sum(1 for e in entries if e.range_warnings)
    spec_failures = sum(1 for e in entries if e.spec_status == "fail")
    waveform_fails = sum(1 for e in entries if e.waveform_status == "fail")
    waveform_warns = sum(1 for e in entries if e.waveform_status == "warn")
    waveform_fails += sum(1 for c in board_comparisons if c.get("waveform_status") == "fail")
    waveform_warns += sum(1 for c in board_comparisons if c.get("waveform_status") == "warn")

    stress_sweep_summary = None
    if stress_sweep_path and stress_sweep_path.exists():
        try:
            sweep_data = json.loads(stress_sweep_path.read_text(encoding="utf-8"))
            stress_sweep_summary = sweep_data.get("worst")
        except (json.JSONDecodeError, OSError):
            pass

    gate_payload = {
        "entries": [asdict(e) for e in entries],
        "comparisons": board_comparisons,
        "waveform_thresholds": {
            "rmse_warn_v": DEFAULT_RMSE_WARN_V,
            "rmse_fail_v": DEFAULT_RMSE_FAIL_V,
            "correlation_warn": DEFAULT_CORR_WARN,
            "correlation_fail": DEFAULT_CORR_FAIL,
        },
    }

    rules_summary = None
    if rule_pack_paths is not None:
        packs = load_rule_packs(rule_pack_paths)
        ctx = RuleContext(
            sim_report=sim_report_data,
            monte_carlo=monte_carlo_data,
            operating_point=operating_point,
            gate_report=gate_payload,
            block_sweep_report=block_sweep_data,
        )
        rules_summary = evaluate_rule_packs(packs, ctx).to_dict()

    coverage_count = (
        int(coverage_summary["uncovered_count"])
        if coverage_summary and coverage_summary.get("uncovered_count") is not None
        else 0
    )
    erc_errors = int(erc_summary["errors"]) if erc_summary else 0
    erc_warnings = int(erc_summary["warnings"]) if erc_summary else 0

    return GateReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        entries=entries,
        summary={
            "total": len(entries),
            "with_bench": with_bench,
            "with_sim": with_sim,
            "compared": compared,
            "range_warnings": range_warnings,
            "spec_failures": spec_failures,
            "waveform_failures": waveform_fails,
            "waveform_warnings": waveform_warns,
            "stress_summary": stress_summary,
            "stress_sweep_worst": stress_sweep_summary,
            "comparisons": board_comparisons,
            "rules": rules_summary,
            "coverage": coverage_summary,
            "coverage_warnings": coverage_count,
            "erc": erc_summary,
            "erc_errors": erc_errors,
            "erc_warnings": erc_warnings,
            "block_sweep_aggregates": (
                block_sweep_data.get("aggregates") if block_sweep_data else None
            ),
        },
    )


def write_gate_report(
    manifest_path: Path,
    output_path: Path,
    *,
    captured_dir: Path,
    sim_dir: Path | None = None,
    sim_raw_path: Path | None = None,
    operating_point: dict | None = None,
    sim_report_path: Path | None = None,
    stress_sweep_path: Path | None = None,
    monte_carlo_path: Path | None = None,
    rule_pack_paths: list[Path] | None = None,
    sim_profile_path: Path | None = None,
    profile: str = "default",
    design_dir: Path | None = None,
    blocks_yaml: Path | None = None,
    erc_report_path: Path | None = None,
    block_sweep_report_path: Path | None = None,
) -> GateReport:
    manifest = load_manifest(manifest_path)
    report = build_gate_report(
        manifest,
        captured_dir=captured_dir,
        sim_dir=sim_dir,
        sim_raw_path=sim_raw_path,
        operating_point=operating_point,
        sim_report_path=sim_report_path,
        stress_sweep_path=stress_sweep_path,
        monte_carlo_path=monte_carlo_path,
        rule_pack_paths=rule_pack_paths,
        sim_profile_path=sim_profile_path,
        profile=profile,
        design_dir=design_dir,
        blocks_yaml=blocks_yaml,
        erc_report_path=erc_report_path,
        block_sweep_report_path=block_sweep_report_path,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    return report
