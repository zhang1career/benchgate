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
    """Return (axis, {signal_name: values}) from an ngspice binary raw file.

    The axis is time for a transient run and frequency for an AC run. AC runs are
    flagged ``complex`` in the header and store two doubles per value; the signals
    then come back as complex arrays, so callers that want a magnitude must say
    so. Reading such a file as real doubles does not fail, it silently interleaves
    real and imaginary parts into neighbouring variables, so the flag has to be
    honoured here rather than left to the caller.
    """
    raw = path.read_bytes()
    marker = b"Binary:\n"
    idx = raw.find(marker)
    if idx < 0:
        raise ValueError(f"ngspice raw file missing Binary section: {path}")

    header = raw[:idx].decode("latin-1", errors="replace")
    nvars = int(re.search(r"No\. Variables:\s*(\d+)", header).group(1))
    npts = int(re.search(r"No\. Points:\s*(\d+)", header).group(1))
    flags = re.search(r"Flags:\s*(.*)", header)
    is_complex = bool(flags) and "complex" in flags.group(1).lower()

    names: list[str] = []
    for line in header.splitlines():
        match = re.match(r"\s*\d+\s+(\S+)", line)
        if match:
            names.append(_normalize_signal(match.group(1)))

    if len(names) != nvars:
        raise ValueError(f"variable count mismatch in {path}: header={len(names)} meta={nvars}")

    dtype = "<c16" if is_complex else "<f8"
    width = 16 if is_complex else 8
    data = raw[idx + len(marker) :]
    expected = nvars * npts * width
    if len(data) < expected:
        raise ValueError(f"truncated raw data in {path}: got {len(data)} need {expected}")

    matrix = np.frombuffer(data[:expected], dtype=dtype).reshape(npts, nvars)
    axis = matrix[:, 0]
    if is_complex:
        # the AC sweep variable is a real frequency stored in the real part
        axis = axis.real.copy()
    signals = {names[i]: matrix[:, i] for i in range(1, nvars)}
    return axis, signals


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


_MINUS_3DB = 20.0 * np.log10(1.0 / np.sqrt(2.0))  # -3.0103 dB

TIME_METRICS = ("min", "max", "avg", "rms", "pp", "final")
# Transient-only; optional params come from the check dict (settle_pct, settle_to, …).
TRANSIENT_METRICS = (
    "settling_time",
    "settling_time_01pct",
    "settling_time_001pct",
    "overshoot_pct",
    "slew_rate",
    "integral",
    "charge_nc",
)
# These need the sweep axis and only mean anything on a monotonic AC sweep.
FREQ_METRICS = ("bw_3db", "peaking_db", "gain_db_max", "gain_db_first")
METRIC_NAMES = TIME_METRICS + TRANSIENT_METRICS + FREQ_METRICS

_SETTLING_PRESETS: dict[str, float] = {
    "settling_time_01pct": 0.001,
    "settling_time_001pct": 0.0001,
}


def _series_values(values: np.ndarray) -> np.ndarray:
    if np.iscomplexobj(values):
        return np.abs(values)
    return values


def _settling_time(
    values: np.ndarray,
    axis: np.ndarray,
    *,
    settle_pct: float = 0.001,
    settle_to: str | float = "final",
) -> float:
    """Time from the window start until the signal enters and stays within ±pct of target."""
    if values.size < 2 or axis.size != values.size:
        return float("nan")
    y = _series_values(values)
    t = axis.astype(float)
    tail = y[-max(1, len(y) // 10) :]
    if isinstance(settle_to, (int, float)):
        target = float(settle_to)
    else:
        target = float(np.mean(tail))
    span = max(abs(float(y[-1]) - float(y[0])), abs(target - float(y[0])), 1e-12)
    tol = float(settle_pct) * span
    for idx in range(len(y)):
        if np.all(np.abs(y[idx:] - target) <= tol):
            return float(t[idx] - t[0])
    return float("nan")


def _overshoot_pct(values: np.ndarray, *, settle_to: str | float = "final") -> float:
    if values.size < 2:
        return float("nan")
    y = _series_values(values)
    if isinstance(settle_to, (int, float)):
        final = float(settle_to)
    else:
        final = float(np.mean(y[-max(1, len(y) // 10) :]))
    initial = float(y[0])
    step = final - initial
    if abs(step) < 1e-15:
        return 0.0
    if step > 0:
        peak = float(np.max(y))
        return max(0.0, (peak - final) / abs(step) * 100.0)
    trough = float(np.min(y))
    return max(0.0, (final - trough) / abs(step) * 100.0)


def _slew_rate(values: np.ndarray, axis: np.ndarray) -> float:
    if values.size < 2 or axis.size != values.size:
        return float("nan")
    y = _series_values(values)
    dt = np.diff(axis.astype(float))
    if not np.any(dt > 0):
        return float("nan")
    dv = np.diff(y)
    with np.errstate(divide="ignore", invalid="ignore"):
        rates = np.abs(dv / dt)
    return float(np.nanmax(rates))


def _integral(values: np.ndarray, axis: np.ndarray) -> float:
    if values.size < 2 or axis.size != values.size:
        return float("nan")
    y = _series_values(values)
    # numpy 2.0 renamed trapz -> trapezoid; keep both for 1.x installs.
    trapz = getattr(np, "trapezoid", None) or getattr(np, "trapz")
    return float(trapz(y, axis.astype(float)))


def _resolve_settle_pct(metric: str, params: dict | None) -> float:
    if params and "settle_pct" in params:
        return float(params["settle_pct"])
    if metric in _SETTLING_PRESETS:
        return _SETTLING_PRESETS[metric]
    return 0.001


def _resolve_settle_to(params: dict | None) -> str | float:
    if not params:
        return "final"
    if "settle_to" in params:
        return params["settle_to"]
    return "final"


def _interp_crossing(
    axis: np.ndarray, db: np.ndarray, index: int, target_db: float
) -> float:
    """Frequency at which ``db`` crosses ``target_db``, between index-1 and index.

    Interpolation is linear in dB against log frequency, which is the geometry a
    decade sweep actually samples.
    """
    if index == 0:
        return float(axis[0])
    f0, f1 = float(axis[index - 1]), float(axis[index])
    d0, d1 = float(db[index - 1]), float(db[index])
    if d1 == d0:
        return f1
    frac = (target_db - d0) / (d1 - d0)
    if f0 > 0.0 and f1 > 0.0:
        return float(10.0 ** (np.log10(f0) + frac * (np.log10(f1) - np.log10(f0))))
    return f0 + frac * (f1 - f0)


def _response_db(values: np.ndarray) -> tuple[np.ndarray, float]:
    """Magnitude in dB relative to the first sampled point, and that reference."""
    mag = np.abs(values)
    ref = float(mag[0])
    if ref <= 0.0:
        return np.full(mag.shape, np.nan), ref
    return 20.0 * np.log10(np.maximum(mag, 1e-300) / ref), ref


def _compute_metric(
    values: np.ndarray,
    metric: str,
    axis: np.ndarray | None = None,
    params: dict | None = None,
) -> float:
    if values.size == 0:
        return float("nan")
    metric = metric.lower()
    params = params or {}

    if metric in FREQ_METRICS:
        db, ref = _response_db(values)
        if metric == "gain_db_first":
            return 20.0 * float(np.log10(ref)) if ref > 0.0 else float("nan")
        if metric == "peaking_db":
            return float(np.nanmax(db))
        if metric == "gain_db_max":
            peak = float(np.nanmax(db))
            return peak + (20.0 * float(np.log10(ref)) if ref > 0.0 else float("nan"))
        if axis is None or axis.size != values.size or axis.size < 2:
            return float("nan")
        below = np.nonzero(db < _MINUS_3DB)[0]
        if below.size == 0:
            # never falls 3 dB anywhere in the swept range
            return float("inf")
        return _interp_crossing(axis, db, int(below[0]), _MINUS_3DB)

    if metric in TRANSIENT_METRICS:
        if axis is None or axis.size != values.size:
            return float("nan")
        settle_to = _resolve_settle_to(params)
        if metric.startswith("settling_time"):
            pct = _resolve_settle_pct(metric, params)
            return _settling_time(values, axis, settle_pct=pct, settle_to=settle_to)
        if metric == "overshoot_pct":
            return _overshoot_pct(values, settle_to=settle_to)
        if metric == "slew_rate":
            return _slew_rate(values, axis)
        if metric == "integral":
            return _integral(values, axis)
        if metric == "charge_nc":
            return _integral(values, axis) * 1e9

    # Complex data reaching a time-domain metric means an AC raw file; magnitude
    # is the only defensible reading.
    if np.iscomplexobj(values):
        values = np.abs(values)

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
        value = _compute_metric(series[mask], metric, axis=time[mask], params=check)
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
