"""Parse ngspice raw output and evaluate profile pass/fail checks."""

from __future__ import annotations

import re
import struct
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import yaml


@dataclass
class CheckResult:
    signal: str
    metric: str
    value: float
    passed: bool
    expected: str
    message: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SimCheckReport:
    passed: bool
    checks: list[CheckResult]

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "checks": [c.to_dict() for c in self.checks],
        }


def load_profile_checks(config_path: Path, profile: str) -> list[dict]:
    if not config_path.exists():
        return []
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    block = data.get(profile) or {}
    checks = block.get("checks", [])
    return [dict(c) for c in checks] if checks else []


def _normalize_signal(name: str) -> str:
    return re.sub(r"\s+", "", name.lower())


def parse_ngspice_raw(path: Path) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Return (time, {signal_name: values}) from ngspice binary raw file."""
    raw = path.read_bytes()
    marker = b"Binary:\n"
    idx = raw.find(marker)
    if idx < 0:
        raise ValueError(f"ngspice raw file missing Binary section: {path}")

    header = raw[:idx].decode("latin-1", errors="replace")
    nvars = int(re.search(r"No\. Variables:\s*(\d+)", header).group(1))
    npts = int(re.search(r"No\. Points:\s*(\d+)", header).group(1))

    names: list[str] = []
    for line in header.splitlines():
        match = re.match(r"\s*\d+\s+(\S+)", line)
        if match:
            names.append(_normalize_signal(match.group(1)))

    if len(names) != nvars:
        raise ValueError(f"variable count mismatch in {path}: header={len(names)} meta={nvars}")

    data = raw[idx + len(marker) :]
    expected = nvars * npts * 8
    if len(data) < expected:
        raise ValueError(f"truncated raw data in {path}: got {len(data)} need {expected}")

    matrix = np.frombuffer(data[:expected], dtype="<f8").reshape(npts, nvars)
    time = matrix[:, 0]
    signals = {names[i]: matrix[:, i] for i in range(1, nvars)}
    return time, signals


def _parse_window(value: str | float | int) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower()
    multipliers = {
        "s": 1.0,
        "ms": 1e-3,
        "us": 1e-6,
        "u": 1e-6,
        "ns": 1e-9,
        "n": 1e-9,
        "ps": 1e-12,
        "p": 1e-12,
    }
    for suffix, scale in sorted(multipliers.items(), key=lambda item: -len(item[0])):
        if text.endswith(suffix):
            return float(text[: -len(suffix)]) * scale
    return float(text)


def _window_slice(time: np.ndarray, window_after: float | None) -> np.ndarray:
    if window_after is None:
        return np.ones(time.shape, dtype=bool)
    start = _parse_window(window_after)
    return time >= start


def _compute_metric(values: np.ndarray, metric: str) -> float:
    if values.size == 0:
        return float("nan")
    metric = metric.lower()
    if metric == "min":
        return float(np.min(values))
    if metric == "max":
        return float(np.max(values))
    if metric == "avg":
        return float(np.mean(values))
    if metric == "rms":
        return float(np.sqrt(np.mean(values**2)))
    if metric == "final":
        return float(values[-1])
    if metric == "pp":
        return float(np.max(values) - np.min(values))
    raise ValueError(f"unsupported metric: {metric}")


def _resolve_signal(signals: dict[str, np.ndarray], name: str) -> np.ndarray | None:
    key = _normalize_signal(name)
    if key in signals:
        return signals[key]
    # Allow bare node names without v() wrapper.
    if not key.startswith("v("):
        alt = _normalize_signal(f"v({key})")
        if alt in signals:
            return signals[alt]
    return None


def _format_expected(check: dict) -> str:
    parts: list[str] = []
    if "gte" in check:
        parts.append(f">= {check['gte']}")
    if "gt" in check:
        parts.append(f"> {check['gt']}")
    if "lte" in check:
        parts.append(f"<= {check['lte']}")
    if "lt" in check:
        parts.append(f"< {check['lt']}")
    return " and ".join(parts)


def _evaluate_bounds(value: float, check: dict) -> tuple[bool, str]:
    if np.isnan(value):
        return False, "metric is NaN"
    if "gte" in check and value < float(check["gte"]):
        return False, f"{value:.6g} < gte {check['gte']}"
    if "gt" in check and value <= float(check["gt"]):
        return False, f"{value:.6g} <= gt {check['gt']}"
    if "lte" in check and value > float(check["lte"]):
        return False, f"{value:.6g} > lte {check['lte']}"
    if "lt" in check and value >= float(check["lt"]):
        return False, f"{value:.6g} >= lt {check['lt']}"
    return True, "ok"


def evaluate_checks(
    time: np.ndarray,
    signals: dict[str, np.ndarray],
    checks: list[dict],
) -> SimCheckReport:
    results: list[CheckResult] = []
    for check in checks:
        signal_name = str(check["signal"])
        metric = str(check.get("metric", "avg"))
        series = _resolve_signal(signals, signal_name)
        expected = _format_expected(check)

        if series is None:
            results.append(
                CheckResult(
                    signal=signal_name,
                    metric=metric,
                    value=float("nan"),
                    passed=False,
                    expected=expected,
                    message="signal not found in raw output",
                )
            )
            continue

        mask = _window_slice(time, check.get("window_after"))
        value = _compute_metric(series[mask], metric)
        passed, message = _evaluate_bounds(value, check)
        results.append(
            CheckResult(
                signal=signal_name,
                metric=metric,
                value=value,
                passed=passed,
                expected=expected,
                message=message,
            )
        )

    all_passed = bool(results) and all(r.passed for r in results)
    return SimCheckReport(passed=all_passed, checks=results)


def analyze_raw_file(raw_path: Path, checks: list[dict]) -> SimCheckReport | None:
    if not checks or not raw_path.exists():
        return None
    time, signals = parse_ngspice_raw(raw_path)
    return evaluate_checks(time, signals, checks)
