"""Evaluate simple voltage expressions against ngspice raw signals."""

from __future__ import annotations

import re

import numpy as np

_VREF_RE = re.compile(
    r"v\s*\(\s*([^)]+?)\s*\)",
    re.IGNORECASE,
)


def _normalize(name: str) -> str:
    return re.sub(r"\s+", "", name.lower())


def _resolve_voltage(signals: dict[str, np.ndarray], node: str) -> np.ndarray | None:
    key = _normalize(node)
    if key in signals:
        return signals[key]
    wrapped = _normalize(f"v({key})")
    if wrapped in signals:
        return signals[wrapped]
    return None


def eval_voltage_expression(expr: str, signals: dict[str, np.ndarray]) -> np.ndarray | None:
    """Evaluate ``v(a) - v(b)``, ``abs(v(a)-v(b))``, or bare ``v(node)``."""
    text = expr.strip()
    if not text:
        return None

    abs_wrap = False
    if text.lower().startswith("abs(") and text.endswith(")"):
        abs_wrap = True
        text = text[4:-1].strip()

    refs = _VREF_RE.findall(text)
    if not refs:
        return None

    if len(refs) == 1 and _normalize(text) == _normalize(f"v({refs[0]})"):
        series = _resolve_voltage(signals, refs[0])
        if series is None:
            return None
        return np.abs(series) if abs_wrap else series

    # Replace v(node) with placeholders and use safe eval on differences.
    substituted = text
    values: dict[str, np.ndarray] = {}
    for i, node in enumerate(refs):
        series = _resolve_voltage(signals, node)
        if series is None:
            return None
        token = f"__v{i}__"
        values[token] = series
        substituted = _VREF_RE.sub(token, substituted, count=1)

    # Only allow +, -, *, /, parentheses on placeholders.
    if re.search(r"[^0-9eE+\-*/().\s_a-zA-Z]", substituted):
        return None

    def _eval_expr(t: str) -> np.ndarray:
        local: dict[str, np.ndarray] = dict(values)
        # pylint: disable=eval-used
        result = eval(t, {"__builtins__": {}}, local)  # noqa: S307
        if not isinstance(result, np.ndarray):
            result = np.asarray(result, dtype=float)
        return result

    try:
        out = _eval_expr(substituted)
    except Exception:
        return None
    return np.abs(out) if abs_wrap else out


def is_expression(signal: str) -> bool:
    text = signal.strip().lower()
    return text.startswith("v(") or text.startswith("abs(") or "-" in text or "+" in text
