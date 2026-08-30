"""Map thermal-frame user coordinates onto a KiCad PCB (footprint hit list)."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

_GRID_ORIGIN_RE = re.compile(r"\(grid_origin\s+([^\s)]+)\s+([^\s)]+)\)")
_GR_EDGE_KIND_RE = re.compile(r"\(gr_(rect|line|arc)\b")
_GR_XY_RE = re.compile(r"\((?:start|end|mid)\s+([^\s)]+)\s+([^\s)]+)\)")
_EDGE_CUTS_RE = re.compile(r'\(layer\s+"Edge\.Cuts"')

# Default candidate radius when px_per_mm is unknown (explicit millimetres).
DEFAULT_HIT_DISTANCE_MM = 5.0
# When px_per_mm is known, radius is this many pixel widths (2 px ≈ neighbour pad).
HIT_RADIUS_PIXELS = 2.0
# Board-outline slack: one pixel width so connectors sitting on Edge.Cuts are not rejected.
BOARD_MARGIN_PIXELS = 1.0


@dataclass(frozen=True)
class FootprintBox:
    reference: str
    x_mm: float
    y_mm: float
    width_mm: float
    height_mm: float
    rotation_deg: float = 0.0


@dataclass(frozen=True)
class FootprintHit:
    reference: str
    distance_mm: float
    inside: bool


@dataclass(frozen=True)
class SchematicPart:
    reference: str
    lib_id: str
    value: str
    in_bom: bool = True


@dataclass(frozen=True)
class RefVerifyResult:
    pcb_refs: list[str]
    schematic_refs: list[str]
    common: list[str]
    pcb_only: list[str]
    schematic_only: list[str]
    schematic_only_bom: list[str]

    @property
    def ok(self) -> bool:
        return not self.pcb_only and not self.schematic_only_bom


def resolve_kicad_project_dir(design_dir: Path | str) -> Path:
    """Return the directory that contains ``*.kicad_pro`` (and sibling ``*.kicad_pcb``).

    Accepts either the KiCad project folder (``…/pcb/``) or a repo root that
    nests it (``…/pcb/*.kicad_pro``).
    """
    root = Path(design_dir).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Not a directory: {root}")
    local = sorted(root.glob("*.kicad_pro"))
    if len(local) == 1:
        return root
    if len(local) > 1:
        raise FileNotFoundError(f"Multiple .kicad_pro under {root}: {[p.name for p in local]}")
    nested = sorted(root.glob("pcb/*.kicad_pro"))
    if len(nested) == 1:
        return nested[0].parent
    if len(nested) > 1:
        raise FileNotFoundError(f"Multiple pcb/*.kicad_pro under {root}")
    raise FileNotFoundError(f"No .kicad_pro under {root} or {root}/pcb")


def resolve_kicad_pcb_path(design_dir: Path | str) -> Path:
    """Primary ``*.kicad_pcb`` beside the resolved project file (skip ``.history``)."""
    proj = resolve_kicad_project_dir(design_dir)
    candidates = sorted(
        p for p in proj.glob("*.kicad_pcb") if ".history" not in p.parts
    )
    if not candidates:
        raise FileNotFoundError(f"No .kicad_pcb under {proj}")
    if len(candidates) > 1:
        pro_stem = next(proj.glob("*.kicad_pro"), None)
        if pro_stem is not None:
            match = proj / f"{pro_stem.stem}.kicad_pcb"
            if match in candidates:
                return match
    return candidates[0]


def homography_from_points(src_xy: np.ndarray, dst_xy: np.ndarray) -> np.ndarray:
    """Direct linear transform. ``src`` and ``dst`` are (n>=4, 2)."""
    src = np.asarray(src_xy, dtype=float).reshape(-1, 2)
    dst = np.asarray(dst_xy, dtype=float).reshape(-1, 2)
    if src.shape[0] < 4 or dst.shape[0] < 4:
        raise ValueError("homography needs at least 4 point pairs")
    rows: list[list[float]] = []
    for (x, y), (u, v) in zip(src, dst):
        rows.append([-x, -y, -1, 0, 0, 0, x * u, y * u, u])
        rows.append([0, 0, 0, -x, -y, -1, x * v, y * v, v])
    _, _, vh = np.linalg.svd(np.asarray(rows, dtype=float))
    h = vh[-1].reshape(3, 3)
    if abs(h[2, 2]) < 1e-15:
        raise ValueError("degenerate homography")
    return h / h[2, 2]


def apply_homography(h: np.ndarray, xy: tuple[float, float] | np.ndarray) -> tuple[float, float]:
    pt = np.asarray(xy, dtype=float).reshape(2)
    vec = h @ np.array([pt[0], pt[1], 1.0], dtype=float)
    if abs(vec[2]) < 1e-15:
        raise ValueError("homography mapped point to infinity")
    return float(vec[0] / vec[2]), float(vec[1] / vec[2])


def _point_to_box_distance(x: float, y: float, box: FootprintBox) -> tuple[float, bool]:
    """Axis-aligned box in board mm (rotation ignored for S0). 0 if inside."""
    hw, hh = box.width_mm / 2.0, box.height_mm / 2.0
    dx = max(abs(x - box.x_mm) - hw, 0.0)
    dy = max(abs(y - box.y_mm) - hh, 0.0)
    dist = math.hypot(dx, dy)
    inside = abs(x - box.x_mm) <= hw and abs(y - box.y_mm) <= hh
    return dist, inside


def default_hit_distance_mm(px_per_mm: list[float] | tuple[float, ...] | None) -> float:
    """Candidate radius for footprint hits.

    Source of the default:
    - known ``px_per_mm`` → ``HIT_RADIUS_PIXELS / min(px_per_mm)`` (two pixel
      widths; one HTPA pixel is typically ~3 mm on the registered fixture).
    - unknown → ``DEFAULT_HIT_DISTANCE_MM`` (5.0 mm), not a footprint heuristic.
    """
    if px_per_mm:
        vals = [float(v) for v in px_per_mm if float(v) > 0.0]
        if vals:
            return HIT_RADIUS_PIXELS / min(vals)
    return DEFAULT_HIT_DISTANCE_MM


def default_board_margin_mm(px_per_mm: list[float] | tuple[float, ...] | None) -> float:
    """Slack around Edge.Cuts for ``point_in_board``.

    Known ``px_per_mm`` → ``BOARD_MARGIN_PIXELS / min(px_per_mm)`` (one pixel).
    Unknown → ``0.0`` (no slack; do not invent a millimetre default).
    """
    if px_per_mm:
        vals = [float(v) for v in px_per_mm if float(v) > 0.0]
        if vals:
            return BOARD_MARGIN_PIXELS / min(vals)
    return 0.0


def hit_footprints(
    x_mm: float,
    y_mm: float,
    footprints: list[FootprintBox],
    *,
    max_n: int = 5,
    max_distance_mm: float | None = None,
) -> list[FootprintHit]:
    """Nearest footprints. ``max_distance_mm`` drops candidates farther than that.

    ``None`` keeps the previous unbounded behaviour (still capped by ``max_n``).
    """
    hits = []
    for box in footprints:
        dist, inside = _point_to_box_distance(x_mm, y_mm, box)
        if max_distance_mm is not None and (not inside) and dist > float(max_distance_mm):
            continue
        hits.append(FootprintHit(reference=box.reference, distance_mm=dist, inside=inside))
    hits.sort(key=lambda h: (0 if h.inside else 1, h.distance_mm, h.reference))
    return hits[:max_n]


def read_grid_origin(pcb_path: Path | str) -> tuple[float, float]:
    """Sheet-absolute KiCad grid origin in mm. ``(0, 0)`` if unset."""
    text = Path(pcb_path).read_text(encoding="utf-8")
    match = _GRID_ORIGIN_RE.search(text)
    if not match:
        return (0.0, 0.0)
    return float(match.group(1)), float(match.group(2))


def _sexpr_block_end(text: str, start: int) -> int:
    """Index after the matching close paren for ``text[start] == '('``."""
    if start >= len(text) or text[start] != "(":
        raise ValueError("s-expr block must start at '('")
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i + 1
    raise ValueError("unbalanced s-expr")


def read_board_outline(pcb_path: Path | str) -> tuple[float, float, float, float]:
    """Axis-aligned Edge.Cuts bbox in mm **relative to KiCad grid origin**.

    File coordinates convert as ``file_xy - grid_origin`` (not
    ``board_origin``). Parses ``gr_rect``, ``gr_line``, and ``gr_arc`` on
    layer ``Edge.Cuts``.
    """
    path = Path(pcb_path)
    text = path.read_text(encoding="utf-8")
    gx, gy = read_grid_origin(path)
    xs: list[float] = []
    ys: list[float] = []
    for match in _GR_EDGE_KIND_RE.finditer(text):
        end = _sexpr_block_end(text, match.start())
        block = text[match.start() : end]
        if not _EDGE_CUTS_RE.search(block):
            continue
        for xy in _GR_XY_RE.finditer(block):
            xs.append(float(xy.group(1)) - gx)
            ys.append(float(xy.group(2)) - gy)
    if not xs:
        raise ValueError(f"no Edge.Cuts geometry in {path}")
    return (min(xs), min(ys), max(xs), max(ys))


def load_board_outline(design_dir: Path | str) -> tuple[float, float, float, float]:
    return read_board_outline(resolve_kicad_pcb_path(design_dir))


def point_in_board(
    x_mm: float,
    y_mm: float,
    outline: tuple[float, float, float, float],
    margin_mm: float = 0.0,
) -> bool:
    x0, y0, x1, y1 = outline
    pad = float(margin_mm)
    return (x0 - pad) <= x_mm <= (x1 + pad) and (y0 - pad) <= y_mm <= (y1 + pad)


def load_pcb_footprints(design_dir: Path | str) -> list[FootprintBox]:
    """Read footprint boxes in mm **relative to KiCad grid origin**.

    kicad-tools exposes positions relative to the Edge.Cuts origin; this
    converts them to ``file_xy - grid_origin`` so they match thermal
    homography ``dst_mm`` (left-bottom LED = grid origin).
    """
    from kicad_tools import PCB, load_pcb

    pcb_path = resolve_kicad_pcb_path(design_dir)
    board = PCB(load_pcb(pcb_path))
    gx, gy = read_grid_origin(pcb_path)
    ox, oy = board.board_origin
    out: list[FootprintBox] = []
    for fp in board.footprints:
        pads = list(getattr(fp, "pads", []) or [])
        if pads:
            xs, ys = [], []
            for pad in pads:
                px, py = pad.position
                w, h = pad.size
                xs.extend([px - w / 2.0, px + w / 2.0])
                ys.extend([py - h / 2.0, py + h / 2.0])
            width = max(xs) - min(xs)
            height = max(ys) - min(ys)
        else:
            width, height = 1.0, 1.0
        x, y = fp.position
        out.append(
            FootprintBox(
                reference=str(fp.reference),
                x_mm=float(x) + ox - gx,
                y_mm=float(y) + oy - gy,
                width_mm=max(float(width), 0.2),
                height_mm=max(float(height), 0.2),
                rotation_deg=float(getattr(fp, "rotation", 0.0) or 0.0),
            )
        )
    return out


def load_schematic_index(design_dir: Path | str) -> dict[str, SchematicPart]:
    """Map placed schematic reference → ``lib_id`` / ``value``."""
    from benchgate.kicad.project import KiCadProject, iter_symbols

    proj_dir = resolve_kicad_project_dir(design_dir)
    project = KiCadProject.load(proj_dir)
    index: dict[str, SchematicPart] = {}
    for sym in iter_symbols(project.schematic_doc(), project.root):
        ref = sym.reference
        if not ref or ref.startswith("#"):
            continue
        index[ref] = SchematicPart(
            reference=ref,
            lib_id=str(sym.lib_id or ""),
            value=str(sym.value or ""),
            in_bom=sym.in_bom is not False,
        )
    return index


def verify_pcb_schematic_refs(design_dir: Path | str) -> RefVerifyResult:
    """Check every PCB footprint reference exists on the schematic (and vice versa for BOM parts)."""
    pcb_refs = sorted({fp.reference for fp in load_pcb_footprints(design_dir)})
    sch_index = load_schematic_index(design_dir)
    sch_refs = sorted(sch_index)
    pcb_set, sch_set = set(pcb_refs), set(sch_index)
    sch_only = sorted(sch_set - pcb_set)
    sch_only_bom = sorted(ref for ref in sch_only if sch_index[ref].in_bom)
    return RefVerifyResult(
        pcb_refs=pcb_refs,
        schematic_refs=sch_refs,
        common=sorted(pcb_set & sch_set),
        pcb_only=sorted(pcb_set - sch_set),
        schematic_only=sch_only,
        schematic_only_bom=sch_only_bom,
    )


def schematic_fields(reference: str | None, index: dict[str, SchematicPart]) -> dict[str, str | None]:
    if not reference:
        return {"schematic_lib_id": None, "schematic_value": None, "schematic_status": "no_hit"}
    part = index.get(reference)
    if part is None:
        return {"schematic_lib_id": None, "schematic_value": None, "schematic_status": "missing_on_schematic"}
    return {
        "schematic_lib_id": part.lib_id,
        "schematic_value": part.value,
        "schematic_status": "matched",
    }


def attach_schematic_to_hits(
    hits: list[FootprintHit],
    index: dict[str, SchematicPart],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for hit in hits:
        row: dict[str, object] = {
            "reference": hit.reference,
            "distance_mm": hit.distance_mm,
            "inside": hit.inside,
        }
        row.update(schematic_fields(hit.reference, index))
        rows.append(row)
    return rows


def parse_homography_points(items: list[str]) -> tuple[np.ndarray, np.ndarray]:
    """Parse ``px,py:mmx,mmy`` pairs into src (pixel/user) and dst (board mm)."""
    src, dst = [], []
    for item in items:
        left, right = item.split(":", 1)
        px, py = (float(p) for p in left.split(","))
        mx, my = (float(p) for p in right.split(","))
        src.append((px, py))
        dst.append((mx, my))
    return np.asarray(src, dtype=float), np.asarray(dst, dtype=float)


def order_rectangle_corners(xy: np.ndarray) -> np.ndarray:
    """Order 4 points clockwise on a y-down image, starting at bottom-left.

    Bottom-left is ``argmin(x - y)`` (small column, large row).
    """
    pts = np.asarray(xy, dtype=float).reshape(-1, 2)
    if pts.shape[0] != 4:
        raise ValueError(f"need 4 corners, got {pts.shape[0]}")
    center = pts.mean(axis=0)
    angles = np.arctan2(pts[:, 1] - center[1], pts[:, 0] - center[0])
    ordered = pts[np.argsort(angles)]
    start = int(np.argmin(ordered[:, 0] - ordered[:, 1]))
    return np.roll(ordered, -start, axis=0)


def rectangle_dst_mm(
    src_xy: np.ndarray, *, length_mm: float, width_mm: float
) -> tuple[np.ndarray, np.ndarray]:
    """Pair ordered corners to a length×width rectangle.

    Convention: ``length_mm`` (长) must be >= ``width_mm`` (宽). The longer
    pixel edge from the start corner maps to ``length_mm``. Origin is image
    bottom-left (= KiCad grid origin). Destination uses KiCad axes: +X right,
    +Y down. A blob toward the top of the thermal image therefore has
    **negative** Y.
    """
    src = order_rectangle_corners(src_xy)
    length_mm, width_mm = float(length_mm), float(width_mm)
    if length_mm <= 0 or width_mm <= 0:
        raise ValueError("length_mm and width_mm must be positive")
    if length_mm < width_mm:
        raise ValueError(f"length_mm ({length_mm}) must be >= width_mm ({width_mm})")
    ab = float(np.linalg.norm(src[1] - src[0]))
    ad = float(np.linalg.norm(src[3] - src[0]))
    if ab >= ad:
        dst = np.array(
            [[0.0, 0.0], [length_mm, 0.0], [length_mm, -width_mm], [0.0, -width_mm]],
            dtype=float,
        )
    else:
        dst = np.array(
            [[0.0, 0.0], [0.0, -width_mm], [length_mm, -width_mm], [length_mm, 0.0]],
            dtype=float,
        )
    return src, dst


def edge_px_per_mm(src_xy: np.ndarray, dst_xy: np.ndarray) -> tuple[float, float]:
    """Pixels per millimetre along the two edges leaving corner 0."""
    src = np.asarray(src_xy, dtype=float).reshape(-1, 2)
    dst = np.asarray(dst_xy, dtype=float).reshape(-1, 2)
    d_ab = float(np.linalg.norm(dst[1] - dst[0]))
    d_ad = float(np.linalg.norm(dst[3] - dst[0]))
    if d_ab < 1e-9 or d_ad < 1e-9:
        raise ValueError("degenerate rectangle edges")
    return (
        float(np.linalg.norm(src[1] - src[0]) / d_ab),
        float(np.linalg.norm(src[3] - src[0]) / d_ad),
    )


def reprojection_err_mm(h: np.ndarray, src_xy: np.ndarray, dst_xy: np.ndarray) -> list[float]:
    errs = []
    for s, d in zip(np.asarray(src_xy, dtype=float), np.asarray(dst_xy, dtype=float)):
        mx, my = apply_homography(h, (float(s[0]), float(s[1])))
        errs.append(math.hypot(mx - float(d[0]), my - float(d[1])))
    return errs


def format_homography_pairs(src_xy: np.ndarray, dst_xy: np.ndarray) -> list[str]:
    pairs = []
    for (px, py), (mx, my) in zip(src_xy, dst_xy):
        pairs.append(f"{px:.4f},{py:.4f}:{mx:.4f},{my:.4f}")
    return pairs
