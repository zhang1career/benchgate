"""Transient presets and coarse-to-fine refinement for tolerance batch runs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class TranPreset:
    id: str
    tran_step: str | None = None
    tran_stop: str | None = None
    maxstep: str | None = None

    @classmethod
    def from_dict(cls, preset_id: str, raw: dict[str, Any]) -> TranPreset:
        return cls(
            id=preset_id,
            tran_step=str(raw["tran_step"]) if raw.get("tran_step") else None,
            tran_stop=str(raw["tran_stop"]) if raw.get("tran_stop") else None,
            maxstep=str(raw["maxstep"]) if raw.get("maxstep") else None,
        )


@dataclass
class ToleranceSimConfig:
    coarse: TranPreset | None = None
    fine: TranPreset | None = None
    refine_margin_pct: float = 5.0
    tier: str = "auto"  # auto | coarse | fine

    def resolve_tier(self, requested: str | None = None) -> str:
        tier = (requested or self.tier or "auto").lower()
        if tier not in {"auto", "coarse", "fine"}:
            raise ValueError(f"unknown sim tier {tier!r}")
        return tier


def load_tolerance_sim_config(blocks: dict[str, Any], profile_block: dict[str, Any]) -> ToleranceSimConfig:
    raw = blocks.get("tolerance_sim") or profile_block.get("tolerance_sim") or {}
    coarse_raw = raw.get("coarse")
    fine_raw = raw.get("fine")
    coarse = TranPreset.from_dict("coarse", coarse_raw) if isinstance(coarse_raw, dict) else None
    fine = TranPreset.from_dict("fine", fine_raw) if isinstance(fine_raw, dict) else None
    return ToleranceSimConfig(
        coarse=coarse,
        fine=fine,
        refine_margin_pct=float(raw.get("refine_margin_pct", 5.0)),
        tier=str(raw.get("tier", "auto")),
    )


def merge_preset_with_overrides(
    preset: TranPreset | None,
    *,
    tran_step: str | None = None,
    tran_stop: str | None = None,
    maxstep: str | None = None,
) -> TranPreset | None:
    if preset is None and not any([tran_step, tran_stop, maxstep]):
        return None
    base = preset or TranPreset(id="override")
    return TranPreset(
        id=base.id,
        tran_step=tran_step or base.tran_step,
        tran_stop=tran_stop or base.tran_stop,
        maxstep=maxstep or base.maxstep,
    )


_TRAN_LINE_RE = re.compile(
    r"^(\s*tran\s+)(?P<step>\S+)(?:\s+(?P<stop>\S+))?(?P<rest>.*)$",
    re.IGNORECASE | re.MULTILINE,
)
_MAXSTEP_RE = re.compile(r"^(\.options\b.*\bmaxstep=)(\S+)", re.IGNORECASE | re.MULTILINE)


def apply_tran_preset(text: str, preset: TranPreset | None) -> str:
    if preset is None or (not preset.tran_step and not preset.tran_stop and not preset.maxstep):
        return text
    if preset.tran_step or preset.tran_stop:
        replaced = False

        def _sub(m: re.Match[str]) -> str:
            nonlocal replaced
            replaced = True
            step = preset.tran_step or m.group("step")
            stop = preset.tran_stop or m.group("stop") or ""
            rest = m.group("rest") or ""
            mid = f"{step} {stop}".strip()
            return f"{m.group(1)}{mid}{rest}"

        text = _TRAN_LINE_RE.sub(_sub, text)
        if not replaced and preset.tran_step and preset.tran_stop:
            text = text.rstrip() + f"\n.control\ntran {preset.tran_step} {preset.tran_stop}\n.endc\n"
    if preset.maxstep:
        if _MAXSTEP_RE.search(text):
            text = _MAXSTEP_RE.sub(lambda m: f"{m.group(1)}{preset.maxstep}", text, count=1)
        else:
            text = text.rstrip() + f"\n.options maxstep={preset.maxstep}\n"
    return text


def _metric_margin_pct(value: float, check: dict) -> float | None:
    """Distance to nearest bound as % of span; 0 = on bound, negative = violated."""
    lo = check.get("gte")
    hi = check.get("lte")
    if not (lo is not None or hi is not None):
        return None
    if lo is not None and value < float(lo):
        return -1.0
    if hi is not None and value > float(hi):
        return -1.0
    margins: list[float] = []
    if lo is not None and hi is not None:
        span = float(hi) - float(lo)
        if span <= 0:
            return None
        margins.append(100.0 * (value - float(lo)) / span)
        margins.append(100.0 * (float(hi) - value) / span)
        return min(margins)
    if lo is not None:
        return 100.0 if value > float(lo) else 0.0
    if hi is not None:
        return 100.0 * (float(hi) - value) / max(abs(float(hi)), 1e-12)
    return None


def needs_fine_simulation(
    metrics: dict[str, float],
    checks: list[dict],
    *,
    margin_pct: float,
    metric_key_fn,
) -> bool:
    """Refine when coarse failed or any metric is within margin_pct of a spec bound."""
    for check in checks:
        key = metric_key_fn(check, float("nan"))
        value = metrics.get(key)
        if value is None or not (value == value):  # NaN
            return True
        m = _metric_margin_pct(value, check)
        if m is None:
            continue
        if m < 0 or m <= margin_pct:
            return True
    return False
