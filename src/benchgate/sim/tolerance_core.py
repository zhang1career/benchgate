"""Shared primitives for tolerance / Monte Carlo sampling."""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

_SPICE_SUFFIX = {
    "f": 1e-15,
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "m": 1e-3,
    "k": 1e3,
    "K": 1e3,
    "M": 1e6,
    "G": 1e9,
}


def parse_spice_number(text: str) -> float:
    text = str(text).strip()
    if not text:
        raise ValueError("empty spice value")
    m = re.match(r"^([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)([fpnuMkKMGT]?)$", text)
    if not m:
        return float(text)
    base = float(m.group(1))
    suf = m.group(2)
    return base * _SPICE_SUFFIX.get(suf, 1.0)


def format_spice_number(value: float, template: str) -> str:
    """Preserve suffix style from template (e.g. 51k, 22n)."""
    template = str(template).strip()
    m = re.match(r"^([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)([fpnuMkKMGT]?)$", template)
    if not m:
        return f"{value:g}"
    suf = m.group(2)
    scale = _SPICE_SUFFIX.get(suf, 1.0)
    scaled = value / scale
    if suf:
        if abs(scaled - round(scaled)) < 1e-6:
            return f"{int(round(scaled))}{suf}"
        return f"{scaled:g}{suf}"
    return f"{value:g}"


def lhs_unit(n_samples: int, n_dims: int, rng: np.random.Generator) -> np.ndarray:
    """Latin Hypercube in [0, 1]^n_dims."""
    result = np.zeros((n_samples, n_dims))
    for j in range(n_dims):
        perm = rng.permutation(n_samples)
        result[:, j] = (perm + rng.random(n_samples)) / n_samples
    return result


def read_element_value(netlist_text: str, ref: str) -> str:
    pat = re.compile(rf"^{re.escape(ref)}\s+.*\s(\S+)\s*$", re.MULTILINE)
    m = pat.search(netlist_text)
    if not m:
        raise KeyError(f"element {ref!r} not found in netlist")
    return m.group(1)


@dataclass
class ToleranceAxis:
    ref: str
    nominal: str
    distribution: str
    tolerance_pct: float
    group: str | None = None
    sample_key: str = ""

    def sample_value(self, u: float) -> str:
        nom = parse_spice_number(self.nominal)
        pct = self.tolerance_pct / 100.0
        if self.distribution == "uniform":
            lo = nom * (1.0 - pct)
            hi = nom * (1.0 + pct)
            val = lo + u * (hi - lo)
        else:
            raise ValueError(f"unsupported distribution {self.distribution!r}")
        return format_spice_number(val, self.nominal)
