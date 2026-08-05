"""Agent tool registry — JSON-schema descriptions for LLM / MCP clients."""

from __future__ import annotations

from typing import Any


# Spelled out rather than imported from benchgate.sim.analysis, which would pull numpy
# into every MCP handshake. test_agent_tools keeps the two in step.
_METRIC_NAMES = (
    "min, max, avg, rms, pp, final, "
    "settling_time, settling_time_01pct, settling_time_001pct, "
    "overshoot_pct, slew_rate, integral, charge_nc, "
    "bw_3db, peaking_db, gain_db_max, gain_db_first"
)

TOOLS: dict[str, dict[str, Any]] = {
    "benchgate_version": {
        "description": (
            "Return benchgate version, install path, and registered MCP tool count "
            "for stale-server detection after local edits"
        ),
        "parameters": {"type": "object", "properties": {}},
    },
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
    "lab_sa_sweep": {
        "description": "Capture a spectrum sweep from the bound SA (role: sa; SA8/tinySA/…) and store a session",
        "parameters": {
            "type": "object",
            "properties": {
                "design_dir": {"type": "string"},
                "instrument": {"type": "string", "description": "Explicit instrument name"},
                "center_mhz": {"type": "number"},
                "span_mhz": {"type": "number"},
                "start_mhz": {"type": "number"},
                "stop_mhz": {"type": "number"},
                "reference_dbm": {"type": "number"},
                "attenuation": {"type": "integer"},
                "component_ref": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["design_dir"],
        },
    },
    "lab_sa_peak": {
        "description": "Read on-screen peak from the bound SA (role: sa)",
        "parameters": {
            "type": "object",
            "properties": {
                "design_dir": {"type": "string"},
                "instrument": {"type": "string"},
                "mode": {"type": "string", "enum": ["AVR", "MIN", "MID", "RMS"], "description": "Peak statistic"},
            },
            "required": ["design_dir"],
        },
    },
    "lab_sa_floor": {
        "description": "Read noise floor from the bound SA (role: sa)",
        "parameters": {
            "type": "object",
            "properties": {
                "design_dir": {"type": "string"},
                "instrument": {"type": "string"},
            },
            "required": ["design_dir"],
        },
    },
    "lab_sa_gen": {
        "description": "Configure the bound RF/tracking generator (role: rfgen; SA8/tinySA/…)",
        "parameters": {
            "type": "object",
            "properties": {
                "design_dir": {"type": "string"},
                "instrument": {"type": "string"},
                "enabled": {"type": "boolean"},
                "frequency_mhz": {"type": "number"},
                "power_dbm": {"type": "integer"},
                "attenuator": {"type": "integer", "description": "Digital attenuator 0..63"},
            },
            "required": ["design_dir"],
        },
    },
    "lab_sa_cal": {
        "description": "Start/stop S-parameter calibration on the bound VNA (role: vna; SA8)",
        "parameters": {
            "type": "object",
            "properties": {
                "design_dir": {"type": "string"},
                "instrument": {"type": "string"},
                "param": {"type": "string", "enum": ["S11", "S21", "SWR"]},
                "standard": {"type": "string", "enum": ["SHORT", "OPEN", "LOAD"]},
                "enabled": {"type": "boolean", "description": "Enable (true) or disable (false) calibration"},
            },
            "required": ["design_dir", "param"],
        },
    },
    "lab_sa_sparam": {
        "description": "Capture an S-parameter history trace from the bound VNA (role: vna; SA8)",
        "parameters": {
            "type": "object",
            "properties": {
                "design_dir": {"type": "string"},
                "instrument": {"type": "string"},
                "param": {"type": "string", "enum": ["S11", "S21", "SWR"], "description": "Trace label (default S21)"},
                "component_ref": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
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
            "Run a sim profile over a grid of overrides and collect metrics per point. "
            "Axes: params (override .param NAME) and sets (override an element value, e.g. R11). "
            "Metric spec is '[name=]signal[:metric[:window_after]]'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "design_dir": {"type": "string"},
                "manifest_path": {"type": "string"},
                "output_dir": {"type": "string"},
                "profile": {"type": "string"},
                "metric": {"type": "string", "description": "single metric, e.g. 'v(n_hdr):min:250u'"},
                "metrics": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "several metrics off one run per point; the first decides pass/fail. "
                        f"metric names: {_METRIC_NAMES}"
                    ),
                },
                "params": {"type": "object", "description": "{PARAM_NAME: [v1, v2, ...]} overriding .param lines"},
                "sets": {"type": "object", "description": "{REFDES: [v1, v2, ...]} overriding element values"},
                "pass_gte": {"type": "number", "description": "Mark point passed if metric >= this"},
                "pass_lte": {"type": "number", "description": "Mark point passed if metric <= this"},
            },
            "required": ["design_dir", "profile"],
        },
    },
    "sim_block_sweep": {
        "description": (
            "Sweep a standalone block testbench .cir over a grid of overrides — no KiCad "
            "project and no sim_profiles.yaml needed. Use this to characterise a block from "
            "models/blocks/ on its own (and to produce the numbers that go into blocks.yaml "
            "metrics) before the board has a schematic. The testbench owns its stimulus and "
            ".control block, so an AC run is just an 'ac dec ...' line in it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "design_dir": {"type": "string"},
                "netlist": {
                    "type": "string",
                    "description": "Testbench .cir, absolute or relative to design_dir",
                },
                "output_dir": {"type": "string"},
                "metrics": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "'[name=]signal[:metric[:window_after]]', e.g. 'bw=v(com):bw_3db'. "
                        "The first decides pass/fail. All are evaluated off one run per point. "
                        f"metric names: {_METRIC_NAMES}"
                    ),
                },
                "params": {"type": "object", "description": "{PARAM_NAME: [v1, v2, ...]} overriding .param lines"},
                "sets": {"type": "object", "description": "{REFDES: [v1, v2, ...]} overriding element values"},
                "pass_gte": {"type": "number", "description": "Mark point passed if first metric >= this"},
                "pass_lte": {"type": "number", "description": "Mark point passed if first metric <= this"},
            },
            "required": ["design_dir", "netlist", "metrics"],
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
                "rules": {
                    "type": "string",
                    "enum": ["auto", "none"],
                    "description": "auto (default) = $BENCHGATE_HOME/config/rules + design models/rules; none = skip rule packs and report spec only",
                },
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
    "diagnose": {
        "description": (
            "Unified diagnosis: sim preflight/log + gate spec/waveform + lab sessions "
            "with design/material/test_setup attribution"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "design_dir": {"type": "string"},
                "gate_report_path": {"type": "string"},
            },
            "required": ["design_dir"],
        },
    },
    "lab_compare_waveforms": {
        "description": "Compare one bench session waveform against a sim CSV (RMSE, correlation)",
        "parameters": {
            "type": "object",
            "properties": {
                "design_dir": {"type": "string"},
                "session_id": {"type": "string"},
                "sim_csv": {"type": "string", "description": "Path under reports/sim/ or absolute"},
                "bench_channel": {"type": "string", "description": "Default scope_ch1"},
            },
            "required": ["design_dir", "session_id", "sim_csv"],
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
                    "enum": ["lhs", "adaptive", "sequential", "auto"],
                    "description": "Sampling strategy (default lhs); auto = sequential + parallel + coarse/fine",
                },
                "warmup_ratio": {"type": "number", "description": "Adaptive warmup fraction (default 0.25)"},
                "surrogate_degree": {"type": "integer", "description": "Polynomial surrogate degree (default 2)"},
                "sequential_batch": {"type": "integer"},
                "sequential_ci_width": {"type": "number"},
                "sequential_min_samples": {"type": "integer"},
                "jobs": {"type": "integer", "description": "Parallel workers (0=cpu_count-1)"},
                "sim_tier": {"type": "string", "enum": ["auto", "coarse", "fine"]},
                "tran_step": {"type": "string"},
                "tran_stop": {"type": "string"},
                "maxstep": {"type": "string"},
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
                "tolerance_strategy": {"type": "string", "enum": ["lhs", "adaptive", "sequential", "auto"]},
                "tolerance_jobs": {"type": "integer", "description": "Parallel MC workers (0=auto)"},
            },
            "required": ["design_dir"],
        },
    },
}


def list_tools() -> list[dict[str, Any]]:
    return [{"name": k, **v} for k, v in TOOLS.items()]
