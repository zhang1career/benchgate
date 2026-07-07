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
    "model_build": {
        "description": (
            "Build an ngspice subckt from a local non-bench source (e.g. an "
            "LTspice-exported .net/.cir netlist) and register it in the manifest "
            "with provenance. Global engine stays ngspice; this only supplies a model."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "design_dir": {"type": "string", "description": "Path to KiCad project folder"},
                "kicad_key": {"type": "string"},
                "reference": {"type": "string"},
                "provider": {
                    "type": "string",
                    "enum": ["ltspice", "datasheet", "bench", "vendor"],
                    "description": "Model source (default: ltspice)",
                },
                "source_file": {"type": "string", "description": "Path to .net/.cir netlist (ltspice)"},
                "lib_path": {"type": "string", "description": "Path to vendor/bench .lib"},
                "sim_name": {"type": "string", "description": "Subckt/model name to emit"},
                "mpn": {"type": "string", "description": "MPN for datasheet provider"},
                "from_meas": {
                    "type": "string",
                    "description": "Path to LTspice/ngspice .MEAS log; parsed into provenance.metrics",
                },
                "pins": {"type": "string", "description": "External pins (space-separated); required to wrap a flat netlist"},
                "valid_range": {"type": "object", "description": "Operating-range assumptions (ports/freq/temp/bias)"},
                "metrics": {"type": "object", "description": "Achieved performance metrics {name: value} from the local sim"},
                "notes": {"type": "string"},
            },
            "required": ["design_dir", "kicad_key"],
        },
    },
    "spec_set": {
        "description": (
            "Set a top-down performance budget (spec) on a component: "
            "{metric: [min, max]}. gate_report checks achieved metrics against it (pass/fail)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "design_dir": {"type": "string"},
                "kicad_key": {"type": "string"},
                "reference": {"type": "string"},
                "spec": {"type": "object", "description": "{metric: [min, max]} required performance budget"},
            },
            "required": ["design_dir", "kicad_key", "spec"],
        },
    },
    "model_status": {
        "description": "Report per-component model source/provenance and valid_range from the manifest",
        "parameters": {
            "type": "object",
            "properties": {
                "design_dir": {"type": "string"},
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
                "fail_on_preflight": {
                    "type": "boolean",
                    "description": "Abort before ngspice when preflight reports errors",
                },
            },
            "required": ["design_dir"],
        },
    },
    "sim_stress_sweep": {
        "description": "Run profile stress_sweep grid and aggregate worst-case component stress",
        "parameters": {
            "type": "object",
            "properties": {
                "design_dir": {"type": "string"},
                "manifest_path": {"type": "string"},
                "output_dir": {"type": "string"},
                "profile": {"type": "string"},
            },
            "required": ["design_dir", "profile"],
        },
    },
    "sim_sweep": {
        "description": (
            "Run a sim profile over a grid of overrides and collect one metric per point. "
            "Axes: params (override .param NAME) and sets (override an element value, e.g. R11). "
            "Metric spec is 'signal[:metric[:window_after]]' (metric: min/max/avg/rms/pp/final)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "design_dir": {"type": "string"},
                "manifest_path": {"type": "string"},
                "output_dir": {"type": "string"},
                "profile": {"type": "string"},
                "metric": {"type": "string", "description": "e.g. 'v(n_hdr):min:250u'"},
                "params": {"type": "object", "description": "{PARAM_NAME: [v1, v2, ...]} overriding .param lines"},
                "sets": {"type": "object", "description": "{REFDES: [v1, v2, ...]} overriding element values"},
                "pass_gte": {"type": "number", "description": "Mark point passed if metric >= this"},
                "pass_lte": {"type": "number", "description": "Mark point passed if metric <= this"},
            },
            "required": ["design_dir", "profile", "metric"],
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
                "operating_point": {
                    "type": "object",
                    "description": "Actual operating point (e.g. {vsupply_v, temp_c, freq_hz}) checked against each model's valid_range",
                },
                "stress_sweep": {"type": "boolean", "description": "Run profile stress_sweep first"},
                "profile": {"type": "string", "description": "sim_profiles.yaml block for stress_sweep"},
            },
            "required": ["design_dir"],
        },
    },
    "pipeline_sync": {
        "description": (
            "Agent automation: read models/blocks.yaml, build local subckt models "
            "(.net/.cir/.asc), apply spec/metrics/valid_range to manifest — no manual model build steps"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "design_dir": {"type": "string", "description": "Path to KiCad project folder"},
            },
            "required": ["design_dir"],
        },
    },
    "watch_once": {
        "description": (
            "One-shot agent pipeline: detect KiCad + blocks.yaml changes → pipeline sync "
            "(local models/spec/metrics) → mapping sync → optional sim → gate (spec + valid_range)"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "design_dir": {"type": "string"},
                "profile": {"type": "string", "description": "sim_profiles.yaml block (default default)"},
                "run_pipeline": {"type": "boolean", "description": "Sync models/blocks.yaml (default true)"},
                "run_sim": {"type": "boolean", "description": "Run ngspice when mapping ready (default true)"},
                "run_gate": {"type": "boolean", "description": "Write gate report with spec/valid_range (default true)"},
                "run_auto_capture": {
                    "type": "boolean",
                    "description": "Trigger lab capture for pending subckt entries (default true)",
                },
                "auto_capture_dry_run": {"type": "boolean", "description": "List candidates only"},
                "run_tolerance": {
                    "type": "boolean",
                    "description": "Run sim tolerance when blocks.yaml has tolerances (default true)",
                },
                "tolerance_samples": {"type": "integer", "description": "MC sample budget (default 200)"},
                "tolerance_strategy": {
                    "type": "string",
                    "enum": ["lhs", "adaptive", "sequential"],
                    "description": "Tolerance strategy when auto-run (default adaptive)",
                },
            },
            "required": ["design_dir"],
        },
    },
    "sim_diagnose": {
        "description": "Summarize simulation preflight/report/log into actionable diagnostics",
        "parameters": {
            "type": "object",
            "properties": {
                "design_dir": {"type": "string"},
            },
            "required": ["design_dir"],
        },
    },
    "sim_tolerance": {
        "description": (
            "LHS/adaptive/sequential Monte Carlo over blocks.yaml tolerances, environment, "
            "and mix (multi-provider); writes reports/mc_tolerance/mc_tolerance.json"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "design_dir": {"type": "string"},
                "profile": {"type": "string", "description": "sim_profiles.yaml block (default charge_pump)"},
                "n_samples": {"type": "integer", "description": "Sample budget (default 200)"},
                "seed": {"type": "integer", "description": "RNG seed (default 42)"},
                "strategy": {
                    "type": "string",
                    "enum": ["lhs", "adaptive", "sequential"],
                    "description": "Sampling strategy (default lhs)",
                },
                "warmup_ratio": {"type": "number", "description": "Adaptive warmup fraction (default 0.25)"},
                "surrogate_degree": {"type": "integer", "description": "Polynomial surrogate degree (default 2)"},
                "output_dir": {"type": "string"},
            },
            "required": ["design_dir"],
        },
    },
    "watch_loop": {
        "description": (
            "Continuous watch: poll KiCad + blocks.yaml; on change run pipeline → mapping → sim → gate"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "design_dir": {"type": "string"},
                "profile": {"type": "string"},
                "interval_s": {"type": "number", "description": "Poll interval seconds (default 2)"},
                "debounce_s": {"type": "number", "description": "Wait after change before next poll (default 1)"},
                "max_iterations": {"type": "integer", "description": "Stop after N polls (0 = until interrupted)"},
                "run_pipeline": {"type": "boolean"},
                "run_sim": {"type": "boolean"},
                "run_gate": {"type": "boolean"},
                "run_auto_capture": {"type": "boolean"},
                "auto_capture_dry_run": {"type": "boolean"},
                "run_tolerance": {"type": "boolean"},
                "tolerance_samples": {"type": "integer"},
                "tolerance_strategy": {"type": "string", "enum": ["lhs", "adaptive", "sequential"]},
            },
            "required": ["design_dir"],
        },
    },
}


def list_tools() -> list[dict[str, Any]]:
    return [{"name": k, **v} for k, v in TOOLS.items()]
