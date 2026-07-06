"""Pre-simulation netlist checks."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path

from benchgate.schemas import MappingManifest, SpiceModelKind

_PLACEHOLDER_RE = re.compile(r"^(\S+)\s+__\1\b.*$", re.MULTILINE)
_CONNECTOR_REF_RE = re.compile(r"^J\d+$", re.I)
_DROPPED_RE = re.compile(
    r"^\* benchgate: dropped unmodeled placeholder '(?P<line>.*)'",
    re.MULTILINE,
)


@dataclass
class PreflightIssue:
    severity: str  # error | warning | info
    code: str
    message: str
    reference: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PreflightReport:
    passed: bool
    issues: list[PreflightIssue]

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "issues": [i.to_dict() for i in self.issues],
        }


def run_preflight(
    netlist_text: str,
    manifest: MappingManifest,
    *,
    prepared_text: str | None = None,
) -> PreflightReport:
    issues: list[PreflightIssue] = []

    for match in _PLACEHOLDER_RE.finditer(netlist_text):
        ref = match.group(1)
        entry = manifest.find_by_reference(ref)
        if entry and entry.is_ready and entry.spice_kind == SpiceModelKind.SUBCKT:
            injected = (
                prepared_text
                and entry.sim_name
                and f"injected subckt from manifest ({ref}" in prepared_text
            )
            if injected:
                issues.append(
                    PreflightIssue(
                        severity="info",
                        code="manifest_subckt_injected",
                        reference=ref,
                        message=(
                            f"{ref} exported as placeholder; benchgate injected "
                            f"{entry.sim_name!r} from manifest (add Sim.* fields on schematic)"
                        ),
                    )
                )
                continue
            issues.append(
                PreflightIssue(
                    severity="error",
                    code="unmodeled_subckt",
                    reference=ref,
                    message=(
                        f"{ref} exported as placeholder but manifest has ready subckt "
                        f"{entry.sim_name!r}; add Sim.Library/Sim.Name/Sim.Pins on schematic"
                    ),
                )
            )
        else:
            lib_id = str((entry.metadata or {}).get("lib_id") or "") if entry else ""
            is_connector = bool(_CONNECTOR_REF_RE.match(ref)) or lib_id.startswith("Connector:")
            issues.append(
                PreflightIssue(
                    severity="info" if is_connector else "warning",
                    code="connector_dropped" if is_connector else "unmodeled_placeholder",
                    reference=ref,
                    message=(
                        f"{ref} is a connector (no SPICE model); dropped from simulation"
                        if is_connector
                        else f"{ref} has no SPICE model and will be dropped from simulation"
                    ),
                )
            )

    if prepared_text:
        for match in _DROPPED_RE.finditer(prepared_text):
            line = match.group("line")
            ref = line.split()[0] if line.split() else None
            entry = manifest.find_by_reference(ref) if ref else None
            if entry and entry.is_ready and entry.spice_kind == SpiceModelKind.SUBCKT:
                issues.append(
                    PreflightIssue(
                        severity="error",
                        code="subckt_dropped",
                        reference=ref,
                        message=f"{ref} was dropped in prepared netlist despite manifest binding",
                    )
                )

        if "benchgate: BJT pin order fixed" in prepared_text:
            count = prepared_text.count("benchgate: BJT pin order fixed")
            issues.append(
                PreflightIssue(
                    severity="info",
                    code="bjt_pin_fixup",
                    message=f"Reordered {count} BJT line(s) to SPICE C-B-E pin order",
                )
            )

    has_error = any(i.severity == "error" for i in issues)
    return PreflightReport(passed=not has_error, issues=issues)


def write_preflight_report(report: PreflightReport, path: Path) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
