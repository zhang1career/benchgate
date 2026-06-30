"""KiCad project discovery and symbol scan."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from kicad_tools import Schematic
from kicad_tools.schema.symbol import SymbolInstance

from benchgate.schemas import kicad_key as make_kicad_key


@dataclass
class KiCadProject:
    root: Path
    project_file: Path
    schematic: Path

    @classmethod
    def load(cls, design_dir: Path) -> KiCadProject:
        design_dir = design_dir.resolve()
        pro = next(design_dir.glob("*.kicad_pro"), None)
        if pro is None:
            raise FileNotFoundError(f"No .kicad_pro under {design_dir}")
        sch = design_dir / f"{pro.stem}.kicad_sch"
        if not sch.exists():
            raise FileNotFoundError(f"Missing schematic {sch}")
        return cls(root=design_dir, project_file=pro, schematic=sch)

    def schematic_doc(self) -> Schematic:
        return Schematic.load(self.schematic)

    def iter_symbols(self) -> list[SymbolInstance]:
        return list(_walk_symbols(self.schematic_doc(), self.root))


def _walk_symbols(sch: Schematic, project_root: Path) -> Iterator[SymbolInstance]:
    """Yield symbols from sch and any hierarchical child sheets."""
    yield from sch.symbols
    for sheet in sch.sheets:
        sub_path = project_root / sheet.filename
        if not sub_path.exists():
            continue
        yield from _walk_symbols(Schematic.load(sub_path), project_root)


def iter_symbols(sch: Schematic, project_root: Path) -> list[SymbolInstance]:
    return list(_walk_symbols(sch, project_root))


def symbol_key(sym: SymbolInstance) -> str:
    return make_kicad_key(sym.lib_id, sym.value)
