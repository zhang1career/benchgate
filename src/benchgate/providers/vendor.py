"""Vendor SPICE library → manifest registration (reference as-is, with encryption guard)."""

from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchgate.schemas import ComponentMapping, ModelArtifact, ModelProvenance, ModelSource

_ENCRYPTED_MARKERS = (
    "encrypt",
    "ENCRYPT",
    "This library is encrypted",
    "protection",
    "PROTECTED",
)
_SUBCKT_RE = re.compile(r"^\s*\.subckt\s+(\S+)", re.I | re.MULTILINE)
_MODEL_RE = re.compile(r"^\s*\.model\s+(\S+)", re.I | re.MULTILINE)


def inspect_vendor_lib(path: Path) -> tuple[str, list[str]]:
    """Return (text, warnings). Raise ValueError when the library looks encrypted."""
    raw = path.read_bytes()
    if b"\x00" in raw[:512]:
        raise ValueError(
            f"vendor library appears encrypted/binary ({path.name}); "
            "use bench or datasheet provider instead"
        )
    text = raw.decode("utf-8", errors="replace")
    for marker in _ENCRYPTED_MARKERS:
        if marker in text:
            raise ValueError(
                f"vendor library appears encrypted ({path.name}); "
                "use bench or datasheet provider instead"
            )
    warnings: list[str] = []
    if not (_SUBCKT_RE.search(text) or _MODEL_RE.search(text)):
        warnings.append(f"no .subckt or .model directive found in {path.name}")
    return text, warnings


def infer_vendor_sim_name(text: str, fallback: str) -> str:
    match = _SUBCKT_RE.search(text) or _MODEL_RE.search(text)
    return match.group(1) if match else fallback


@dataclass
class VendorModelProvider:
    """Copy a vendor ``.lib`` into the project subckt store with VENDOR provenance."""

    lib_path: Path
    sim_name: str
    sim_pins: str | None = None
    valid_range: dict[str, Any] = field(default_factory=dict)
    notes: str | None = None
    copy_into_workdir: bool = True

    source = ModelSource.VENDOR

    def build(self, entry: ComponentMapping, *, workdir: Path) -> ModelArtifact:
        src = self.lib_path.resolve()
        if not src.exists():
            raise FileNotFoundError(f"vendor library not found: {src}")

        text, inspect_warnings = inspect_vendor_lib(src)
        sim_name = self.sim_name or infer_vendor_sim_name(text, src.stem)
        dest = src
        if self.copy_into_workdir:
            workdir.mkdir(parents=True, exist_ok=True)
            dest = (workdir / f"{sim_name}.lib").resolve()
            if src != dest:
                shutil.copy2(src, dest)
                text = dest.read_text(encoding="utf-8", errors="replace")

        checksum = hashlib.sha256(text.encode("utf-8")).hexdigest()
        note_parts = list(inspect_warnings)
        if self.notes:
            note_parts.append(self.notes)
        provenance = ModelProvenance(
            source=ModelSource.VENDOR,
            generated_at=datetime.now(timezone.utc).isoformat(),
            tool="benchgate vendor provider",
            source_files=[str(src)],
            checksum=checksum,
            valid_range=dict(self.valid_range or {}),
            metrics={},
            notes="; ".join(note_parts) if note_parts else f"vendor model {sim_name}",
        )
        return ModelArtifact(
            lib_path=dest,
            sim_name=sim_name,
            sim_pins=self.sim_pins,
            provenance=provenance,
        )
