"""Homography and footprint hit list (no PCB required)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from benchgate.lab.board_map import (
    FootprintBox,
    apply_homography,
    hit_footprints,
    homography_from_points,
    parse_homography_points,
    point_in_board,
)


def test_identity_homography():
    src = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=float)
    h = homography_from_points(src, src)
    x, y = apply_homography(h, (0.25, 0.5))
    assert x == pytest.approx(0.25, abs=1e-6)
    assert y == pytest.approx(0.5, abs=1e-6)


def test_scale_homography():
    src = np.array([[0, 0], [10, 0], [10, 10], [0, 10]], dtype=float)
    dst = src * 2.0
    h = homography_from_points(src, dst)
    x, y = apply_homography(h, (5.0, 5.0))
    assert x == pytest.approx(10.0, abs=1e-5)
    assert y == pytest.approx(10.0, abs=1e-5)


def test_hit_footprints_sorted_and_unassigned():
    boxes = [
        FootprintBox("U1", 10, 10, 4, 4),
        FootprintBox("R1", 30, 10, 2, 1),
    ]
    inside = hit_footprints(10.5, 10.2, boxes)
    assert inside[0].reference == "U1"
    assert inside[0].inside is True
    far = hit_footprints(100, 100, boxes)
    assert far[0].inside is False
    assert far[0].reference in {"U1", "R1"}


def test_parse_homography_points():
    src, dst = parse_homography_points(["0,0:1,2", "1,0:3,2", "1,1:3,4", "0,1:1,4"])
    assert src.shape == (4, 2)
    assert dst[0].tolist() == [1.0, 2.0]


def test_order_and_rectangle_mm_long_edge_first():
    from benchgate.lab.board_map import edge_px_per_mm, order_rectangle_corners, rectangle_dst_mm

    # shuffled corners of a 24 x 18.5 px rectangle
    raw = np.array([[28.0, 23.0], [4.0, 5.0], [4.0, 24.0], [28.0, 5.0]], dtype=float)
    ordered = order_rectangle_corners(raw)
    assert ordered[0].tolist() == pytest.approx([4.0, 24.0])
    src, dst = rectangle_dst_mm(raw, length_mm=79.0, width_mm=61.0)
    assert src[0].tolist() == pytest.approx([4.0, 24.0])
    assert dst[0].tolist() == [0.0, 0.0]
    assert dst[1].tolist() == [0.0, -61.0]
    assert dst[3].tolist() == pytest.approx([79.0, 0.0])
    px_x, px_y = edge_px_per_mm(src, dst)
    assert px_x == pytest.approx(19.0 / 61.0)
    assert px_y == pytest.approx(float(np.linalg.norm(src[3] - src[0])) / 79.0)
    with pytest.raises(ValueError, match="must be >="):
        rectangle_dst_mm(raw, length_mm=61.0, width_mm=79.0)


def test_read_grid_origin(tmp_path):
    from benchgate.lab.board_map import read_grid_origin

    pcb = tmp_path / "x.kicad_pcb"
    pcb.write_text("(kicad_pcb (version 20241201) (grid_origin 12.5 80) (setup))\n", encoding="utf-8")
    assert read_grid_origin(pcb) == (12.5, 80.0)
    pcb.write_text("(kicad_pcb (version 20241201))\n", encoding="utf-8")
    assert read_grid_origin(pcb) == (0.0, 0.0)


@pytest.mark.integration
def test_crosspoint_pcb_schematic_refs_match():
    root = Path("/Users/mini/Projects/hw-lab/tars-io-crosspoint")
    if not (root / "pcb" / "tars-io-crosspoint.kicad_pro").exists():
        pytest.skip("tars-io-crosspoint not present")
    from benchgate.lab.board_map import resolve_kicad_project_dir, verify_pcb_schematic_refs

    assert resolve_kicad_project_dir(root).name == "pcb"
    result = verify_pcb_schematic_refs(root)
    assert result.ok
    assert len(result.common) == 43
    assert result.pcb_only == []
    assert result.schematic_only_bom == []
    assert set(result.schematic_only) == {"H2", "H3", "H4"}


def test_hit_footprints_max_distance():
    boxes = [
        FootprintBox("U1", 10, 10, 4, 4),
        FootprintBox("R1", 30, 10, 2, 1),
    ]
    assert hit_footprints(100, 100, boxes, max_distance_mm=5) == []


def test_point_in_board_margin():
    outline = (0.0, -17.0, 56.0, 0.0)
    assert point_in_board(10.0, -8.0, outline) is True
    assert point_in_board(-1.0, -8.0, outline) is False
    assert point_in_board(-0.5, -8.0, outline, margin_mm=1.0) is True


@pytest.mark.integration
def test_board_outline_crosspoint():
    root = Path("/Users/mini/Projects/hw-lab/tars-io-crosspoint")
    if not (root / "pcb" / "tars-io-crosspoint.kicad_pro").exists():
        pytest.skip("tars-io-crosspoint not present")
    from benchgate.lab.board_map import load_board_outline

    x0, y0, x1, y1 = load_board_outline(root)
    assert x0 == pytest.approx(0.0, abs=0.01)
    assert y0 == pytest.approx(-17.018, abs=0.01)
    assert x1 == pytest.approx(55.88, abs=0.01)
    assert y1 == pytest.approx(0.0, abs=0.01)


@pytest.mark.integration
def test_map_out_of_board():
    root = Path("/Users/mini/Projects/hw-lab/tars-io-crosspoint")
    session = root / "models/captured/sessions/20260830T051932Z_e7eb/session.yaml"
    hom = Path.home() / ".benchgate/config/thermal_map/47cf328f84bc.yaml"
    if not session.exists() or not hom.exists():
        pytest.skip("crosspoint session or legacy homography missing")
    from benchgate.agent.dispatch import dispatch

    result = dispatch(
        "lab_thermal_map",
        {
            "design_dir": str(root),
            "session_id": "20260830T051932Z_e7eb",
            "homography_file": str(hom),
        },
    )
    hit = result["kicad_hits"][0]
    assert hit["status"] == "out_of_board"
    assert hit["reference"] is None
