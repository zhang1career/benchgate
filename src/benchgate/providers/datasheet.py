"""Build ngspice model libraries from datasheet catalog entries."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchgate.lab.fit import write_subckt
from benchgate.schemas import ComponentMapping, ModelArtifact, ModelProvenance, ModelSource
from benchgate.sim.datasheet_catalog import (
    default_datasheet_catalog_path,
    load_datasheet_catalog,
    lookup_datasheet_model,
    model_line,
)

@dataclass
class DatasheetModelProvider:
    """Emit a ``.lib`` with a single ``.model`` line from the datasheet catalog."""

    mpn: str
    catalog_path: Path | None = None
    valid_range: dict[str, Any] = field(default_factory=dict)
    notes: str | None = None

    source = ModelSource.DATASHEET

    def build(self, entry: ComponentMapping, *, workdir: Path) -> ModelArtifact:
        catalog = load_datasheet_catalog(self.catalog_path or default_datasheet_catalog_path())
        spec = lookup_datasheet_model(self.mpn, catalog)
        if not spec:
            raise ValueError(
                f"no datasheet SPICE model for MPN {self.mpn!r}; "
                "add it to config/datasheet_models.yaml"
            )

        mpn_upper = self.mpn.strip().upper()
        line = model_line(mpn_upper, spec)
        lib_path = (workdir / f"{mpn_upper}.lib").resolve()
        content = f"* {mpn_upper} — benchgate datasheet model\n{line}\n"
        write_subckt(lib_path, content)

        checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()
        provenance = ModelProvenance(
            source=ModelSource.DATASHEET,
            generated_at=datetime.now(timezone.utc).isoformat(),
            tool="benchgate datasheet provider",
            source_files=[str(self.catalog_path or default_datasheet_catalog_path())],
            checksum=checksum,
            valid_range=dict(self.valid_range or {}),
            metrics={},
            notes=self.notes or f"catalog model for {mpn_upper}",
        )
        return ModelArtifact(
            lib_path=lib_path,
            sim_name=mpn_upper,
            sim_pins=None,
            provenance=provenance,
        )
