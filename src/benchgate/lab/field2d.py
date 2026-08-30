"""Device-agnostic analysis of regular 2-D scalar fields."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from benchgate.instruments.types import Frame2D


@dataclass(frozen=True)
class FrameGeometry:
    """Pixel-index → user coordinate mapping."""

    origin: str = "top_left"
    x_scale: float = 1.0
    y_scale: float = 1.0
    unit: str = "px"
    flip_x: bool = False
    flip_y: bool = False
    rotate_quadrants: int = 0

    def __post_init__(self) -> None:
        if self.origin not in {"top_left", "bottom_left", "center"}:
            raise ValueError(f"unknown origin {self.origin!r}")
        object.__setattr__(self, "rotate_quadrants", int(self.rotate_quadrants) % 4)


@dataclass(frozen=True)
class Hotspot:
    """A local maximum. ``x``/``y`` are the peak in user coordinates.

    ``centroid_x``/``centroid_y`` are the region centroid (used for blob
    registration). For a single pixel they match the peak.
    """

    row: int
    col: int
    x: float
    y: float
    value: float
    unit: str
    area_px: int = 1
    rank: int = 0
    peak_x: float = 0.0
    peak_y: float = 0.0
    centroid_x: float = 0.0
    centroid_y: float = 0.0


def _rotate_coord(
    row: float, col: float, height: int, width: int, quadrants: int
) -> tuple[float, float, int, int]:
    r, c, h, w = float(row), float(col), int(height), int(width)
    for _ in range(quadrants % 4):
        r, c, h, w = c, (h - 1) - r, w, h
    return r, c, h, w


def pixel_to_user(row: float, col: float, height: int, width: int, geometry: FrameGeometry) -> tuple[float, float]:
    r, c, h, w = _rotate_coord(row, col, height, width, geometry.rotate_quadrants)
    if geometry.flip_x:
        c = (w - 1) - c
    if geometry.flip_y:
        r = (h - 1) - r
    if geometry.origin == "top_left":
        x, y = c * geometry.x_scale, r * geometry.y_scale
    elif geometry.origin == "bottom_left":
        x, y = c * geometry.x_scale, ((h - 1) - r) * geometry.y_scale
    else:
        x = (c - (w - 1) / 2.0) * geometry.x_scale
        y = ((h - 1) / 2.0 - r) * geometry.y_scale
    return float(x), float(y)


def _valid_values(frame: Frame2D) -> np.ndarray:
    vals = np.asarray(frame.values, dtype=float).copy()
    if frame.mask is not None:
        vals[np.asarray(frame.mask, dtype=bool)] = np.nan
    return vals


def find_max(frame: Frame2D, *, geometry: FrameGeometry | None = None, subpixel: bool = False) -> Hotspot:
    geometry = geometry or FrameGeometry()
    vals = _valid_values(frame)
    if not np.isfinite(vals).any():
        raise ValueError("no valid pixels in frame")
    flat = np.nanargmax(vals)
    row, col = np.unravel_index(int(flat), vals.shape)
    value = float(vals[row, col])
    pr, pc = float(row), float(col)
    if subpixel:
        pr, pc = _subpixel(vals, int(row), int(col))
    x, y = pixel_to_user(pr, pc, frame.height, frame.width, geometry)
    return Hotspot(
        row=int(row),
        col=int(col),
        x=x,
        y=y,
        value=value,
        unit=frame.unit,
        area_px=1,
        rank=0,
        peak_x=x,
        peak_y=y,
        centroid_x=x,
        centroid_y=y,
    )


def _subpixel(vals: np.ndarray, row: int, col: int) -> tuple[float, float]:
    r0, r1 = max(0, row - 1), min(vals.shape[0], row + 2)
    c0, c1 = max(0, col - 1), min(vals.shape[1], col + 2)
    patch = vals[r0:r1, c0:c1]
    weight = np.where(np.isfinite(patch), np.clip(patch - np.nanmin(patch) + 1e-9, 0, None), 0.0)
    total = float(weight.sum())
    if total <= 0:
        return float(row), float(col)
    rr = np.arange(r0, r1)[:, None]
    cc = np.arange(c0, c1)[None, :]
    return float((rr * weight).sum() / total), float((cc * weight).sum() / total)


def find_above_array(
    values: np.ndarray,
    threshold: float,
    *,
    mask: np.ndarray | None = None,
    geometry: FrameGeometry | None = None,
    min_area_px: int = 1,
    max_n: int = 10,
    unit: str = "count",
) -> list[Hotspot]:
    """8-connected blobs on a raw 2-D array (no ``Frame2D`` unit constraints)."""
    geometry = geometry or FrameGeometry()
    vals = np.asarray(values, dtype=float).copy()
    if mask is not None:
        vals[np.asarray(mask, dtype=bool)] = np.nan
    height, width = int(vals.shape[0]), int(vals.shape[1])
    valid = np.isfinite(vals) & (vals >= float(threshold))
    if not valid.any():
        return []
    labeled, nlab = ndimage.label(valid, structure=np.ones((3, 3), dtype=int))
    spots: list[Hotspot] = []
    for lab in range(1, nlab + 1):
        region = labeled == lab
        area = int(region.sum())
        if area < min_area_px:
            continue
        region_vals = np.where(region, vals, np.nan)
        flat = int(np.nanargmax(region_vals))
        row, col = np.unravel_index(flat, vals.shape)
        ys, xs = np.nonzero(region)
        cy, cx = float(ys.mean()), float(xs.mean())
        peak_x, peak_y = pixel_to_user(float(row), float(col), height, width, geometry)
        cen_x, cen_y = pixel_to_user(cy, cx, height, width, geometry)
        spots.append(
            Hotspot(
                row=int(row),
                col=int(col),
                x=peak_x,
                y=peak_y,
                value=float(vals[row, col]),
                unit=unit,
                area_px=area,
                rank=0,
                peak_x=peak_x,
                peak_y=peak_y,
                centroid_x=cen_x,
                centroid_y=cen_y,
            )
        )
    spots.sort(key=lambda s: s.value, reverse=True)
    ranked: list[Hotspot] = []
    for i, spot in enumerate(spots[:max_n]):
        ranked.append(
            Hotspot(
                row=spot.row,
                col=spot.col,
                x=spot.x,
                y=spot.y,
                value=spot.value,
                unit=spot.unit,
                area_px=spot.area_px,
                rank=i,
                peak_x=spot.peak_x,
                peak_y=spot.peak_y,
                centroid_x=spot.centroid_x,
                centroid_y=spot.centroid_y,
            )
        )
    return ranked


def find_above(
    frame: Frame2D,
    threshold: float,
    *,
    geometry: FrameGeometry | None = None,
    min_area_px: int = 1,
    max_n: int = 10,
) -> list[Hotspot]:
    return find_above_array(
        _valid_values(frame),
        threshold,
        geometry=geometry,
        min_area_px=min_area_px,
        max_n=max_n,
        unit=frame.unit,
    )


def summarize(
    frame: Frame2D,
    *,
    geometry: FrameGeometry | None = None,
    threshold: float | None = None,
) -> dict[str, float]:
    geometry = geometry or FrameGeometry()
    vals = _valid_values(frame)
    finite = vals[np.isfinite(vals)]
    if finite.size == 0:
        raise ValueError("no valid pixels in frame")
    peak = find_max(frame, geometry=geometry)
    over = 0.0
    area = 0.0
    frac = 0.0
    if threshold is not None:
        spots = find_above(frame, threshold, geometry=geometry, min_area_px=1, max_n=10_000)
        over = float(len(spots))
        area = float(sum(s.area_px for s in spots))
        frac = area / float(finite.size)
    bad = float(int(np.asarray(frame.mask).sum())) if frame.mask is not None else 0.0
    return {
        "t_max": float(finite.max()),
        "t_min": float(finite.min()),
        "t_mean": float(finite.mean()),
        "t_p99": float(np.percentile(finite, 99)),
        "t_std": float(finite.std(ddof=0)),
        "hotspot_row": float(peak.row),
        "hotspot_col": float(peak.col),
        "hotspot_x": float(peak.x),
        "hotspot_y": float(peak.y),
        "n_over_threshold": over,
        "over_threshold_area_px": area,
        "over_threshold_frac": frac,
        "bad_pixel_count": bad,
        "frame_unit_is_degc": 1.0 if frame.unit == "degC" else 0.0,
    }
