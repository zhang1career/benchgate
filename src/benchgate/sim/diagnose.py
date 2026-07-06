"""Summarize simulation failures into actionable diagnostics."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_NGSPICE_ERR_RE = re.compile(r"(?i)(error|fatal|cannot find|unknown|syntax error)")


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _preflight_findings(data: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for issue in data.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        code = issue.get("code", "")
        ref = issue.get("reference")
        severity = issue.get("severity", "info")
        action = None
        if code == "unmodeled_subckt":
            action = f"Add Sim.Library/Sim.Name on schematic for {ref}"
        elif code == "unmodeled_placeholder" and ref and ref.startswith("J"):
            action = f"{ref} is a connector — safe to ignore (dropped from sim)"
            severity = "info"
        elif code == "unmodeled_placeholder":
            action = f"Map {ref} in manifest or add Sim.* fields"
        elif code == "manifest_subckt_injected":
            action = f"Optional: add Sim.* on schematic for {ref} to silence placeholder warning"
        out.append(
            {
                "category": "preflight",
                "severity": severity,
                "code": code,
                "reference": ref,
                "message": issue.get("message", ""),
                "action": action,
            }
        )
    return out


def _sim_report_findings(data: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not data.get("success"):
        if not data.get("ngspice_ok", True):
            out.append(
                {
                    "category": "ngspice",
                    "severity": "error",
                    "code": "ngspice_failed",
                    "message": "ngspice batch run did not complete successfully",
                    "action": "Inspect reports/sim/ngspice.log for SPICE errors",
                }
            )
    checks = data.get("checks") or {}
    for check in checks.get("results") or []:
        if check.get("passed"):
            continue
        out.append(
            {
                "category": "checks",
                "severity": "error",
                "code": "check_failed",
                "message": check.get("message") or f"check {check.get('name')} failed",
                "action": "Adjust design or relax profile checks in sim_profiles.yaml",
            }
        )
    stress = data.get("stress") or {}
    for row in stress.get("results") or []:
        if row.get("passed") or row.get("required") is False:
            continue
        out.append(
            {
                "category": "stress",
                "severity": "error",
                "code": "stress_limit",
                "reference": row.get("reference"),
                "message": row.get("message") or f"stress probe {row.get('probe')} over limit",
                "action": "Review stress_limits.yaml derating or component selection",
            }
        )
    for warn in stress.get("warnings") or []:
        out.append(
            {
                "category": "stress",
                "severity": "warning",
                "code": "stress_warn",
                "message": str(warn),
                "action": None,
            }
        )
    return out


def _log_findings(log_path: Path, *, limit: int = 20) -> list[dict[str, Any]]:
    if not log_path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not _NGSPICE_ERR_RE.search(line):
            continue
        out.append(
            {
                "category": "ngspice_log",
                "severity": "error",
                "code": "log_line",
                "message": line.strip(),
                "action": None,
            }
        )
        if len(out) >= limit:
            break
    return out


def diagnose_sim(reports_dir: Path) -> dict[str, Any]:
    """Read ``reports/sim/*`` artifacts and return a structured diagnosis."""
    sim_dir = reports_dir / "sim"
    findings: list[dict[str, Any]] = []

    preflight = _read_json(sim_dir / "preflight.json")
    if preflight:
        findings.extend(_preflight_findings(preflight))

    sim_report = _read_json(sim_dir / "sim_report.json")
    if sim_report:
        findings.extend(_sim_report_findings(sim_report))

    findings.extend(_log_findings(sim_dir / "ngspice.log"))

    errors = sum(1 for f in findings if f.get("severity") == "error")
    warnings = sum(1 for f in findings if f.get("severity") == "warning")
    ok = errors == 0 and (sim_report or {}).get("success", preflight is None)

    actions = [f["action"] for f in findings if f.get("action")]
    return {
        "ok": ok,
        "sim_success": (sim_report or {}).get("success"),
        "summary": {"errors": errors, "warnings": warnings, "findings": len(findings)},
        "findings": findings,
        "actions": actions,
    }
