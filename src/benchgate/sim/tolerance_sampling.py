"""Sampling plan helpers for tolerance / Monte Carlo studies."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from benchgate.sim.sweep import apply_param
from benchgate.sim.tolerance_core import (
    ToleranceAxis,
    format_spice_number,
    lhs_unit,
    parse_spice_number,
    read_element_value,
)


@dataclass
class EnvironmentAxis:
    id: str
    apply: str
    param: str
    nominal: float
    distribution: str
    tolerance_pct: float | None = None
    low: float | None = None
    high: float | None = None
    sample_key: str = ""

    def sample_numeric(self, u: float) -> float:
        if self.distribution == "uniform":
            if self.low is not None and self.high is not None:
                return float(self.low + u * (self.high - self.low))
            pct = (self.tolerance_pct or 0.0) / 100.0
            lo = self.nominal * (1.0 - pct)
            hi = self.nominal * (1.0 + pct)
            return float(lo + u * (hi - lo))
        raise ValueError(f"unsupported environment distribution {self.distribution!r}")

    def format_param_value(self, value: float) -> str:
        if abs(value - round(value)) < 1e-6:
            return str(int(round(value)))
        return f"{value:g}"


@dataclass
class MixOption:
    id: str
    weight: float
    tolerance_pct: float | None = None
    sim_name: str | None = None
    sim_library: str | None = None


@dataclass
class MixAxis:
    ref: str
    nominal: str
    distribution: str
    options: list[MixOption]
    mix_kind: str = "value"  # value | model | both
    sample_key: str = ""
    value_key: str = ""

    def select_option(self, u_cat: float) -> MixOption:
        weights = np.asarray([opt.weight for opt in self.options], dtype=float)
        weights = weights / weights.sum()
        edges = np.concatenate([[0.0], np.cumsum(weights)])
        idx = int(np.searchsorted(edges, u_cat, side="right") - 1)
        idx = min(max(idx, 0), len(self.options) - 1)
        return self.options[idx]

    def sample_value(self, option: MixOption, u: float) -> str | None:
        if option.tolerance_pct is None:
            return None
        nom = parse_spice_number(self.nominal)
        pct = option.tolerance_pct / 100.0
        if self.distribution == "uniform":
            lo = nom * (1.0 - pct)
            hi = nom * (1.0 + pct)
            val = lo + u * (hi - lo)
        else:
            raise ValueError(f"unsupported distribution {self.distribution!r}")
        return format_spice_number(val, self.nominal)


def resolve_environment_nominal(raw: dict, operating_point: dict) -> float:
    if raw.get("nominal") is not None:
        return float(raw["nominal"])
    src = str(raw.get("nominal_from", ""))
    if src.startswith("operating_point."):
        key = src.split(".", 1)[1]
        if key not in operating_point:
            raise KeyError(f"operating_point missing {key!r} for environment {raw.get('id')!r}")
        return float(operating_point[key])
    raise ValueError(f"environment {raw.get('id')!r} needs nominal or nominal_from")


def build_sampling_plan(
    tolerances_raw: list[dict],
    environment_raw: list[dict],
    base_text: str,
    operating_point: dict,
) -> tuple[
    list[ToleranceAxis],
    list[EnvironmentAxis],
    list[MixAxis],
    dict[str, int],
    list[dict],
]:
    """Build component, environment, and mix axes with shared LHS dimensions."""
    key_to_col: dict[str, int] = {}
    component_axes: list[ToleranceAxis] = []
    mix_axes: list[MixAxis] = []

    for raw in tolerances_raw:
        ref = str(raw["ref"])
        if raw.get("mix"):
            mix_key = f"mix:{ref}"
            if mix_key not in key_to_col:
                key_to_col[mix_key] = len(key_to_col)
            options = [
                MixOption(
                    id=str(item.get("id", f"opt{i}")),
                    weight=float(item.get("weight", 1.0)),
                    tolerance_pct=(
                        float(item["tolerance_pct"])
                        if item.get("tolerance_pct") is not None
                        else (float(raw["tolerance_pct"]) if raw.get("tolerance_pct") is not None else None)
                    ),
                    sim_name=str(item["sim_name"]) if item.get("sim_name") else None,
                    sim_library=str(item["sim_library"]) if item.get("sim_library") else None,
                )
                for i, item in enumerate(raw["mix"])
            ]
            has_model = any(opt.sim_name for opt in options)
            has_value = any(opt.tolerance_pct is not None for opt in options)
            if has_model and has_value:
                mix_kind = "both"
            elif has_model:
                mix_kind = "model"
            else:
                mix_kind = "value"
            value_key = ""
            if mix_kind in ("value", "both"):
                value_key = f"ref:{ref}"
                if value_key not in key_to_col:
                    key_to_col[value_key] = len(key_to_col)
            mix_axes.append(
                MixAxis(
                    ref=ref,
                    nominal=read_element_value(base_text, ref),
                    distribution=str(raw.get("distribution", "uniform")),
                    options=options,
                    mix_kind=mix_kind,
                    sample_key=mix_key,
                    value_key=value_key,
                )
            )
            continue

        group = raw.get("group")
        key = f"group:{group}" if group else f"ref:{ref}"
        if key not in key_to_col:
            key_to_col[key] = len(key_to_col)
        component_axes.append(
            ToleranceAxis(
                ref=ref,
                nominal=read_element_value(base_text, ref),
                distribution=str(raw.get("distribution", "uniform")),
                tolerance_pct=float(raw.get("tolerance_pct", 1.0)),
                group=str(group) if group else None,
                sample_key=key,
            )
        )

    env_axes: list[EnvironmentAxis] = []
    for raw in environment_raw:
        env_id = str(raw["id"])
        key = f"env:{env_id}"
        if key not in key_to_col:
            key_to_col[key] = len(key_to_col)
        env_axes.append(
            EnvironmentAxis(
                id=env_id,
                apply=str(raw.get("apply", "param")),
                param=str(raw.get("param", "")),
                nominal=resolve_environment_nominal(raw, operating_point),
                distribution=str(raw.get("distribution", "uniform")),
                tolerance_pct=float(raw["tolerance_pct"]) if raw.get("tolerance_pct") is not None else None,
                low=float(raw["low"]) if raw.get("low") is not None else None,
                high=float(raw["high"]) if raw.get("high") is not None else None,
                sample_key=key,
            )
        )

    refs_by_key: dict[str, list[str]] = {}
    meta_by_key: dict[str, dict] = {}
    for axis in component_axes:
        refs_by_key.setdefault(axis.sample_key, []).append(axis.ref)
        meta_by_key[axis.sample_key] = {"kind": "component", "group": axis.group}
    for axis in mix_axes:
        refs_by_key.setdefault(axis.sample_key, []).append(axis.ref)
        meta_by_key[axis.sample_key] = {"kind": "mix", "refs": [axis.ref], "mix_kind": axis.mix_kind}
        if axis.value_key:
            refs_by_key.setdefault(axis.value_key, []).append(axis.ref)
            meta_by_key[axis.value_key] = {"kind": "component_value", "refs": [axis.ref]}
    for axis in env_axes:
        refs_by_key.setdefault(axis.sample_key, []).append(axis.id)
        meta_by_key[axis.sample_key] = {
            "kind": "environment",
            "param": axis.param or None,
            "apply": axis.apply,
        }

    sampling_dims = [
        {
            "key": key,
            "refs": refs_by_key.get(key, []),
            **meta_by_key.get(key, {}),
        }
        for key in sorted(key_to_col, key=lambda k: key_to_col[k])
    ]
    return component_axes, env_axes, mix_axes, key_to_col, sampling_dims


def build_sampling_axes(
    axes_raw: list[dict],
    base_text: str,
) -> tuple[list[ToleranceAxis], dict[str, int], list[dict]]:
    """Backward-compatible wrapper without environment/mix axes."""
    component_axes, _, _, key_to_col, sampling_dims = build_sampling_plan(
        axes_raw, [], base_text, {}
    )
    return component_axes, key_to_col, sampling_dims


def refine_unit_vectors(
    n_refine: int,
    n_dims: int,
    rng: np.random.Generator,
    *,
    focus_cols: list[int] | None = None,
) -> np.ndarray:
    """Generate refinement samples biased toward corners on focus dimensions."""
    if n_refine <= 0:
        return np.zeros((0, n_dims))
    refine = lhs_unit(n_refine, n_dims, rng)
    cols = focus_cols or list(range(n_dims))
    for col in cols:
        corners = rng.choice([0.0, 1.0], size=n_refine)
        blend = rng.random(n_refine)
        refine[:, col] = np.where(blend < 0.65, corners, refine[:, col])
    return refine


def warmup_sample_count(n_samples: int, n_dims: int, warmup_ratio: float) -> int:
    n_warmup = max(n_dims + 1, int(round(n_samples * warmup_ratio)))
    return min(n_warmup, n_samples)


def unit_vectors(
    n_samples: int,
    n_dims: int,
    rng: np.random.Generator,
    *,
    strategy: str = "lhs",
    focus_cols: list[int] | None = None,
    warmup_ratio: float = 0.25,
) -> tuple[np.ndarray, int]:
    """Return (unit, n_warmup). For lhs, n_warmup == n_samples."""
    if strategy == "lhs" or n_dims == 0:
        if n_dims == 0:
            return np.zeros((n_samples, 0)), n_samples
        return lhs_unit(n_samples, n_dims, rng), n_samples

    n_warmup = max(n_dims + 1, int(round(n_samples * warmup_ratio)))
    n_warmup = min(n_warmup, n_samples)
    n_refine = n_samples - n_warmup
    warmup = lhs_unit(n_warmup, n_dims, rng) if n_warmup else np.zeros((0, n_dims))
    if n_refine == 0:
        return warmup, n_warmup

    refine = lhs_unit(n_refine, n_dims, rng)
    cols = focus_cols or list(range(n_dims))
    for col in cols:
        corners = rng.choice([0.0, 1.0], size=n_refine)
        blend = rng.random(n_refine)
        refine[:, col] = np.where(blend < 0.65, corners, refine[:, col])
    return np.vstack([warmup, refine]), n_warmup


def adaptive_focus_columns(
    points: list[dict],
    *,
    dim_keys: list[str],
    key_to_col: dict[str, int],
    metric_keys: list[str],
) -> list[int]:
    """Pick LHS columns to bias using |Spearman rho| on warmup points."""
    from benchgate.sim.sensitivity import compute_sensitivity

    if not points or not dim_keys:
        return []
    ref_by_key: dict[str, str] = {}
    for key in dim_keys:
        if key.startswith("env:"):
            ref_by_key[key] = key.split(":", 1)[1]
        elif key.startswith("mix:"):
            ref_by_key[key] = key.split(":", 1)[1]
        elif key.startswith("ref:"):
            ref_by_key[key] = key.split(":", 1)[1]
        elif key.startswith("group:"):
            refs = next((p.get("u_norm", {}) for p in points if p.get("u_norm")), {})
            for ref in refs:
                ref_by_key[key] = ref
                break

    sens_refs = list({v for v in ref_by_key.values()})
    sensitivity = compute_sensitivity(points, axis_refs=sens_refs, metric_keys=metric_keys)
    scores: dict[str, float] = {}
    for per_ref in sensitivity.values():
        for ref, rho in per_ref.items():
            if rho is None:
                continue
            for key, mapped in ref_by_key.items():
                if mapped == ref:
                    scores[key] = max(scores.get(key, 0.0), abs(rho))
    ranked = sorted(scores, key=lambda k: scores[k], reverse=True)
    return [key_to_col[k] for k in ranked[: max(1, len(ranked) // 2)] if k in key_to_col]


def apply_sample_to_netlist(
    base_text: str,
    *,
    component_axes: list[ToleranceAxis],
    env_axes: list[EnvironmentAxis],
    mix_axes: list[MixAxis],
    key_to_col: dict[str, int],
    unit_row: np.ndarray,
    design_dir: Path | None = None,
) -> tuple[str, dict[str, str], dict[str, float], dict[str, str], dict[str, float]]:
    """Apply one sample; return text, overrides, u_norm, mix_choice, u_dim."""
    text = base_text
    overrides: dict[str, str] = {}
    u_norm: dict[str, float] = {}
    u_dim: dict[str, float] = {}
    mix_choice: dict[str, str] = {}

    for axis in component_axes:
        u = float(unit_row[key_to_col[axis.sample_key]])
        u_dim[axis.sample_key] = u
        val = axis.sample_value(u)
        overrides[axis.ref] = val
        u_norm[axis.ref] = u
        text = _apply_set(text, axis.ref, val)

    for axis in mix_axes:
        u_cat = float(unit_row[key_to_col[axis.sample_key]])
        u_dim[axis.sample_key] = u_cat
        option = axis.select_option(u_cat)
        mix_choice[axis.ref] = option.id
        if option.sim_name:
            text = _swap_device_model(text, axis.ref, option.sim_name)
            overrides[f"model:{axis.ref}"] = option.sim_name
            if option.sim_library:
                lib = Path(option.sim_library)
                if design_dir and not lib.is_absolute():
                    lib = design_dir / lib
                text = _ensure_include(text, lib)
                overrides[f"lib:{axis.ref}"] = str(lib)
        if axis.value_key:
            u_val = float(unit_row[key_to_col[axis.value_key]])
            u_dim[axis.value_key] = u_val
            val = axis.sample_value(option, u_val)
            if val is not None:
                overrides[axis.ref] = val
                u_norm[axis.ref] = u_val
                text = _apply_set(text, axis.ref, val)

    for axis in env_axes:
        u = float(unit_row[key_to_col[axis.sample_key]])
        u_dim[axis.sample_key] = u
        u_norm[f"env:{axis.id}"] = u
        val = axis.sample_numeric(u)
        if axis.apply == "param":
            text = apply_param(text, axis.param, axis.format_param_value(val))
            overrides[f"param:{axis.param}"] = axis.format_param_value(val)
        elif axis.apply == "temp":
            text = _apply_temp(text, val)
            overrides[f"temp_c"] = axis.format_param_value(val)
        else:
            raise ValueError(f"unsupported environment apply {axis.apply!r}")

    return text, overrides, u_norm, mix_choice, u_dim


def _apply_set(text: str, ref: str, value: str) -> str:
    pat = re.compile(rf"^({re.escape(ref)}\s+.*\s)(\S+)\s*$", re.MULTILINE)
    new_text, n = pat.subn(lambda m: f"{m.group(1)}{value}", text, count=1)
    if n == 0:
        raise ValueError(f"tolerance --set: element {ref!r} not found in netlist")
    return new_text


def _swap_device_model(text: str, ref: str, model_name: str) -> str:
    pat = re.compile(rf"^({re.escape(ref)}\s+.*\s)(\S+)\s*$", re.MULTILINE)
    new_text, n = pat.subn(lambda m: f"{m.group(1)}{model_name}", text, count=1)
    if n == 0:
        raise ValueError(f"tolerance mix: device {ref!r} not found in netlist")
    return new_text


def _ensure_include(text: str, lib_path: Path) -> str:
    resolved = lib_path.expanduser().resolve()
    inc = f'.include "{resolved}"'
    if inc.lower() in text.lower():
        return text
    if re.search(r"^\.title\b.*$", text, flags=re.MULTILINE | re.IGNORECASE):
        return re.sub(
            r"^(\.title\b.*)$",
            r"\1\n" + inc,
            text,
            count=1,
            flags=re.MULTILINE | re.IGNORECASE,
        )
    return inc + "\n" + text


def _apply_temp(text: str, temp_c: float) -> str:
    line = (
        f".temp {int(round(temp_c))}"
        if abs(temp_c - round(temp_c)) < 1e-6
        else f".temp {temp_c:g}"
    )
    pat = re.compile(r"^\.temp\s+.*$", re.MULTILINE | re.IGNORECASE)
    if pat.search(text):
        return pat.sub(line, text, count=1)
    if re.search(r"^\.title\b.*$", text, flags=re.MULTILINE | re.IGNORECASE):
        return re.sub(
            r"^(\.title\b.*)$",
            r"\1\n" + line,
            text,
            count=1,
            flags=re.MULTILINE | re.IGNORECASE,
        )
    return line + "\n" + text
