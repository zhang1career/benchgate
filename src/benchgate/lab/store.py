"""S0 lab data store: session-oriented local persistence and time-axis queries.

One acquisition = one **Session** directory under ``<design>/models/captured/``::

    sessions/<session_id>/
        session.yaml      # metadata: time, component, roles, channel manifest
        <channel>.npz     # waveform payload (time_s, voltage_v[, raw_adc])
        <channel>.csv     # scalar time series (t_rel_s,value,unit,flags)
        derived.json      # fitted metrics / statistics

Waveforms use NPZ (compact, fast); scalar series use CSV. The store answers two
kinds of time-axis query:

  * within one acquisition  -> ``load_waveform`` / ``load_scalar_series`` (+ window)
  * across acquisitions      -> ``list_sessions`` / ``metric_series``

This is the S0 layer: plain files, no database. A catalog index (jsonl/DuckDB)
can be layered on later without changing this API.
"""

from __future__ import annotations

import csv
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from collections.abc import Callable
from typing import Any

import numpy as np
import yaml

from benchgate.instruments.types import Frame2D, Frame2DSeries, QuantityKind, ScalarSeries, Spectrum, Waveform

_TS_FMT = "%Y%m%dT%H%M%SZ"


def new_session_id(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"{now.strftime(_TS_FMT)}_{secrets.token_hex(2)}"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(text: str) -> datetime:
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass
class ChannelMeta:
    name: str
    kind: str  # "waveform" | "scalar_series" | "spectrum"
    path: str
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionMeta:
    session_id: str
    captured_at: datetime
    design: str | None = None
    component_ref: str | None = None
    kicad_key: str | None = None
    mpn: str | None = None
    tags: list[str] = field(default_factory=list)
    roles: dict[str, str | None] = field(default_factory=dict)
    instruments: dict[str, str] = field(default_factory=dict)
    channels: list[ChannelMeta] = field(default_factory=list)
    derived: dict[str, float] = field(default_factory=dict)
    notes: str = ""
    path: Path | None = None

    def channel(self, name: str) -> ChannelMeta | None:
        for ch in self.channels:
            if ch.name == name:
                return ch
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "captured_at": _iso(self.captured_at),
            "design": self.design,
            "component_ref": self.component_ref,
            "kicad_key": self.kicad_key,
            "mpn": self.mpn,
            "tags": list(self.tags),
            "roles": dict(self.roles),
            "instruments": dict(self.instruments),
            "channels": [
                {"name": c.name, "kind": c.kind, "path": c.path, **({"extra": c.extra} if c.extra else {})}
                for c in self.channels
            ],
            "derived": dict(self.derived),
            "notes": self.notes,
        }


def _meta_from_dict(d: dict[str, Any], path: Path) -> SessionMeta:
    channels = [
        ChannelMeta(name=c["name"], kind=c["kind"], path=c["path"], extra=c.get("extra", {}))
        for c in d.get("channels", [])
    ]
    return SessionMeta(
        session_id=d["session_id"],
        captured_at=_parse_iso(d["captured_at"]),
        design=d.get("design"),
        component_ref=d.get("component_ref"),
        kicad_key=d.get("kicad_key"),
        mpn=d.get("mpn"),
        tags=list(d.get("tags", [])),
        roles=dict(d.get("roles", {})),
        instruments=dict(d.get("instruments", {})),
        channels=channels,
        derived=dict(d.get("derived", {})),
        notes=d.get("notes", ""),
        path=path,
    )


def _flags_to_str(flags: dict[str, bool]) -> str:
    return "|".join(k for k, v in flags.items() if v)


@dataclass(frozen=True)
class ChannelKind:
    name: str
    write: Callable[[Path, str, Any], ChannelMeta]
    load: Callable[[Path, ChannelMeta, SessionMeta], Any]


CHANNEL_KINDS: dict[str, ChannelKind] = {}


def register_channel_kind(kind: ChannelKind) -> None:
    CHANNEL_KINDS[kind.name] = kind


def _write_waveform(sdir: Path, name: str, wf: Waveform) -> ChannelMeta:
    fname = f"{name}.npz"
    payload = {"time_s": wf.time_s, "voltage_v": wf.voltage_v}
    if wf.raw_adc is not None:
        payload["raw_adc"] = wf.raw_adc
    np.savez_compressed(sdir / fname, **payload)
    return ChannelMeta(
        name=name,
        kind="waveform",
        path=fname,
        extra={
            "channel": wf.channel,
            "sample_rate_hz": wf.sample_rate_hz,
            "t0_utc": _iso(wf.timestamp),
            "n": len(wf),
        },
    )


def _load_waveform_kind(sdir: Path, ch: ChannelMeta, meta: SessionMeta) -> Waveform:
    with np.load(sdir / ch.path) as npz:
        t = npz["time_s"]
        v = npz["voltage_v"]
        raw = npz["raw_adc"] if "raw_adc" in npz else None
    return Waveform(
        time_s=t,
        voltage_v=v,
        channel=int(ch.extra.get("channel", 1)),
        timestamp=_parse_iso(ch.extra.get("t0_utc", _iso(meta.captured_at))),
        sample_rate_hz=ch.extra.get("sample_rate_hz"),
        raw_adc=raw,
    )


def _write_spectrum(sdir: Path, name: str, spec: Spectrum) -> ChannelMeta:
    fname = f"{name}.npz"
    np.savez_compressed(sdir / fname, freq_hz=spec.freq_hz, amplitude_dbm=spec.amplitude_dbm)
    return ChannelMeta(
        name=name,
        kind="spectrum",
        path=fname,
        extra={
            "trace": spec.trace,
            "t0_utc": _iso(spec.timestamp),
            "n": len(spec),
            **({k: v for k, v in spec.metadata.items()} if spec.metadata else {}),
        },
    )


def _load_spectrum_kind(sdir: Path, ch: ChannelMeta, meta: SessionMeta) -> Spectrum:
    with np.load(sdir / ch.path) as npz:
        f = npz["freq_hz"]
        a = npz["amplitude_dbm"]
    extra = dict(ch.extra)
    extra.pop("trace", None)
    extra.pop("t0_utc", None)
    extra.pop("n", None)
    return Spectrum(
        freq_hz=f,
        amplitude_dbm=a,
        timestamp=_parse_iso(ch.extra.get("t0_utc", _iso(meta.captured_at))),
        trace=str(ch.extra.get("trace", "current")),
        metadata=extra,
    )


def _write_scalar_series_kind(sdir: Path, name: str, series: ScalarSeries) -> ChannelMeta:
    fname = f"{name}.csv"
    LabDataStore._write_scalar_csv(sdir / fname, series)
    return ChannelMeta(
        name=name,
        kind="scalar_series",
        path=fname,
        extra={
            "unit": series.unit,
            "quantity": series.quantity.value,
            "t0_utc": _iso(series.t0_utc),
            "n": len(series),
        },
    )


def _as_frame_series(obj: Frame2D | Frame2DSeries) -> Frame2DSeries:
    if isinstance(obj, Frame2DSeries):
        return obj
    return Frame2DSeries(
        t_rel_s=np.asarray([0.0], dtype=float),
        values=np.asarray(obj.values)[np.newaxis, ...],
        unit=obj.unit,
        quantity=obj.quantity,
        t0_utc=obj.timestamp,
        mask=obj.mask,
        calibration=obj.calibration,
        metadata=dict(obj.metadata),
    )


_FRAME2D_META_SKIP = frozenset(
    {
        "unit",
        "quantity",
        "t0_utc",
        "n",
        "height",
        "width",
        "calibration_kind",
        "calibration_slope",
        "calibration_offset",
        "calibration_emissivity",
        "calibration_instrument_idn",
    }
)


def _flatten_calibration(cal: dict[str, Any] | None) -> dict[str, Any]:
    if not cal:
        return {"calibration_kind": "none"}
    extra: dict[str, Any] = {"calibration_kind": cal.get("kind", "none")}
    for key in ("slope", "offset", "emissivity", "instrument_idn"):
        val = cal.get(key)
        if isinstance(val, (str, int, float, bool)):
            extra[f"calibration_{key}"] = val
    return extra


def _calibration_from_extra(extra: dict[str, Any]) -> dict[str, Any] | None:
    kind = extra.get("calibration_kind", "none")
    if kind in (None, "none"):
        return None
    cal: dict[str, Any] = {"kind": kind}
    for key in ("slope", "offset", "emissivity", "instrument_idn"):
        field = f"calibration_{key}"
        if field in extra:
            cal[key] = extra[field]
    return cal


def _write_frame2d(sdir: Path, name: str, obj: Frame2D | Frame2DSeries) -> ChannelMeta:
    series = _as_frame_series(obj)
    fname = f"{name}.npz"
    payload: dict[str, Any] = {
        "values": series.values,
        "t_rel_s": series.t_rel_s,
    }
    if series.mask is not None:
        payload["mask"] = series.mask
    np.savez_compressed(sdir / fname, **payload)
    extra: dict[str, Any] = {
        "unit": series.unit,
        "quantity": series.quantity.value,
        "t0_utc": _iso(series.t0_utc),
        "n": len(series),
        "height": series.height,
        "width": series.width,
    }
    extra.update(_flatten_calibration(series.calibration))
    extra.update({k: v for k, v in series.metadata.items() if isinstance(v, (str, int, float, bool))})
    return ChannelMeta(name=name, kind="frame2d", path=fname, extra=extra)


def _load_frame2d_kind(sdir: Path, ch: ChannelMeta, meta: SessionMeta) -> Frame2DSeries:
    with np.load(sdir / ch.path, allow_pickle=False) as npz:
        values = npz["values"]
        t_rel = npz["t_rel_s"] if "t_rel_s" in npz else np.arange(values.shape[0], dtype=float)
        mask = npz["mask"] if "mask" in npz else None
    if values.ndim == 2:
        values = values[np.newaxis, ...]
        t_rel = np.asarray([0.0], dtype=float)
    return Frame2DSeries(
        t_rel_s=np.asarray(t_rel, dtype=float),
        values=np.asarray(values),
        unit=str(ch.extra.get("unit", "count")),
        quantity=QuantityKind(ch.extra.get("quantity", "temperature")),
        t0_utc=_parse_iso(ch.extra.get("t0_utc", _iso(meta.captured_at))),
        mask=None if mask is None else np.asarray(mask, dtype=bool),
        calibration=_calibration_from_extra(ch.extra),
        metadata={k: v for k, v in ch.extra.items() if k not in _FRAME2D_META_SKIP},
    )


def _load_scalar_series_kind(sdir: Path, ch: ChannelMeta, meta: SessionMeta) -> None:
    raise NotImplementedError("scalar_series load goes through LabDataStore.load_scalar_series")


register_channel_kind(ChannelKind("waveform", _write_waveform, _load_waveform_kind))
register_channel_kind(ChannelKind("spectrum", _write_spectrum, _load_spectrum_kind))
register_channel_kind(ChannelKind("scalar_series", _write_scalar_series_kind, _load_scalar_series_kind))
register_channel_kind(ChannelKind("frame2d", _write_frame2d, _load_frame2d_kind))


class LabDataStore:
    def __init__(self, captured_root: Path) -> None:
        self.root = Path(captured_root)
        self.sessions_dir = self.root / "sessions"

    # --- write ---

    def write_session(
        self,
        *,
        component_ref: str | None = None,
        mpn: str | None = None,
        kicad_key: str | None = None,
        design: str | None = None,
        waveforms: dict[str, Waveform] | None = None,
        spectra: dict[str, Spectrum] | None = None,
        scalar_series: dict[str, ScalarSeries] | None = None,
        frames: dict[str, Frame2D | Frame2DSeries] | None = None,
        payloads: dict[str, tuple[str, Any]] | None = None,
        derived: dict[str, float] | None = None,
        roles: dict[str, str | None] | None = None,
        instruments: dict[str, str] | None = None,
        tags: list[str] | None = None,
        notes: str = "",
        session_id: str | None = None,
        captured_at: datetime | None = None,
    ) -> SessionMeta:
        captured_at = captured_at or datetime.now(timezone.utc)
        sid = session_id or new_session_id(captured_at)
        sdir = self.sessions_dir / sid
        sdir.mkdir(parents=True, exist_ok=True)

        channels: list[ChannelMeta] = []
        grouped: list[tuple[str, dict[str, Any]]] = [
            ("waveform", waveforms or {}),
            ("spectrum", spectra or {}),
            ("scalar_series", scalar_series or {}),
            ("frame2d", frames or {}),
        ]
        for kind_name, items in grouped:
            handler = CHANNEL_KINDS[kind_name]
            for name, payload in items.items():
                channels.append(handler.write(sdir, name, payload))
        for name, (kind_name, payload) in (payloads or {}).items():
            handler = CHANNEL_KINDS.get(kind_name)
            if handler is None:
                raise KeyError(f"Unknown channel kind {kind_name!r}; registered: {sorted(CHANNEL_KINDS)}")
            channels.append(handler.write(sdir, name, payload))

        derived = derived or {}
        if derived:
            (sdir / "derived.json").write_text(json.dumps(derived, indent=2), encoding="utf-8")

        meta = SessionMeta(
            session_id=sid,
            captured_at=captured_at,
            design=design,
            component_ref=component_ref,
            kicad_key=kicad_key,
            mpn=mpn,
            tags=tags or [],
            roles=roles or {},
            instruments=instruments or {},
            channels=channels,
            derived={k: float(v) for k, v in derived.items()},
            notes=notes,
            path=sdir,
        )
        (sdir / "session.yaml").write_text(
            yaml.safe_dump(meta.to_dict(), sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        return meta

    @staticmethod
    def _write_scalar_csv(path: Path, series: ScalarSeries) -> None:
        with path.open("w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["t_rel_s", "value", "unit", "flags"])
            flags = series.flags or [{}] * len(series)
            for i in range(len(series)):
                fl = flags[i] if i < len(flags) else {}
                writer.writerow(
                    [f"{float(series.t_rel_s[i]):.9g}", f"{float(series.values[i]):.9g}", series.unit, _flags_to_str(fl)]
                )

    # --- read / query ---

    def get_session(self, session_id: str) -> SessionMeta:
        path = self.sessions_dir / session_id / "session.yaml"
        if not path.exists():
            raise FileNotFoundError(f"No session {session_id!r} at {path}")
        return _meta_from_dict(yaml.safe_load(path.read_text()), path.parent)

    def list_sessions(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        component_ref: str | None = None,
        tags: list[str] | None = None,
        role_instrument: tuple[str, str] | None = None,
    ) -> list[SessionMeta]:
        if not self.sessions_dir.exists():
            return []
        out: list[SessionMeta] = []
        for child in self.sessions_dir.iterdir():
            meta_path = child / "session.yaml"
            if not meta_path.is_file():
                continue
            try:
                meta = _meta_from_dict(yaml.safe_load(meta_path.read_text()), child)
            except Exception:
                continue
            if since and meta.captured_at < since:
                continue
            if until and meta.captured_at > until:
                continue
            if component_ref and meta.component_ref != component_ref:
                continue
            if tags and not set(tags).issubset(set(meta.tags)):
                continue
            if role_instrument and meta.roles.get(role_instrument[0]) != role_instrument[1]:
                continue
            out.append(meta)
        out.sort(key=lambda m: m.captured_at)
        return out

    def load_waveform(
        self,
        session_id: str,
        channel: str = "scope_ch1",
        *,
        t_start: float | None = None,
        t_end: float | None = None,
    ) -> Waveform:
        meta = self.get_session(session_id)
        ch = meta.channel(channel)
        if ch is None or ch.kind != "waveform":
            raise KeyError(f"No waveform channel {channel!r} in session {session_id!r}")
        wf = CHANNEL_KINDS["waveform"].load(meta.path, ch, meta)
        if t_start is not None or t_end is not None:
            lo = t_start if t_start is not None else -np.inf
            hi = t_end if t_end is not None else np.inf
            mask = (wf.time_s >= lo) & (wf.time_s <= hi)
            raw = wf.raw_adc[mask] if wf.raw_adc is not None else None
            wf = Waveform(
                time_s=wf.time_s[mask],
                voltage_v=wf.voltage_v[mask],
                channel=wf.channel,
                timestamp=wf.timestamp,
                sample_rate_hz=wf.sample_rate_hz,
                raw_adc=raw,
            )
        return wf

    def load_spectrum(
        self,
        session_id: str,
        channel: str = "sa_trace",
        *,
        f_start_hz: float | None = None,
        f_end_hz: float | None = None,
    ) -> Spectrum:
        meta = self.get_session(session_id)
        ch = meta.channel(channel)
        if ch is None or ch.kind != "spectrum":
            raise KeyError(f"No spectrum channel {channel!r} in session {session_id!r}")
        spec = CHANNEL_KINDS["spectrum"].load(meta.path, ch, meta)
        if f_start_hz is not None or f_end_hz is not None:
            lo = f_start_hz if f_start_hz is not None else -np.inf
            hi = f_end_hz if f_end_hz is not None else np.inf
            mask = (spec.freq_hz >= lo) & (spec.freq_hz <= hi)
            spec = Spectrum(
                freq_hz=spec.freq_hz[mask],
                amplitude_dbm=spec.amplitude_dbm[mask],
                timestamp=spec.timestamp,
                trace=spec.trace,
                metadata=spec.metadata,
            )
        return spec

    def load_frame2d(
        self,
        session_id: str,
        channel: str = "thermal",
        *,
        index: int = 0,
    ) -> Frame2D:
        series = self.load_frame2d_series(session_id, channel)
        if index < 0 or index >= len(series):
            raise IndexError(f"frame index {index} out of range for {len(series)} frames")
        return series.frame(index)

    def load_frame2d_series(self, session_id: str, channel: str = "thermal") -> Frame2DSeries:
        meta = self.get_session(session_id)
        ch = meta.channel(channel)
        if ch is None or ch.kind != "frame2d":
            raise KeyError(f"No frame2d channel {channel!r} in session {session_id!r}")
        return CHANNEL_KINDS["frame2d"].load(meta.path, ch, meta)

    def load_scalar_series(
        self,
        session_id: str,
        channel: str = "dmm",
        *,
        t_start: float | None = None,
        t_end: float | None = None,
    ) -> ScalarSeries:
        meta = self.get_session(session_id)
        ch = meta.channel(channel)
        if ch is None or ch.kind != "scalar_series":
            raise KeyError(f"No scalar channel {channel!r} in session {session_id!r}")
        ts: list[float] = []
        vals: list[float] = []
        flags: list[dict[str, bool]] = []
        unit = ch.extra.get("unit", "")
        with (meta.path / ch.path).open(newline="") as fh:
            for row in csv.DictReader(fh):
                t = float(row["t_rel_s"])
                if t_start is not None and t < t_start:
                    continue
                if t_end is not None and t > t_end:
                    continue
                ts.append(t)
                vals.append(float(row["value"]))
                unit = row.get("unit") or unit
                flags.append({k: True for k in (row.get("flags") or "").split("|") if k})
        quantity = QuantityKind(ch.extra.get("quantity", "unknown"))
        return ScalarSeries(
            t_rel_s=np.asarray(ts, dtype=float),
            values=np.asarray(vals, dtype=float),
            unit=unit,
            quantity=quantity,
            t0_utc=_parse_iso(ch.extra.get("t0_utc", _iso(meta.captured_at))),
            flags=flags,
        )

    def metric_series(
        self,
        metric: str,
        *,
        component_ref: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Pull one derived metric across sessions, ordered by capture time."""
        rows: list[dict[str, Any]] = []
        for meta in self.list_sessions(component_ref=component_ref, since=since, until=until):
            if metric in meta.derived:
                rows.append(
                    {
                        "captured_at": meta.captured_at,
                        "session_id": meta.session_id,
                        "component_ref": meta.component_ref,
                        "value": meta.derived[metric],
                    }
                )
        return rows


def dump_spectrum_csv(spec: Spectrum) -> str:
    """Render a spectrum as 2-column CSV text (export helper, not the store format)."""
    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(["freq_hz", "amplitude_dbm"])
    for f, a in zip(spec.freq_hz.tolist(), spec.amplitude_dbm.tolist()):
        writer.writerow([f"{f:.9g}", f"{a:.9g}"])
    return buf.getvalue()


def dump_waveform_csv(wf: Waveform) -> str:
    """Render a waveform as 2-column CSV text (export helper, not the store format)."""
    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(["time_s", "voltage_v"])
    for t, v in zip(wf.time_s.tolist(), wf.voltage_v.tolist()):
        writer.writerow([f"{t:.9g}", f"{v:.9g}"])
    return buf.getvalue()
