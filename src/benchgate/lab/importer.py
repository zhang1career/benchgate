"""Bring measurements that benchgate did not capture into a Session.

Scope CSV exports, readings copied off a meter, screenshots: each import is one
Session under ``<design>/models/captured/sessions/`` and one line in the record
ledger, the same as a capture benchgate drove itself.

CSV waveforms: rows whose fields are all numbers are data; the first column is
time, every further column is one channel. The last text row with the same
column count above the data names the channels. The original CSV is kept as an
attachment because vendor formats vary and this parser keeps only numbers.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from benchgate.instruments.types import QuantityKind, ScalarSeries, Waveform

_VALUE_RE = re.compile(r"^\s*([A-Za-z_][\w.\-]*)\s*=\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*(\S*)\s*$")
_CHANNEL_SAFE = re.compile(r"[^\w\-]+")

_UNIT_QUANTITY = {
    "v": QuantityKind.VOLTAGE,
    "a": QuantityKind.CURRENT,
    "ω": QuantityKind.RESISTANCE,
    "ohm": QuantityKind.RESISTANCE,
    "f": QuantityKind.CAPACITANCE,
    "hz": QuantityKind.FREQUENCY,
    "%": QuantityKind.PERCENT,
    "°c": QuantityKind.TEMPERATURE,
    "degc": QuantityKind.TEMPERATURE,
    "c": QuantityKind.TEMPERATURE,
    "rpm": QuantityKind.RPM,
}
_PREFIXES = ("p", "n", "u", "µ", "m", "k", "M", "G")


class LabImportError(ValueError):
    """The input cannot be imported as given."""


@dataclass
class ParsedValue:
    name: str
    value: float
    unit: str


def parse_value(text: str) -> ParsedValue:
    """``vout=23.6V`` → name, number, unit (unit as written; no prefix scaling)."""
    match = _VALUE_RE.match(text)
    if not match:
        raise LabImportError(f"读数格式应为 名字=数值[单位]，例如 vout=23.6V：{text!r}")
    return ParsedValue(match.group(1), float(match.group(2)), match.group(3))


def quantity_for_unit(unit: str) -> QuantityKind:
    key = unit.strip()
    if not key:
        return QuantityKind.UNKNOWN
    lowered = key.lower()
    if lowered in _UNIT_QUANTITY:
        return _UNIT_QUANTITY[lowered]
    if len(key) > 1 and key[0] in _PREFIXES and key[1:].lower() in _UNIT_QUANTITY:
        return _UNIT_QUANTITY[key[1:].lower()]
    return QuantityKind.UNKNOWN


def _sniff_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t ").delimiter
    except csv.Error:
        return ","


def _floats(row: list[str]) -> list[float] | None:
    cells = [c.strip() for c in row]
    while cells and cells[-1] == "":
        cells.pop()
    if len(cells) < 2:
        return None
    out = []
    for cell in cells:
        try:
            out.append(float(cell))
        except ValueError:
            return None
    return out


def read_waveform_csv(
    path: Path,
    *,
    time_scale: float = 1.0,
    sample_interval_s: float | None = None,
) -> tuple[list[str], np.ndarray, np.ndarray]:
    """Return ``(channel names, time_s, values[n_samples, n_channels])``.

    ``sample_interval_s`` replaces the first column with ``i * interval`` (for
    exports whose first column is a sample index).
    """
    text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    delimiter = _sniff_delimiter(text[:4096])
    rows = list(csv.reader(text.splitlines(), delimiter=delimiter, skipinitialspace=True))

    data: list[list[float]] = []
    width = None
    header: list[str] | None = None
    last_text: list[str] | None = None
    for row in rows:
        nums = _floats(row)
        if nums is None:
            if not data:
                cells = [c.strip() for c in row]
                while cells and cells[-1] == "":
                    cells.pop()
                if cells:
                    last_text = cells
            continue
        if width is None:
            width = len(nums)
            if last_text is not None and len(last_text) >= width:
                header = last_text[:width]
        if len(nums) >= width:
            data.append(nums[:width])
    if not data or width is None:
        raise LabImportError(f"{path} 里没有找到至少两列的数字行")

    arr = np.asarray(data, dtype=float)
    if sample_interval_s is not None:
        time_s = np.arange(arr.shape[0], dtype=float) * float(sample_interval_s)
    else:
        time_s = arr[:, 0] * float(time_scale)
    names = header[1:] if header else [f"ch{i}" for i in range(1, width)]
    return names, time_s, arr[:, 1:]


def _channel_name(stem: str, column: str, used: set[str]) -> str:
    base = _CHANNEL_SAFE.sub("_", f"{stem}_{column}").strip("_") or "ch"
    name, n = base, 2
    while name in used:
        name = f"{base}_{n}"
        n += 1
    used.add(name)
    return name


def _sample_rate(time_s: np.ndarray) -> float | None:
    if time_s.size < 2:
        return None
    dt = np.diff(time_s)
    step = float(np.median(dt))
    return 1.0 / step if step > 0 else None


def import_session(
    captured_root: Path,
    *,
    design: str | None = None,
    waveforms: list[Path | str] | None = None,
    values: list[str] | None = None,
    files: list[Path | str] | None = None,
    instrument: str | None = None,
    component_ref: str | None = None,
    tags: list[str] | None = None,
    notes: str = "",
    captured_at: datetime | str | None = None,
    time_scale: float = 1.0,
    sample_interval_s: float | None = None,
) -> dict[str, Any]:
    from benchgate.lab.store import LabDataStore

    waveforms = [Path(p).expanduser() for p in (waveforms or [])]
    files = [Path(p).expanduser() for p in (files or [])]
    values = list(values or [])
    if not (waveforms or values or files):
        raise LabImportError("至少给一个波形 CSV、读数或附件")
    for path in (*waveforms, *files):
        if not path.is_file():
            raise LabImportError(f"文件不存在：{path}")

    if isinstance(captured_at, str):
        at = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        at = at if at.tzinfo else at.astimezone()
    else:
        at = captured_at or datetime.now(timezone.utc)

    waves: dict[str, Waveform] = {}
    used: set[str] = set()
    channel_index = 1
    for path in waveforms:
        names, time_s, matrix = read_waveform_csv(path, time_scale=time_scale, sample_interval_s=sample_interval_s)
        rate = _sample_rate(time_s)
        for col, column in enumerate(names):
            ys = matrix[:, col]
            keep = np.isfinite(time_s) & np.isfinite(ys)
            name = _channel_name(path.stem, column, used)
            waves[name] = Waveform(
                time_s=time_s[keep],
                voltage_v=ys[keep],
                channel=channel_index,
                timestamp=at,
                sample_rate_hz=rate,
            )
            channel_index += 1

    series: dict[str, ScalarSeries] = {}
    derived: dict[str, float] = {}
    for text in values:
        parsed = parse_value(text)
        if parsed.name in series:
            raise LabImportError(f"读数名字重复：{parsed.name}")
        series[parsed.name] = ScalarSeries(
            t_rel_s=np.asarray([0.0]),
            values=np.asarray([parsed.value]),
            unit=parsed.unit,
            quantity=quantity_for_unit(parsed.unit),
            t0_utc=at,
        )
        derived[parsed.name] = parsed.value

    source: dict[str, str] = {"driver": "import"}
    if instrument:
        source["description"] = instrument
    meta = LabDataStore(Path(captured_root)).write_session(
        component_ref=component_ref,
        design=design,
        waveforms=waves or None,
        scalar_series=series or None,
        derived=derived or None,
        instruments={"imported": source},
        tags=sorted({"import", *(tags or [])}),
        notes=notes,
        captured_at=at,
        attachments=[*waveforms, *files],
    )
    return {
        "session_id": meta.session_id,
        "path": str(meta.path),
        "channels": [c.name for c in meta.channels],
        "derived": meta.derived,
        "attachments": meta.attachments,
        "captured_at": meta.to_dict()["captured_at"],
    }
