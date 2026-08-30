"""Thermal-imager application layer: fixture identity, calibration hook, summaries."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from benchgate.instruments.types import Frame2D
from benchgate.lab.field2d import FrameGeometry, summarize
from benchgate.paths import benchgate_home


@dataclass(frozen=True)
class ThermalCalibration:
    kind: str
    slope: float = 1.0
    offset: float = 0.0
    ref_points: list[dict[str, Any]] = field(default_factory=list)
    emissivity: float = 1.0
    captured_at: datetime | None = None
    instrument_idn: str = ""

    def __post_init__(self) -> None:
        if self.kind not in {"none", "affine2pt"}:
            raise ValueError(f"unknown calibration kind {self.kind!r}")


def _quantize_distance_mm(distance_mm: float | None) -> str:
    """5 mm bins via floor (``int(d / 5) * 5``).

    Warmup and sub-bin distance scatter are capture timing / measurement
    error, not a different fixture. Floor (not round) keeps 12 mm and 14 mm
    in the same bin (10); 22 mm is bin 20.
    """
    if distance_mm is None:
        return "na"
    return str(int(float(distance_mm) // 5.0) * 5)


def fixture_id(
    *,
    instrument_idn: str,
    emissivity: float,
    warmup_s: float = 0.0,
    distance_mm: float | None = None,
    ambient_bin: str = "unknown",
) -> str:
    """Stable hash of the physical setup. Counts are only comparable inside one fixture.

    ``warmup_s`` stays on the signature so callers do not change, but it is
    **not** hashed: preheat is a capture-timing parameter, not fixture identity.
    ``distance_mm`` is quantized to 5 mm floor-bins before hashing.
    """
    del warmup_s  # accepted for API stability; not part of fixture identity
    payload = "|".join(
        [
            instrument_idn,
            f"e={float(emissivity):.4f}",
            f"d={_quantize_distance_mm(distance_mm)}",
            f"a={ambient_bin}",
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def fixture_id_hash_float(fid: str) -> float:
    """Pack the 12-hex fixture id into a derived float (lossy display only)."""
    return float(int(fid, 16))


def apply_calibration(frame: Frame2D, cal: ThermalCalibration) -> Frame2D:
    if cal.kind == "none":
        return frame
    if frame.unit != "count":
        raise ValueError(f"apply_calibration expects unit='count', got {frame.unit!r}")
    values = np.asarray(frame.values, dtype=float) * cal.slope + cal.offset
    return Frame2D(
        values=values,
        unit="degC",
        quantity=frame.quantity,
        timestamp=frame.timestamp,
        mask=frame.mask,
        calibration={
            "kind": cal.kind,
            "slope": cal.slope,
            "offset": cal.offset,
            "emissivity": cal.emissivity,
            "instrument_idn": cal.instrument_idn,
        },
        metadata=dict(frame.metadata),
    )


def affine_from_points(
    points: list[tuple[float, float]],
    *,
    instrument_idn: str = "",
    emissivity: float = 1.0,
) -> ThermalCalibration:
    """Two-point affine: count → degC. ``points`` are (count, degC)."""
    if len(points) < 2:
        raise ValueError("affine2pt needs at least two (count, degC) points")
    c0, t0 = points[0]
    c1, t1 = points[1]
    if c1 == c0:
        raise ValueError("calibration points have identical count")
    slope = (t1 - t0) / (c1 - c0)
    offset = t0 - slope * c0
    return ThermalCalibration(
        kind="affine2pt",
        slope=float(slope),
        offset=float(offset),
        ref_points=[{"count": float(c), "degc": float(t)} for c, t in points],
        captured_at=datetime.now(timezone.utc),
        instrument_idn=instrument_idn,
        emissivity=emissivity,
    )


def calibration_path(idn: str, *, home: Path | None = None) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-._" else "_" for ch in idn)[:80]
    return benchgate_home(home) / "config" / "thermal_cal" / f"{safe}.yaml"


def save_calibration(cal: ThermalCalibration, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "kind": cal.kind,
        "slope": cal.slope,
        "offset": cal.offset,
        "ref_points": list(cal.ref_points),
        "emissivity": cal.emissivity,
        "captured_at": cal.captured_at.isoformat() if cal.captured_at else None,
        "instrument_idn": cal.instrument_idn,
    }
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def load_calibration(path: Path) -> ThermalCalibration:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    captured = raw.get("captured_at")
    ts = datetime.fromisoformat(captured) if captured else None
    return ThermalCalibration(
        kind=str(raw.get("kind", "none")),
        slope=float(raw.get("slope", 1.0)),
        offset=float(raw.get("offset", 0.0)),
        ref_points=list(raw.get("ref_points") or []),
        emissivity=float(raw.get("emissivity", 1.0)),
        captured_at=ts,
        instrument_idn=str(raw.get("instrument_idn", "")),
    )


_THERMAL_PATH_KEYS = ("homography_file", "baseline_file")


def load_thermal_config(lab_yaml: Path | str | None) -> dict[str, Any]:
    """Read the optional ``thermal:`` block from ``<design>/models/lab.yaml``.

    Missing file or missing block → ``{}``. Path values are expanduser'd.
    Unknown keys are kept (callers ignore what they do not use).
    """
    if lab_yaml is None:
        return {}
    path = Path(lab_yaml).expanduser()
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return {}
    cfg = dict(raw.get("thermal") or {})
    for key in _THERMAL_PATH_KEYS:
        if cfg.get(key):
            cfg[key] = str(Path(str(cfg[key])).expanduser())
    return cfg


def apply_thermal_defaults(args: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    """Fill missing dispatch args from lab.yaml ``thermal:``. Existing keys win."""
    if not cfg:
        return args
    out = dict(args)
    for key in (
        "homography_file",
        "baseline_file",
        "delta_warn",
        "delta_fail",
        "k_sigma_warn",
        "k_sigma_fail",
        "min_area_px",
        "max_regions",
        "apply_calibration",
        "frames",
        "reduce",
        "session_tag",
    ):
        if out.get(key) is None and cfg.get(key) is not None:
            out[key] = cfg[key]
    return out


def baseline_path(fixture_id: str, *, home: Path | None = None) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-._" else "_" for ch in fixture_id)[:80]
    return benchgate_home(home) / "config" / "thermal_baseline" / f"{safe}.npz"


def save_baseline(values: np.ndarray, sigma: np.ndarray, meta: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        path,
        values=np.asarray(values, dtype=float),
        sigma=np.asarray(sigma, dtype=float),
    )
    sidecar = path.with_suffix(".yaml")
    sidecar.write_text(yaml.safe_dump(dict(meta), sort_keys=False), encoding="utf-8")


def load_baseline(path: Path) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    with np.load(path, allow_pickle=False) as npz:
        values = np.asarray(npz["values"], dtype=float)
        sigma = np.asarray(npz["sigma"], dtype=float)
    sidecar = path.with_suffix(".yaml")
    raw = yaml.safe_load(sidecar.read_text(encoding="utf-8")) if sidecar.is_file() else {}
    if not isinstance(raw, dict):
        raw = {}
    return values, sigma, raw


def homography_path(fixture_id: str, *, home: Path | None = None) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-._" else "_" for ch in fixture_id)[:80]
    return benchgate_home(home) / "config" / "thermal_map" / f"{safe}.yaml"


def save_homography(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def load_homography(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"invalid homography file {path}")
    return raw


def detect_four_hotspots(frame: Frame2D, *, threshold: float | None = None) -> list:
    from benchgate.lab.field2d import find_above

    vals = np.asarray(frame.values, dtype=float)
    if frame.mask is not None:
        vals = vals.copy()
        vals[np.asarray(frame.mask, dtype=bool)] = np.nan
    finite = vals[np.isfinite(vals)]
    if finite.size == 0:
        raise ValueError("no valid pixels in frame")

    def _at(thr: float):
        return [s for s in find_above(frame, thr, min_area_px=2, max_n=12) if s.area_px >= 2]

    if threshold is not None:
        spots = _at(float(threshold))
    else:
        spots = []
        for q in (99.5, 99.0, 98.5, 98.0, 97.0, 95.0, 92.0):
            spots = _at(float(np.nanpercentile(finite, q)))
            if len(spots) == 4:
                break
    if len(spots) != 4:
        raise ValueError(f"expected 4 registration blobs, got {len(spots)}")
    return spots


def register_rectangle(
    frame: Frame2D,
    *,
    length_mm: float,
    width_mm: float,
    threshold: float | None = None,
    fixture_id: str = "",
    session_id: str = "",
) -> dict[str, Any]:
    from benchgate.lab.board_map import (
        edge_px_per_mm,
        format_homography_pairs,
        homography_from_points,
        rectangle_dst_mm,
        reprojection_err_mm,
    )

    spots = detect_four_hotspots(frame, threshold=threshold)
    src = np.array([(s.centroid_x, s.centroid_y) for s in spots], dtype=float)
    src, dst = rectangle_dst_mm(src, length_mm=length_mm, width_mm=width_mm)
    h = homography_from_points(src, dst)
    px_x, px_y = edge_px_per_mm(src, dst)
    err = reprojection_err_mm(h, src, dst)
    warn = min(px_x, px_y) < 1.0
    pairs = format_homography_pairs(src, dst)
    return {
        "kind": "homography4",
        "fixture_id": fixture_id,
        "session_id": session_id,
        "length_mm": float(length_mm),
        "width_mm": float(width_mm),
        "src_px": src.tolist(),
        "dst_mm": dst.tolist(),
        "matrix": h.tolist(),
        "pairs": pairs,
        "px_per_mm": [px_x, px_y],
        "reprojection_err_mm": err,
        "reprojection_err_mm_max": float(max(err) if err else 0.0),
        "coord_frame": "kicad_grid",
        "origin": (
            "KiCad grid origin: image bottom-left blob is (0,0). "
            f"Corner 1 maps to {dst[1].tolist()} mm, corner 3 to {dst[3].tolist()} mm. "
            f"length_mm={float(length_mm)} is the longer pixel edge from that origin; "
            f"width_mm={float(width_mm)} is the shorter. +Y is KiCad-down "
            "(top of the thermal image is negative Y)."
        ),
        "coarse_warning": (
            "32x32 over this rectangle is coarser than 1 px/mm; small footprints are candidates only"
            if warn
            else ""
        ),
        "spots": [
            {
                "x": s.x,
                "y": s.y,
                "peak_x": s.peak_x,
                "peak_y": s.peak_y,
                "centroid_x": s.centroid_x,
                "centroid_y": s.centroid_y,
                "row": s.row,
                "col": s.col,
                "value": s.value,
                "area_px": s.area_px,
            }
            for s in spots
        ],
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


def summarize_thermal(
    frame: Frame2D,
    *,
    geometry: FrameGeometry | None = None,
    threshold: float | None = None,
    instrument_idn: str = "",
    emissivity: float = 1.0,
    warmup_s: float = 0.0,
    distance_mm: float | None = None,
    ambient_bin: str = "unknown",
    known_fixture_id: str | None = None,
) -> dict[str, float]:
    derived = summarize(frame, geometry=geometry, threshold=threshold)
    fid = known_fixture_id or fixture_id(
        instrument_idn=instrument_idn,
        emissivity=emissivity,
        warmup_s=warmup_s,
        distance_mm=distance_mm,
        ambient_bin=ambient_bin,
    )
    derived["fixture_id_hash"] = fixture_id_hash_float(fid)
    derived["emissivity"] = float(emissivity)
    derived["warmup_s"] = float(warmup_s)
    return derived
