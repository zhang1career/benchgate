"""Bench capture orchestration built on the unified instrument layer.

A :class:`LabSession` resolves logical roles (scope / dmm / awg) from a
:class:`~benchgate.instruments.registry.Bench` and drives an RC step-response
acquisition:

* ``awg`` bound  -> TARS GPIO produces the 0 -> 3.3 V edge that triggers the scope.
* ``awg`` null   -> the scope is armed and waits for an external/manual stimulus.

The DMM (if bound) is polled for a steady-state reading in the same session.
Results are persisted as one :class:`~benchgate.lab.store.LabDataStore` session.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np

from benchgate.instruments import (
    Bench,
    DigitalStimulus,
    Instrument,
    Oscilloscope,
    ScalarReader,
    TriggerConfig,
    TriggerSlope,
)
from benchgate.instruments.types import QuantityKind, ScalarSeries, Waveform
from benchgate.lab.store import LabDataStore, SessionMeta
from benchgate.schemas import MeasuredParams


@dataclass
class LabConfig:
    """Capture parameters. Defaults overridable via project lab.yaml `capture:`."""

    scope_channel: int = 1
    scope_auto_setup: bool = False
    trigger_level_v: float = 1.0
    awg_channel: str = "pg13"
    idle_high: bool = False
    rising: bool = True
    edge_settle_s: float = 0.05
    dmm_readings: int = 5
    dmm_settle_s: float = 0.2
    sample_count: int = 10_000

    @classmethod
    def from_capture_dict(cls, data: dict[str, Any]) -> "LabConfig":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in (data or {}).items() if k in known})


class LabSession:
    """Open the role-bound instruments for a bench acquisition."""

    def __init__(self, bench: Bench, config: LabConfig | None = None) -> None:
        self.bench = bench
        self.config = config or LabConfig.from_capture_dict(bench.capture)
        self._scope: Instrument | None = None
        self._dmm: Instrument | None = None
        self._awg: Instrument | None = None
        self._opened: list[Instrument] = []

    def __enter__(self) -> "LabSession":
        # Scope is required; dmm/awg are optional.
        self._scope = self._open("scope", required=True)
        self._dmm = self._open("dmm", required=False)
        self._awg = self._open("awg", required=False)
        return self

    def __exit__(self, *exc: object) -> None:
        for inst in reversed(self._opened):
            try:
                inst.disconnect()
            except Exception:
                pass
        self._opened.clear()

    def _open(self, role: str, *, required: bool) -> Instrument | None:
        if not self.bench.instrument_for_role(role):
            if required:
                raise RuntimeError(f"Role {role!r} must be bound to an instrument for capture")
            return None
        inst = self.bench.open_role(role)
        self._opened.append(inst)
        return inst

    # --- acquisition ---

    def capture_step_response(self, channel: int | None = None) -> Waveform:
        if self._scope is None:
            raise RuntimeError("LabSession not open")
        scope: Oscilloscope = self._scope  # type: ignore[assignment]
        channel = channel or self.config.scope_channel

        if hasattr(scope, "enable_channel"):
            scope.enable_channel(channel)  # type: ignore[attr-defined]
        if self.config.scope_auto_setup:
            scope.auto_setup()

        slope = TriggerSlope.RISING if self.config.rising else TriggerSlope.FALLING
        scope.configure_trigger(
            TriggerConfig(source_channel=channel, slope=slope, level_v=self.config.trigger_level_v)
        )

        awg: DigitalStimulus | None = self._awg  # type: ignore[assignment]
        if awg is not None:
            awg.set_level(self.config.awg_channel, high=self.config.idle_high)
            time.sleep(self.config.edge_settle_s)
            scope.single_capture()
            awg.step_edge(self.config.awg_channel, rising=self.config.rising)
        else:
            # No stimulus device: arm and rely on an external/manual edge.
            scope.single_capture()

        return scope.capture_waveform(channel)

    def poll_dmm(self, n: int | None = None, settle_s: float | None = None) -> ScalarSeries | None:
        if self._dmm is None:
            return None
        reader: ScalarReader = self._dmm  # type: ignore[assignment]
        n = self.config.dmm_readings if n is None else n
        settle_s = self.config.dmm_settle_s if settle_s is None else settle_s

        t0 = datetime.now(timezone.utc)
        start = time.monotonic()
        ts: list[float] = []
        vals: list[float] = []
        flags: list[dict[str, bool]] = []
        unit = ""
        quantity = QuantityKind.UNKNOWN
        for i in range(max(1, n)):
            reading = reader.read()
            ts.append(time.monotonic() - start)
            vals.append(reading.value)
            flags.append(dict(reading.flags))
            unit = reading.unit or unit
            quantity = reading.quantity
            if settle_s and i < n - 1:
                time.sleep(settle_s)
        return ScalarSeries(
            t_rel_s=np.asarray(ts, dtype=float),
            values=np.asarray(vals, dtype=float),
            unit=unit,
            quantity=quantity,
            t0_utc=t0,
            flags=flags,
        )

    def provenance(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for role, inst in (("scope", self._scope), ("dmm", self._dmm), ("awg", self._awg)):
            if inst is not None:
                out.update(inst.info.as_provenance(role))
        return out

    def roles_map(self) -> dict[str, str | None]:
        return {role: self.bench.instrument_for_role(role) for role in ("scope", "dmm", "awg")}


def _tau_from_63pct(time_s: np.ndarray, voltage_v: np.ndarray, v0: float, v_final: float) -> float:
    """Estimate tau as the time to reach 63.2% of the step (numpy-only)."""
    target = v0 + 0.632 * (v_final - v0)
    if v_final >= v0:
        crossings = np.flatnonzero(voltage_v >= target)
    else:
        crossings = np.flatnonzero(voltage_v <= target)
    if crossings.size == 0:
        return float("nan")
    return float(time_s[crossings[0]] - time_s[0])


def fit_rc_step(time_s: np.ndarray, voltage_v: np.ndarray) -> dict[str, float]:
    """RC time constant from a step response.

    Uses scipy ``curve_fit`` when available; otherwise falls back to the
    dependency-free 63.2% rise-time estimate so the capture pipeline keeps
    working without scipy.
    """
    v_final = float(np.median(voltage_v[len(voltage_v) // 2:]))
    v0 = float(voltage_v[0])

    try:
        from scipy.optimize import curve_fit

        def rc(t, tau):
            return v_final - (v_final - v0) * np.exp(-t / tau)

        guess = _tau_from_63pct(time_s, voltage_v, v0, v_final)
        p0 = [guess if np.isfinite(guess) and guess > 0 else 1e-3]
        popt, _ = curve_fit(rc, time_s, voltage_v, p0=p0, maxfev=5000)
        tau = float(popt[0])
    except Exception:
        tau = _tau_from_63pct(time_s, voltage_v, v0, v_final)

    return {"tau_s": tau, "v_final": v_final, "v0": v0}


def capture_and_fit(
    session: LabSession,
    store: LabDataStore,
    *,
    component_ref: str,
    mpn: str,
    kicad_key: str | None = None,
    design: str | None = None,
    tags: list[str] | None = None,
) -> tuple[MeasuredParams, SessionMeta]:
    """Capture a step response (+ optional DMM steady), fit, and persist a session."""
    wf = session.capture_step_response()
    dmm_series = session.poll_dmm()

    derived = fit_rc_step(wf.time_s, wf.voltage_v)
    if dmm_series is not None and len(dmm_series):
        derived["dmm_steady"] = float(np.median(dmm_series.values))
        derived["dmm_readings_n"] = float(len(dmm_series))

    instruments = session.provenance()
    meta = store.write_session(
        component_ref=component_ref,
        mpn=mpn,
        kicad_key=kicad_key,
        design=design,
        waveforms={"scope_ch1": wf},
        scalar_series={"dmm": dmm_series} if dmm_series is not None else None,
        derived=derived,
        roles=session.roles_map(),
        instruments=instruments,
        tags=tags,
    )

    measured = MeasuredParams(
        component_ref=component_ref,
        mpn=mpn,
        captured_at=meta.captured_at.isoformat(),
        session_id=meta.session_id,
        instrument=instruments,
        params=derived,
    )
    return measured, meta
