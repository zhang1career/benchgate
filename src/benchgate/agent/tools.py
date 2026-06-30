"""Agent tool registry — JSON-schema descriptions for LLM / MCP clients."""

from __future__ import annotations

from typing import Any


TOOLS: dict[str, dict[str, Any]] = {
    "mapping_sync": {
        "description": "Scan KiCad schematic and update models/manifest.yaml",
        "parameters": {
            "type": "object",
            "properties": {
                "design_dir": {"type": "string", "description": "Path to KiCad project folder"},
            },
            "required": ["design_dir"],
        },
    },
    "mapping_status": {
        "description": "Report ready/pending/unmapped components in manifest",
        "parameters": {
            "type": "object",
            "properties": {
                "design_dir": {"type": "string", "description": "Path to KiCad project folder"},
                "manifest_path": {"type": "string"},
            },
            "required": ["design_dir"],
        },
    },
    "lab_capture": {
        "description": "Step-response capture (scope + optional DMM/AWG) → fit → subckt + manifest + session",
        "parameters": {
            "type": "object",
            "properties": {
                "design_dir": {"type": "string", "description": "Path to KiCad project folder"},
                "component_ref": {"type": "string"},
                "mpn": {"type": "string"},
                "kicad_key": {"type": "string"},
                "scope": {"type": "string", "description": "Override scope instrument name"},
                "dmm": {"type": "string", "description": "Override dmm instrument name"},
                "awg": {"type": "string", "description": "Override awg/stimulus instrument name"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["design_dir", "component_ref", "mpn", "kicad_key"],
        },
    },
    "lab_read": {
        "description": "Read scalar value(s) from a measurement instrument (default role: dmm)",
        "parameters": {
            "type": "object",
            "properties": {
                "design_dir": {"type": "string"},
                "role": {"type": "string", "description": "Capability role (default: dmm)"},
                "instrument": {"type": "string", "description": "Explicit instrument name (overrides role)"},
                "count": {"type": "integer", "description": "Number of readings (default 1)"},
            },
            "required": ["design_dir"],
        },
    },
    "lab_capture_waveform": {
        "description": "Capture a single waveform from the scope (role: scope) and store a session",
        "parameters": {
            "type": "object",
            "properties": {
                "design_dir": {"type": "string"},
                "role": {"type": "string", "description": "Capability role (default: scope)"},
                "instrument": {"type": "string", "description": "Explicit instrument name"},
                "channel": {"type": "integer"},
                "component_ref": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["design_dir"],
        },
    },
    "lab_list": {
        "description": "List configured instruments and effective role bindings",
        "parameters": {
            "type": "object",
            "properties": {"design_dir": {"type": "string"}},
            "required": ["design_dir"],
        },
    },
    "lab_query_sessions": {
        "description": "List stored capture sessions filtered by component/time/tags",
        "parameters": {
            "type": "object",
            "properties": {
                "design_dir": {"type": "string"},
                "component_ref": {"type": "string"},
                "since": {"type": "string", "description": "ISO timestamp lower bound"},
                "until": {"type": "string", "description": "ISO timestamp upper bound"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["design_dir"],
        },
    },
    "lab_metric_series": {
        "description": "Extract one derived metric across sessions as a time-ordered series",
        "parameters": {
            "type": "object",
            "properties": {
                "design_dir": {"type": "string"},
                "metric": {"type": "string"},
                "component_ref": {"type": "string"},
                "since": {"type": "string"},
                "until": {"type": "string"},
            },
            "required": ["design_dir", "metric"],
        },
    },
    "lab_metric_drift": {
        "description": "Trend + stats of a derived metric across sessions (slope/s, mean, std)",
        "parameters": {
            "type": "object",
            "properties": {
                "design_dir": {"type": "string"},
                "metric": {"type": "string"},
                "component_ref": {"type": "string"},
                "since": {"type": "string"},
                "until": {"type": "string"},
            },
            "required": ["design_dir", "metric"],
        },
    },
    "lab_apply_model": {
        "description": "Write Sim.* fields to schematic and update manifest",
        "parameters": {
            "type": "object",
            "properties": {
                "design_dir": {"type": "string"},
                "reference": {"type": "string"},
                "kicad_key": {"type": "string"},
                "sim_library": {"type": "string"},
                "sim_name": {"type": "string"},
                "sim_pins": {"type": "string"},
            },
            "required": ["design_dir", "reference", "kicad_key", "sim_library", "sim_name"],
        },
    },
    "sim_run": {
        "description": "Export KiCad SPICE netlist, inject models, run ngspice",
        "parameters": {
            "type": "object",
            "properties": {
                "design_dir": {"type": "string"},
                "manifest_path": {"type": "string"},
                "output_dir": {"type": "string"},
                "profile": {"type": "string"},
            },
            "required": ["design_dir"],
        },
    },
    "sim_cosim": {
        "description": "Closed-loop cosim: compile firmware control.c, segment ngspice, validate",
        "parameters": {
            "type": "object",
            "properties": {
                "design_dir": {"type": "string"},
                "manifest_path": {"type": "string"},
                "output_dir": {"type": "string"},
                "profile": {"type": "string"},
            },
            "required": ["design_dir"],
        },
    },
    "gate_report": {
        "description": "Bench vs simulation quality report",
        "parameters": {
            "type": "object",
            "properties": {
                "design_dir": {"type": "string", "description": "Path to KiCad project folder"},
                "manifest_path": {"type": "string"},
                "output_path": {"type": "string"},
                "sim_raw_path": {"type": "string"},
            },
            "required": ["design_dir"],
        },
    },
    "watch_once": {
        "description": "Detect design changes and run mapping + optional sim pipeline",
        "parameters": {
            "type": "object",
            "properties": {
                "design_dir": {"type": "string"},
                "run_sim": {"type": "boolean"},
            },
            "required": ["design_dir"],
        },
    },
}


def list_tools() -> list[dict[str, Any]]:
    return [{"name": k, **v} for k, v in TOOLS.items()]
