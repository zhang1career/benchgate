"""Monte Carlo / LHS tolerance study over passive component values."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from benchgate.io.blocks_config import (
    circuit_spec_to_checks,
    load_blocks_yaml,
    load_environment,
    load_tolerances,
)
from benchgate.kicad.cli_export import export_spice_netlist
from benchgate.kicad.project import KiCadProject
from benchgate.sim.analysis import analyze_raw_file
from benchgate.sim.netlist import prepare_netlist
from benchgate.sim.profile import load_profile_checks
from benchgate.sim.runner import run_ngspice
from benchgate.sim.sensitivity import compute_sensitivity, top_sensitivity_drivers
from benchgate.sim.sequential import sequential_should_stop, wilson_yield_interval
from benchgate.sim.surrogate import (
    fit_linear_surrogate,
    fit_polynomial_surrogate,
    predict_yield_from_surrogates,
)
from benchgate.sim.tolerance_core import (
    ToleranceAxis,
    format_spice_number,
    lhs_unit,
    parse_spice_number,
)
from benchgate.sim.tolerance_sampling import (
    adaptive_focus_columns,
    apply_sample_to_netlist,
    build_sampling_axes,
    build_sampling_plan,
    refine_unit_vectors,
    warmup_sample_count,
)

# Re-export primitives used by tests and external callers.
__all__ = [
    "ToleranceAxis",
    "ToleranceReport",
    "build_sampling_axes",
    "format_spice_number",
    "lhs_unit",
    "parse_spice_number",
    "run_tolerance_study",
]


@dataclass
class TolerancePoint:
    sample: int
    overrides: dict[str, str]
    passed: bool
    metrics: dict[str, float] = field(default_factory=dict)
    u_norm: dict[str, float] = field(default_factory=dict)
    u_dim: dict[str, float] = field(default_factory=dict)
    mix_choice: dict[str, str] = field(default_factory=dict)


@dataclass
class ToleranceReport:
    n_samples: int
    seed: int
    yield_pct: float
    passed_count: int
    metrics_summary: dict[str, dict[str, float]]
    sampling_dims: list[dict]
    sensitivity: dict[str, dict[str, float | None]]
    failure_drivers: dict[str, list[dict]]
    strategy: str
    n_warmup: int | None
    surrogate: dict[str, dict]
    surrogate_yield_pct: float | None
    points: list[dict]
    ran_at: str
    report_path: str | None = None
    yield_ci_low_pct: float | None = None
    yield_ci_high_pct: float | None = None
    yield_ci_width_pct: float | None = None
    sequential_info: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _resolve_checks(blocks_yaml: Path, sim_profile_path: Path, profile: str) -> list[dict]:
    blocks = load_blocks_yaml(blocks_yaml)
    checks = circuit_spec_to_checks(blocks.get("circuit_spec"))
    if checks:
        return checks
    return load_profile_checks(sim_profile_path, profile)


def _metric_key(check: dict, value: float) -> str:
    alias = check.get("alias")
    if alias:
        return str(alias)
    return f"{check.get('signal')}:{check.get('metric')}"


def _run_sample_batch(
    *,
    base_text: str,
    component_axes,
    env_axes,
    mix_axes,
    key_to_col,
    unit: np.ndarray,
    checks,
    output_dir: Path,
    design_dir: Path,
    sample_offset: int = 0,
) -> tuple[list[TolerancePoint], dict[str, list[float]]]:
    points: list[TolerancePoint] = []
    metric_values: dict[str, list[float]] = {}
    for i in range(unit.shape[0]):
        text, overrides, u_norm, mix_choice, u_dim = apply_sample_to_netlist(
            base_text,
            component_axes=component_axes,
            env_axes=env_axes,
            mix_axes=mix_axes,
            key_to_col=key_to_col,
            unit_row=unit[i],
            design_dir=design_dir,
        )
        idx = sample_offset + i
        point_cir = output_dir / f"pt{idx:03d}.cir"
        point_cir.write_text(text, encoding="utf-8")
        work = output_dir / f"pt{idx:03d}"
        work.mkdir(parents=True, exist_ok=True)
        result = run_ngspice(point_cir, work_dir=work)
        report = analyze_raw_file(result.raw_output, checks) if result.raw_output else None
        passed = bool(report and report.passed)
        metrics: dict[str, float] = {}
        if report:
            for chk, res in zip(checks, report.checks):
                key = _metric_key(chk, res.value)
                metrics[key] = res.value
                metric_values.setdefault(key, []).append(res.value)
        points.append(
            TolerancePoint(
                sample=idx,
                overrides=overrides,
                passed=passed,
                metrics=metrics,
                u_norm=u_norm,
                u_dim=u_dim,
                mix_choice=mix_choice,
            )
        )
    return points, metric_values


def run_tolerance_study(
    design_dir: Path,
    manifest_path: Path,
    output_dir: Path,
    *,
    blocks_yaml: Path,
    sim_profile_path: Path,
    profile: str = "charge_pump",
    n_samples: int = 200,
    seed: int = 42,
    strategy: str = "lhs",
    warmup_ratio: float = 0.25,
    surrogate_degree: int = 2,
    sequential_batch: int = 25,
    sequential_ci_width: float = 5.0,
    sequential_min_samples: int = 50,
) -> ToleranceReport:
    output_dir.mkdir(parents=True, exist_ok=True)
    blocks = load_blocks_yaml(blocks_yaml)
    axes_raw = load_tolerances(blocks_yaml)
    env_raw = load_environment(blocks_yaml)
    if not axes_raw and not env_raw:
        raise ValueError("blocks.yaml has no tolerances[] or environment[]")

    project = KiCadProject.load(design_dir)
    exported = output_dir / "exported.net"
    base_prepared = output_dir / "tolerance_base.cir"
    export_spice_netlist(project.schematic, exported)
    prepare_netlist(
        exported,
        manifest_path,
        base_prepared,
        sim_profile_path=sim_profile_path,
        profile=profile,
    )
    base_text = base_prepared.read_text(encoding="utf-8")
    operating_point = blocks.get("operating_point") or {}
    component_axes, env_axes, mix_axes, key_to_col, sampling_dims = build_sampling_plan(
        axes_raw,
        env_raw,
        base_text,
        operating_point,
    )
    n_dims = len(key_to_col)
    dim_keys = [d["key"] for d in sampling_dims]

    checks = _resolve_checks(blocks_yaml, sim_profile_path, profile)
    if not checks:
        raise ValueError("no checks from circuit_spec or sim profile")

    rng = np.random.default_rng(seed)
    points: list[TolerancePoint] = []
    metric_values: dict[str, list[float]] = {}
    n_warmup: int | None = None
    sequential_info: dict | None = None
    effective_n = n_samples

    if strategy == "sequential" and n_dims > 0:
        points = []
        metric_values = {}
        offset = 0
        while offset < n_samples:
            batch_n = min(sequential_batch, n_samples - offset)
            unit = lhs_unit(batch_n, n_dims, rng)
            batch_points, batch_metrics = _run_sample_batch(
                base_text=base_text,
                component_axes=component_axes,
                env_axes=env_axes,
                mix_axes=mix_axes,
                key_to_col=key_to_col,
                unit=unit,
                checks=checks,
                output_dir=output_dir,
                design_dir=design_dir,
                sample_offset=offset,
            )
            points.extend(batch_points)
            for key, vals in batch_metrics.items():
                metric_values.setdefault(key, []).extend(vals)
            offset += batch_n
            passed_count = sum(1 for p in points if p.passed)
            stop, sequential_info = sequential_should_stop(
                passed_count,
                len(points),
                min_samples=sequential_min_samples,
                max_samples=n_samples,
                ci_width_pct=sequential_ci_width,
            )
            if stop:
                effective_n = len(points)
                break
        n_warmup = None
    elif strategy == "adaptive" and n_dims > 0:
        n_warmup = warmup_sample_count(n_samples, n_dims, warmup_ratio)
        warmup_unit = lhs_unit(n_warmup, n_dims, rng)
        warmup_points, warmup_metrics = _run_sample_batch(
            base_text=base_text,
            component_axes=component_axes,
            env_axes=env_axes,
            mix_axes=mix_axes,
            key_to_col=key_to_col,
            unit=warmup_unit,
            checks=checks,
            output_dir=output_dir,
            design_dir=design_dir,
            sample_offset=0,
        )
        points.extend(warmup_points)
        for key, vals in warmup_metrics.items():
            metric_values.setdefault(key, []).extend(vals)

        warmup_dicts = [asdict(p) for p in warmup_points]
        metric_keys_so_far = list(metric_values)
        focus_cols = adaptive_focus_columns(
            warmup_dicts,
            dim_keys=dim_keys,
            key_to_col=key_to_col,
            metric_keys=metric_keys_so_far,
        )
        n_refine = n_samples - n_warmup
        refine_unit = refine_unit_vectors(n_refine, n_dims, rng, focus_cols=focus_cols)
        refine_points, refine_metrics = _run_sample_batch(
            base_text=base_text,
            component_axes=component_axes,
            env_axes=env_axes,
            mix_axes=mix_axes,
            key_to_col=key_to_col,
            unit=refine_unit,
            checks=checks,
            output_dir=output_dir,
            design_dir=design_dir,
            sample_offset=n_warmup,
        )
        points.extend(refine_points)
        for key, vals in refine_metrics.items():
            metric_values.setdefault(key, []).extend(vals)
    else:
        unit = lhs_unit(n_samples, max(n_dims, 1), rng)[:, :n_dims] if n_dims else np.zeros((n_samples, 0))
        points, metric_values = _run_sample_batch(
            base_text=base_text,
            component_axes=component_axes,
            env_axes=env_axes,
            mix_axes=mix_axes,
            key_to_col=key_to_col,
            unit=unit,
            checks=checks,
            output_dir=output_dir,
            design_dir=design_dir,
        )
        n_warmup = n_samples if strategy == "lhs" else None

    passed_count = sum(1 for p in points if p.passed)
    total = len(points)
    yield_pct, ci_lo, ci_hi = wilson_yield_interval(passed_count, total)
    ci_width = ci_hi - ci_lo
    metrics_summary: dict[str, dict[str, float]] = {}
    for key, vals in metric_values.items():
        arr = np.asarray(vals, dtype=float)
        metrics_summary[key] = {
            "p0.1": float(np.percentile(arr, 0.1)),
            "p50": float(np.percentile(arr, 50)),
            "p99.9": float(np.percentile(arr, 99.9)),
            "min": float(np.min(arr)),
            "max": float(np.max(arr)),
        }

    point_dicts = [asdict(p) for p in points]
    sens_refs = list({axis.ref for axis in component_axes} | {axis.ref for axis in mix_axes})
    for axis in env_axes:
        sens_refs.append(f"env:{axis.id}")
    metric_keys = list(metrics_summary)
    sensitivity = compute_sensitivity(point_dicts, axis_refs=sens_refs, metric_keys=metric_keys)
    failed_dicts = [p for p in point_dicts if not p.get("passed")]
    if failed_dicts:
        failure_sensitivity = compute_sensitivity(
            failed_dicts, axis_refs=sens_refs, metric_keys=metric_keys
        )
        failure_drivers = top_sensitivity_drivers(failure_sensitivity)
    else:
        failure_drivers = top_sensitivity_drivers(sensitivity)

    surrogate: dict[str, dict] = {}
    for metric in metric_keys:
        if surrogate_degree >= 2:
            model = fit_polynomial_surrogate(
                point_dicts, dim_keys=dim_keys, metric_key=metric, degree=surrogate_degree
            )
        else:
            model = fit_linear_surrogate(point_dicts, dim_keys=dim_keys, metric_key=metric)
        if model:
            surrogate[metric] = model
    surrogate_yield = predict_yield_from_surrogates(
        surrogate,
        checks=checks,
        dim_keys=dim_keys,
        seed=seed + 1,
    )

    report = ToleranceReport(
        n_samples=effective_n,
        seed=seed,
        yield_pct=yield_pct,
        passed_count=passed_count,
        metrics_summary=metrics_summary,
        sampling_dims=sampling_dims,
        sensitivity=sensitivity,
        failure_drivers=failure_drivers,
        strategy=strategy,
        n_warmup=n_warmup,
        surrogate=surrogate,
        surrogate_yield_pct=surrogate_yield,
        yield_ci_low_pct=ci_lo,
        yield_ci_high_pct=ci_hi,
        yield_ci_width_pct=ci_width,
        sequential_info=sequential_info,
        points=point_dicts,
        ran_at=datetime.now(timezone.utc).isoformat(),
    )
    out_path = output_dir / "mc_tolerance.json"
    out_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    report.report_path = str(out_path)
    return report
