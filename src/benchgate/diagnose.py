"""Unified diagnosis: simulation + lab + gate attribution."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from benchgate.bench_compare import WATCH_TRIGGER_TAGS
from benchgate.gate.report import load_sim_report_context
from benchgate.lab.store import LabDataStore
from benchgate.sim.diagnose import diagnose_sim


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _gate_findings(gate: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    summary = gate.get("summary") or {}

    for entry in gate.get("entries") or []:
        ref = entry.get("reference")
        for fail in entry.get("spec_failures") or []:
            findings.append(
                {
                    "category": "spec",
                    "severity": "error",
                    "reference": ref,
                    "message": fail,
                    "action": "Revise design or update spec budget",
                }
            )
        for warn in entry.get("range_warnings") or []:
            findings.append(
                {
                    "category": "valid_range",
                    "severity": "warning",
                    "reference": ref,
                    "message": warn,
                    "action": "Verify operating point or update model valid_range",
                }
            )
        wf_status = entry.get("waveform_status")
        if wf_status == "fail":
            rmse = entry.get("rmse")
            findings.append(
                {
                    "category": "waveform",
                    "severity": "error",
                    "reference": ref,
                    "message": f"bench vs sim RMSE {rmse:g} V exceeds fail threshold",
                    "action": "Compare waveforms; update model or investigate test setup",
                }
            )
        elif wf_status == "warn":
            rmse = entry.get("rmse")
            findings.append(
                {
                    "category": "waveform",
                    "severity": "warning",
                    "reference": ref,
                    "message": f"bench vs sim RMSE {rmse:g} V above warn threshold",
                    "action": "Review probe alignment and operating conditions",
                }
            )
        for scalar in entry.get("scalar_comparisons") or []:
            if scalar.get("rel_error") is not None and abs(scalar["rel_error"]) > 0.05:
                findings.append(
                    {
                        "category": "scalar",
                        "severity": "warning",
                        "reference": ref,
                        "message": (
                            f"{scalar.get('bench_metric')}: bench {scalar.get('bench_value'):g} "
                            f"vs sim {scalar.get('sim_value'):g}"
                        ),
                        "action": "Check if design model or bench conditions explain the delta",
                    }
                )

    for comp in summary.get("comparisons") or gate.get("comparisons") or []:
        if comp.get("waveform_status") == "fail":
            findings.append(
                {
                    "category": "waveform",
                    "severity": "error",
                    "reference": comp.get("component_ref"),
                    "message": f"probe {comp.get('id')}: RMSE {comp.get('rmse'):g} V",
                    "action": "Align bench capture with sim profile bench_compare",
                }
            )

    rules = summary.get("rules") or {}
    for result in rules.get("results") or []:
        if result.get("passed"):
            continue
        findings.append(
            {
                "category": "rules",
                "severity": result.get("severity", "error"),
                "message": result.get("message", ""),
                "action": f"See rule {result.get('rule_id')} evidence {result.get('evidence')}",
            }
        )

    return findings


def _lab_findings(captured_dir: Path, operating_point: dict | None) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if not captured_dir.is_dir():
        return findings

    store = LabDataStore(captured_dir)
    sessions = store.list_sessions()
    tagged = [m for m in sessions if set(m.tags) & WATCH_TRIGGER_TAGS]
    if not tagged:
        return findings

    latest = tagged[-1]
    if not latest.instruments:
        findings.append(
            {
                "category": "lab_setup",
                "severity": "warning",
                "reference": latest.component_ref,
                "message": f"session {latest.session_id} missing instrument provenance",
                "action": "Record scope/DMM/AWG bindings for audit",
            }
        )

    if operating_point:
        for dim, expected in operating_point.items():
            derived_key = dim.replace("_v", "").replace("_c", "")
            if derived_key in latest.derived:
                continue
    return findings


def _attribute(findings: list[dict[str, Any]], gate: dict | None, sim_diag: dict) -> dict[str, Any]:
    """Heuristic responsibility hints for P5."""
    design = 0
    material = 0
    test_setup = 0

    for f in findings:
        cat = f.get("category", "")
        sev = f.get("severity", "")
        if cat in ("spec", "valid_range", "stress", "checks", "ngspice"):
            design += 3 if sev == "error" else 1
        elif cat in ("waveform", "scalar"):
            design += 1
            test_setup += 2 if sev == "error" else 1
        elif cat in ("lab_setup",):
            test_setup += 2

    for entry in (gate or {}).get("entries") or []:
        if entry.get("source") == "bench" and entry.get("waveform_status") == "fail":
            material += 2

    sim_errors = (sim_diag.get("summary") or {}).get("errors", 0)
    if sim_errors:
        design += sim_errors

    scores = {"design": design, "material": material, "test_setup": test_setup}
    top = max(scores, key=scores.get)
    if scores[top] == 0:
        likely = "none"
        confidence = "low"
    else:
        likely = top
        total = sum(scores.values())
        confidence = "high" if scores[top] / total > 0.5 else "medium"

    return {
        "scores": scores,
        "likely": likely,
        "confidence": confidence,
        "hints": _attribution_hints(likely),
    }


def _attribution_hints(likely: str) -> list[str]:
    if likely == "design":
        return [
            "Check spec failures, stress limits, and sim preflight issues first",
            "Run sim diagnose and review ngspice.log",
        ]
    if likely == "material":
        return [
            "Compare latest characterize session with prior baseline",
            "Re-characterize suspect component and re-run sim",
        ]
    if likely == "test_setup":
        return [
            "Verify bench_compare probe/channel matches schematic net",
            "Confirm supply, load, and trigger match sim profile operating_point",
        ]
    return ["No strong attribution signal; gather bench session + sim report"]


def diagnose_project(
    reports_dir: Path,
    *,
    captured_dir: Path | None = None,
    gate_report_path: Path | None = None,
) -> dict[str, Any]:
    """Merge sim, gate, and lab evidence into one actionable report."""
    sim_diag = diagnose_sim(reports_dir)
    gate_path = gate_report_path or (reports_dir / "gate_report.json")
    gate = _read_json(gate_path)

    op, _ = load_sim_report_context(reports_dir / "sim" / "sim_report.json")
    lab_findings = _lab_findings(captured_dir, op) if captured_dir else []
    gate_findings = _gate_findings(gate) if gate else []
    sim_findings = sim_diag.get("findings") or []

    findings = sim_findings + gate_findings + lab_findings
    errors = sum(1 for f in findings if f.get("severity") == "error")
    warnings = sum(1 for f in findings if f.get("severity") == "warning")
    attribution = _attribute(findings, gate, sim_diag)

    actions = [f["action"] for f in findings if f.get("action")]
    ok = errors == 0 and sim_diag.get("ok", False)

    return {
        "ok": ok,
        "sim_success": sim_diag.get("sim_success"),
        "summary": {
            "errors": errors,
            "warnings": warnings,
            "findings": len(findings),
            "sim_findings": len(sim_findings),
            "gate_findings": len(gate_findings),
            "lab_findings": len(lab_findings),
        },
        "attribution": attribution,
        "findings": findings,
        "actions": actions,
        "sim_diagnose": sim_diag,
    }
