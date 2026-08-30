"""Pure 2-D field analysis (no hardware)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from benchgate.instruments.types import FRAME_UNITS, Frame2D, Frame2DSeries, QuantityKind
from benchgate.lab.field2d import FrameGeometry, find_above, find_max, pixel_to_user, summarize


def _frame(values, mask=None, unit="count"):
    return Frame2D(
        values=np.asarray(values, dtype=float),
        unit=unit,
        quantity=QuantityKind.TEMPERATURE,
        timestamp=datetime.now(timezone.utc),
        mask=None if mask is None else np.asarray(mask, dtype=bool),
    )


def test_find_max_hits_known_pixel():
    grid = np.zeros((8, 8))
    grid[3, 5] = 42
    spot = find_max(_frame(grid))
    assert (spot.row, spot.col) == (3, 5)
    assert spot.value == 42


def test_find_max_skips_bad_pixel():
    grid = np.ones((8, 8))
    grid[1, 2] = 999
    mask = np.zeros((8, 8), dtype=bool)
    mask[1, 2] = True
    grid[4, 4] = 10
    spot = find_max(_frame(grid, mask=mask))
    assert (spot.row, spot.col) == (4, 4)
    assert spot.value == 10


def test_all_masked_raises():
    grid = np.ones((2, 2))
    mask = np.ones((2, 2), dtype=bool)
    with pytest.raises(ValueError, match="no valid"):
        find_max(_frame(grid, mask=mask))


def test_geometry_origins_and_flip():
    geo = FrameGeometry(origin="top_left", x_scale=2.0, y_scale=3.0)
    x, y = pixel_to_user(1, 2, 8, 8, geo)
    assert (x, y) == (4.0, 3.0)
    x, y = pixel_to_user(1, 2, 8, 8, FrameGeometry(origin="bottom_left"))
    assert (x, y) == (2.0, 6.0)
    x, y = pixel_to_user(1, 2, 8, 8, FrameGeometry(origin="center"))
    assert x == pytest.approx(2 - 3.5)
    assert y == pytest.approx(3.5 - 1)


def test_rotate_then_scale():
    # 90 CW: (0, 0) -> (0, h-1) in rotated (w, h) = (8, 8) still square
    x, y = pixel_to_user(0, 0, 8, 8, FrameGeometry(rotate_quadrants=1, x_scale=1, y_scale=1))
    assert (x, y) == (7.0, 0.0)
    # fractional part rotates with the pixel, not glued back on the old axis
    x, y = pixel_to_user(3.4, 5.0, 8, 8, FrameGeometry(rotate_quadrants=1))
    assert x == pytest.approx(3.6)
    assert y == pytest.approx(5.0)


def test_find_above_diagonal_is_one_blob():
    grid = np.zeros((6, 6))
    grid[1, 1] = 20
    grid[2, 2] = 21
    spots = find_above(_frame(grid), 10, min_area_px=1, max_n=10)
    assert len(spots) == 1
    assert spots[0].area_px == 2


def test_find_above_two_regions_and_min_area():
    grid = np.zeros((10, 10))
    grid[1:3, 1:3] = 20
    grid[7, 7] = 30
    spots = find_above(_frame(grid), 10, min_area_px=1, max_n=10)
    assert len(spots) == 2
    assert spots[0].value == 30
    assert spots[0].area_px == 1
    assert spots[1].area_px == 4
    tiny = find_above(_frame(grid), 10, min_area_px=2, max_n=10)
    assert len(tiny) == 1
    assert tiny[0].area_px == 4


def test_summarize_keys_are_float():
    grid = np.arange(16, dtype=float).reshape(4, 4)
    out = summarize(_frame(grid), threshold=10)
    assert set(out) >= {
        "t_max",
        "t_min",
        "t_mean",
        "hotspot_row",
        "hotspot_col",
        "n_over_threshold",
        "frame_unit_is_degc",
    }
    assert all(isinstance(v, float) for v in out.values())
    assert out["frame_unit_is_degc"] == 0.0


def test_frame2d_rejects_degc_without_calibration():
    with pytest.raises(ValueError, match="calibration"):
        _frame([[1.0, 2.0]], unit="degC")
    assert "count" in FRAME_UNITS
    with pytest.raises(ValueError, match="slope"):
        Frame2D(
            values=np.asarray([[1.0, 2.0]], dtype=float),
            unit="degC",
            quantity=QuantityKind.TEMPERATURE,
            timestamp=datetime.now(timezone.utc),
            calibration={"kind": "affine2pt"},
        )


def test_find_above_peak_and_centroid_differ():
    grid = np.zeros((10, 10))
    grid[2:5, 2:5] = 10.0
    grid[4, 4] = 99.0
    spot = find_above(_frame(grid), 5.0)[0]
    assert (spot.row, spot.col) == (4, 4)
    assert (spot.x, spot.y) == (spot.peak_x, spot.peak_y) == (4.0, 4.0)
    assert (spot.centroid_x, spot.centroid_y) == (3.0, 3.0)


def test_find_above_array_matches_frame_version():
    from benchgate.lab.field2d import find_above_array

    grid = np.zeros((10, 10))
    grid[2:5, 2:5] = 10.0
    grid[4, 4] = 99.0
    frame = _frame(grid)
    from_frame = find_above(frame, 5.0)
    from_array = find_above_array(grid, 5.0, unit=frame.unit)
    assert len(from_frame) == len(from_array) == 1
    a, b = from_frame[0], from_array[0]
    assert (a.row, a.col, a.area_px, a.value) == (b.row, b.col, b.area_px, b.value)
    assert (a.centroid_x, a.centroid_y) == (b.centroid_x, b.centroid_y)


def test_frame2d_series_frame_uses_t_rel():
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    series = Frame2DSeries(
        t_rel_s=np.array([0.0, 1.0, 2.5]),
        values=np.zeros((3, 4, 4)),
        unit="count",
        quantity=QuantityKind.TEMPERATURE,
        t0_utc=t0,
    )
    assert series.frame(0).timestamp == t0
    assert series.frame(2).timestamp == t0 + timedelta(seconds=2.5)


def test_apply_calibration_refuses_second_pass():
    from benchgate.lab.thermal import ThermalCalibration, apply_calibration

    cal = ThermalCalibration(kind="affine2pt", slope=0.1, offset=-273.15)
    once = apply_calibration(_frame(np.full((2, 2), 3000.0)), cal)
    assert once.unit == "degC"
    assert once.calibration["slope"] == pytest.approx(0.1)
    with pytest.raises(ValueError, match="count"):
        apply_calibration(once, cal)


def test_register_rectangle_four_peaks():
    from benchgate.lab.thermal import register_rectangle

    grid = np.full((32, 32), 2900.0)
    for r, c in ((5, 4), (5, 28), (24, 28), (24, 4)):
        grid[r, c] = 3200.0
        grid[r, c + 1] = 3180.0
    data = register_rectangle(_frame(grid), length_mm=79.0, width_mm=61.0)
    assert len(data["pairs"]) == 4
    assert data["src_px"][0][1] > data["src_px"][1][1]  # origin is the lower blob
    assert data["dst_mm"][0] == [0.0, 0.0]
    assert data["coord_frame"] == "kicad_grid"
    assert data["dst_mm"][1][1] < 0  # toward top of image is KiCad −Y
    assert data["coarse_warning"]
    assert data["reprojection_err_mm_max"] < 1.0


def test_fixture_id_ignores_warmup():
    from benchgate.lab.thermal import fixture_id

    kwargs = {
        "instrument_idn": "umeko-dec-h:test",
        "emissivity": 1.0,
        "distance_mm": None,
        "ambient_bin": "unknown",
    }
    assert fixture_id(warmup_s=0.0, **kwargs) == fixture_id(warmup_s=2.0, **kwargs)


def test_fixture_id_quantizes_distance():
    from benchgate.lab.thermal import fixture_id

    base = {"instrument_idn": "umeko-dec-h:test", "emissivity": 1.0, "ambient_bin": "unknown"}
    a = fixture_id(distance_mm=12.0, **base)
    b = fixture_id(distance_mm=14.0, **base)
    c = fixture_id(distance_mm=22.0, **base)
    assert a == b
    assert a != c


def test_reduce_median():
    from benchgate.agent.dispatch import _reduce_series

    t0 = datetime.now(timezone.utc)
    frames = np.ones((3, 4, 4), dtype=float)
    frames[1, 0, 0] = 999.0
    series = Frame2DSeries(
        t_rel_s=np.array([0.0, 1.0, 2.0]),
        values=frames,
        unit="count",
        quantity=QuantityKind.TEMPERATURE,
        t0_utc=t0,
    )
    out = _reduce_series(series, "median")
    assert out.values[0, 0] == pytest.approx(1.0)
    maxed = _reduce_series(series, "max")
    assert maxed.values[0, 0] == pytest.approx(999.0)
