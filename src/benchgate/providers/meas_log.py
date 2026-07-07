"""Parse LTspice / ngspice ``.MEAS`` log output into ``provenance.metrics`` dicts."""

from __future__ import annotations

import re
from pathlib import Path

# LTspice: ``vout_avg: AVG(V(out))=1.234 FROM ...`` (nested parens in expression)
_LTSPICE_EXPR_RE = re.compile(
    r"^\s*([A-Za-z_]\w*)\s*:\s*.+?=\s*"
    r"([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*(?:FROM\b|$)",
    re.IGNORECASE | re.MULTILINE,
)

# LTspice PARAM shorthand: ``eff_pct: PARAM=92.5``
_LTSPICE_PARAM_RE = re.compile(
    r"^\s*([A-Za-z_]\w*)\s*:\s*PARAM\s*=\s*"
    r"([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# ngspice control ``meas`` / print: ``vout_avg = 1.234567e+00``
_NGSPICE_ASSIGN_RE = re.compile(
    r"^\s*([A-Za-z_]\w*)\s*=\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*$",
    re.MULTILINE,
)

# Generic fallback: ``name=value`` or ``name: ... = value``
_GENERIC_RE = re.compile(
    r"^\s*([A-Za-z_]\w*)\s*[:=]\s*(?:[^=\n]*=\s*)?([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*$",
    re.MULTILINE,
)

_SKIP_NAMES = frozenset(
    {
        "date",
        "time",
        "title",
        "plotname",
        "flags",
        "command",
        "variables",
        "binary",
        "node",
        "temp",
        "tnom",
    }
)


def parse_meas_log(text: str) -> dict[str, float]:
    """Extract ``{measurement_name: value}`` from simulator log text."""
    metrics: dict[str, float] = {}

    def _add(name: str, raw: str) -> None:
        key = name.strip()
        if not key or key.lower() in _SKIP_NAMES:
            return
        try:
            val = float(raw)
        except ValueError:
            return
        if key not in metrics:
            metrics[key] = val

    for pattern in (_LTSPICE_EXPR_RE, _LTSPICE_PARAM_RE, _NGSPICE_ASSIGN_RE, _GENERIC_RE):
        for match in pattern.finditer(text):
            _add(match.group(1), match.group(2))

    return metrics


def parse_meas_file(path: Path) -> dict[str, float]:
    if not path.is_file():
        raise FileNotFoundError(f"meas log not found: {path}")
    return parse_meas_log(path.read_text(encoding="utf-8", errors="replace"))


def merge_metrics(*sources: dict[str, float] | None) -> dict[str, float]:
    """Later sources override earlier keys."""
    out: dict[str, float] = {}
    for src in sources:
        if src:
            out.update(src)
    return out
