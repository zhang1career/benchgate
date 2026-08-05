"""Parse KiCad ERC report files for gate summaries."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class ErcItem:
    code: str
    severity: str
    message: str
    location: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ErcSummary:
    path: str | None = None
    errors: int = 0
    warnings: int = 0
    items: list[ErcItem] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "errors": self.errors,
            "warnings": self.warnings,
            "items": [i.to_dict() for i in self.items],
        }


_ITEM_RE = re.compile(r"^\[(?P<code>[^\]]+)\]:\s*(?P<message>.+)$")
_COUNT_RE = re.compile(r"\*\*\s*ERC messages:\s*(?P<total>\d+)\s+Errors\s+(?P<errors>\d+)\s+Warnings\s+(?P<warnings>\d+)")


def parse_erc_report(path: Path) -> ErcSummary:
    if not path.is_file():
        return ErcSummary(path=str(path))
    text = path.read_text(encoding="utf-8", errors="replace")
    summary = ErcSummary(path=str(path))
    m = _COUNT_RE.search(text)
    if m:
        summary.errors = int(m.group("errors"))
        summary.warnings = int(m.group("warnings"))

    current: ErcItem | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("; error"):
            if current:
                current.severity = "error"
            continue
        if stripped.startswith("; warning"):
            if current:
                current.severity = "warning"
            continue
        m_item = _ITEM_RE.match(stripped)
        if m_item:
            if current:
                summary.items.append(current)
            current = ErcItem(
                code=m_item.group("code"),
                severity="info",
                message=m_item.group("message").strip(),
            )
            continue
        if current and stripped.startswith("@("):
            current.location = stripped
    if current:
        summary.items.append(current)
    return summary


def find_erc_report(design_dir: Path) -> Path | None:
    for pattern in ("*.erc.rpt", "*erc*.rpt"):
        matches = sorted(design_dir.glob(pattern))
        if matches:
            return matches[0]
    return None
