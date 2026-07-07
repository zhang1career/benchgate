"""Parse ngspice raw output and evaluate profile pass/fail checks."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from benchgate.sim.expressions import eval_voltage_expression, is_expression
from benchgate.sim.profile import load_profile_checks as _load_profile_checks


@dataclass
class CheckResult:
    signal: str
    metric: str
    value: float
    passed: bool
    expected: str
    message: str = ""
    alias: str | None = None

    def to_dict(self) -> dict:
        data = asdict(self)
        if data.get("alias") is None:
            data.pop("alias", None)
        return data


@dataclass
class SimCheckReport:
    passed: bool
    checks: list[CheckResult]

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "checks": [c.to_dict() for c in self.checks],
        }


def load_profile_checks(config_path: Path, profile: str = "default") -> list[dict]:
    return _load_profile_checks(config_path, profile)


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


def _resolve_series(
    signals: dict[str, np.ndarray],
    check: dict,
) -> tuple[np.ndarray | None, str]:
    signal_name = str(check.get("expr") or check.get("signal") or "")
    if not signal_name:
        return None, ""
    if is_expression(signal_name):
        series = eval_voltage_expression(signal_name, signals)
        if series is not None and check.get("abs"):
            series = np.abs(series)
        return series, signal_name
    series = _resolve_signal(signals, signal_name)
    if series is not None and check.get("abs"):
        series = np.abs(series)
    return series, signal_name


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


def _resolve_signal(signals: dict[str, np.ndarray], name: str) -> np.ndarray | None:
    key = _normalize_signal(name)
    if key in signals:
        return signals[key]

    candidates: list[str] = [key]

    # Bare node → v(node)
    if not key.startswith("v(") and not key.startswith("i(") and not key.startswith("@"):
        candidates.append(_normalize_signal(f"v({key})"))

    # Branch / device current: i(q1) / @q1[c] → i(@q1[ic]) in ngspice raw
    if key.startswith("@"):
        candidates.append(_normalize_signal(f"i({key})"))
        if key.endswith("[c]"):
            ic_key = key[:-3] + "[ic]"
            candidates.append(_normalize_signal(f"i({ic_key})"))
    if key.startswith("i(") and key.endswith(")"):
        ref = key[2:-1]
        candidates.extend(
            [
                _normalize_signal(f"@{ref}[c]"),
                _normalize_signal(f"@{ref}[ic]"),
                _normalize_signal(f"i(@{ref}[ic])"),
                _normalize_signal(f"i(v{ref})"),
                _normalize_signal(f"@{ref.lower()}[c]"),
            ]
        )

    for candidate in candidates:
        if candidate in signals:
            return signals[candidate]
    return None


def evaluate_checks(
    time: np.ndarray,
    signals: dict[str, np.ndarray],
    checks: list[dict],
) -> SimCheckReport:
    results: list[CheckResult] = []
    for check in checks:
        metric = str(check.get("metric", "avg"))
        series, signal_name = _resolve_series(signals, check)
        expected = _format_expected(check)
        alias = check.get("alias")

        if series is None:
            results.append(
                CheckResult(
                    signal=signal_name,
                    metric=metric,
                    value=float("nan"),
                    passed=False,
                    expected=expected,
                    message="signal not found in raw output",
                    alias=alias,
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
                alias=alias,
            )
        )

    all_passed = bool(results) and all(r.passed for r in results)
    return SimCheckReport(passed=all_passed, checks=results)


def analyze_raw_file(raw_path: Path, checks: list[dict]) -> SimCheckReport | None:
    if not checks or not raw_path.exists():
        return None
    time, signals = parse_ngspice_raw(raw_path)
    return evaluate_checks(time, signals, checks)


def analyze_raw_stress(raw_path: Path, stress_block: dict):
    """Evaluate stress checks; imported lazily to avoid cycles."""
    from benchgate.sim.stress import evaluate_stress

    if not stress_block or not raw_path.exists():
        return None
    time, signals = parse_ngspice_raw(raw_path)
    return evaluate_stress(time, signals, stress_block)
