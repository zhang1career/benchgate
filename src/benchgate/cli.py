"""Command-line interface for benchgate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from benchgate import __version__
from benchgate.agent.dispatch import dispatch
from benchgate.agent.tools import list_tools
from benchgate.io.manifest import load_manifest
from benchgate.mapping.engine import mapping_status, sync_project
from benchgate.paths import benchgate_paths, resolve_project_path


CLI_DESCRIPTION = (
    "Design–verification loop for KiCad 10: lab capture and local block models "
    "→ manifest → ngspice batch sim → quality gate (spec / valid_range / RMSE)."
)

DOCS_EPILOG = """\
documentation (in the benchgate install / source tree):
  README.md                 workflows (top-down / bottom-up)
  docs/examples/blocks.yaml local block automation
  docs/MINIMUM_SCOPE.md     architecture and agent tools"""

DESIGN_ARG_HELP = (
    "KiCad project root (contains *.kicad_pro). "
    "Default: 'design' relative to the current working directory — "
    "when run outside a project, pass an absolute path, e.g. "
    "--design /path/to/myboard"
)

ROOT_EPILOG = f"""\
examples:
  benchgate mapping sync --design /path/to/myboard
  benchgate watch once --design /path/to/myboard
  benchgate pipeline sync --design /path/to/myboard

{DOCS_EPILOG}"""

WATCH_ONCE_EPILOG = f"""\
Prerequisites (top-down): edit <design>/models/blocks.yaml and add blocks/*.net
and *.metrics.json (see docs/examples/blocks.yaml).

example:
  benchgate watch once --design /path/to/myboard

{DOCS_EPILOG}"""

PIPELINE_SYNC_EPILOG = f"""\
Read <design>/models/blocks.yaml → build subckts, apply spec/metrics → manifest.
Does not run mapping sync, sim, or gate (use watch once for the full pipeline).

example:
  benchgate pipeline sync --design /path/to/myboard

{DOCS_EPILOG}"""


def _help_formatter() -> type[argparse.RawDescriptionHelpFormatter]:
    return argparse.RawDescriptionHelpFormatter


def _add_design_arg(parser: argparse.ArgumentParser, *, default: str = "design") -> None:
    parser.add_argument("--design", default=default, help=DESIGN_ARG_HELP)


def _paths(args: argparse.Namespace):
    return benchgate_paths(args.design, manifest=getattr(args, "manifest", None))


def cmd_mapping_sync(args: argparse.Namespace) -> int:
    p = _paths(args)
    manifest = sync_project(
        p.design,
        p.manifest,
        p.models,
        subckt_dir=p.subckt,
        global_models_dir=p.global_models,
    )
    print(json.dumps(mapping_status(manifest), indent=2, ensure_ascii=False))
    return 0


def cmd_mapping_status(args: argparse.Namespace) -> int:
    p = _paths(args)
    manifest = load_manifest(p.manifest, global_models_dir=p.global_models)
    print(json.dumps(mapping_status(manifest), indent=2, ensure_ascii=False))
    return 0


def cmd_sim_run(args: argparse.Namespace) -> int:
    p = _paths(args)
    out_dir = resolve_project_path(p.design, args.out, p.reports / "sim")
    result = dispatch(
        "sim_run",
        {
            "design_dir": str(p.design),
            "manifest_path": str(p.manifest),
            "output_dir": str(out_dir),
            "profile": args.profile,
            "fail_on_preflight": args.fail_on_preflight,
        },
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("success") else 1


def cmd_sim_preflight(args: argparse.Namespace) -> int:
    from benchgate.sim.pipeline import run_preflight_only

    p = _paths(args)
    out_dir = resolve_project_path(p.design, args.out, p.reports / "sim")
    report = run_preflight_only(
        p.design,
        p.manifest,
        out_dir,
        sim_profile_path=p.sim_profile,
        profile=args.profile,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report.get("passed") else 1


def cmd_sim_stress_sweep(args: argparse.Namespace) -> int:
    from benchgate.sim.stress_sweep import run_stress_sweep

    p = _paths(args)
    out_dir = resolve_project_path(p.design, args.out, p.reports / "stress_sweep")
    report = run_stress_sweep(
        p.design,
        p.manifest,
        out_dir,
        sim_profile_path=p.sim_profile,
        profile=args.profile,
    )
    payload = report.to_dict()
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    worst = payload.get("worst") or {}
    return 0 if worst.get("passed", False) else 1


def cmd_sim_sweep(args: argparse.Namespace) -> int:
    from benchgate.sim.sweep import parse_axis

    p = _paths(args)
    out_dir = resolve_project_path(p.design, args.out, p.reports / "sim_sweep")
    params: dict[str, list[str]] = {}
    sets: dict[str, list[str]] = {}
    for spec in args.param or []:
        name, values = parse_axis(spec)
        params[name] = values
    for spec in args.set or []:
        name, values = parse_axis(spec)
        sets[name] = values
    call_args: dict = {
        "design_dir": str(p.design),
        "manifest_path": str(p.manifest),
        "output_dir": str(out_dir),
        "profile": args.profile,
        "metric": args.metric,
        "params": params,
        "sets": sets,
    }
    if args.pass_gte is not None:
        call_args["pass_gte"] = args.pass_gte
    if args.pass_lte is not None:
        call_args["pass_lte"] = args.pass_lte
    result = dispatch("sim_sweep", call_args)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def cmd_sim_diagnose(args: argparse.Namespace) -> int:
    result = dispatch("sim_diagnose", {"design_dir": args.design})
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def cmd_sim_tolerance(args: argparse.Namespace) -> int:
    from benchgate.sim.tolerance import run_tolerance_study

    p = _paths(args)
    out_dir = resolve_project_path(p.design, args.out, p.reports / "mc_tolerance")
    report = run_tolerance_study(
        p.design,
        p.manifest,
        out_dir,
        blocks_yaml=p.blocks_yaml,
        sim_profile_path=p.sim_profile,
        profile=args.profile,
        n_samples=args.samples,
        seed=args.seed,
        strategy=args.strategy,
        warmup_ratio=args.warmup_ratio,
        surrogate_degree=args.surrogate_degree,
        sequential_batch=args.sequential_batch,
        sequential_ci_width=args.sequential_ci_width,
        sequential_min_samples=args.sequential_min_samples,
    )
    payload = report.to_dict()
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def cmd_sim_cosim(args: argparse.Namespace) -> int:
    p = _paths(args)
    out_dir = resolve_project_path(p.design, args.out, p.reports / "sim_cosim")
    result = dispatch(
        "sim_cosim",
        {
            "design_dir": str(p.design),
            "manifest_path": str(p.manifest),
            "output_dir": str(out_dir),
            "profile": args.profile,
        },
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("success") else 1


def cmd_watch_once(args: argparse.Namespace) -> int:
    params: dict = {
        "design_dir": args.design,
        "run_sim": not args.no_sim,
        "profile": args.profile,
    }
    if args.no_pipeline:
        params["run_pipeline"] = False
    if args.no_gate:
        params["run_gate"] = False
    if args.no_auto_capture:
        params["run_auto_capture"] = False
    if args.auto_capture_dry_run:
        params["auto_capture_dry_run"] = True
    if args.no_tolerance:
        params["run_tolerance"] = False
    if getattr(args, "tolerance_strategy", None):
        params["tolerance_strategy"] = args.tolerance_strategy
    if getattr(args, "tolerance_samples", None):
        params["tolerance_samples"] = args.tolerance_samples
    result = dispatch("watch_once", params)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0


def cmd_watch_loop(args: argparse.Namespace) -> int:
    from benchgate.paths import benchgate_paths
    from benchgate.watch.loop import watch_loop

    p = benchgate_paths(args.design)
    state_path = p.state
    max_iter = args.max_iterations if args.max_iterations > 0 else None
    result = watch_loop(
        p.design,
        manifest_path=p.manifest,
        models_dir=p.models,
        reports_dir=p.reports,
        state_path=state_path,
        sim_profile_path=p.sim_profile,
        profile=args.profile,
        subckt_dir=p.subckt,
        global_models_dir=p.global_models,
        blocks_yaml=p.blocks_yaml,
        tmp_dir=p.tmp_root / "pipeline",
        run_pipeline=not args.no_pipeline,
        run_sim=not args.no_sim,
        run_gate=not args.no_gate,
        run_auto_capture=not args.no_auto_capture,
        auto_capture_dry_run=args.auto_capture_dry_run,
        run_tolerance=not args.no_tolerance,
        tolerance_samples=args.tolerance_samples,
        tolerance_strategy=args.tolerance_strategy,
        interval_s=args.interval,
        debounce_s=args.debounce,
        max_iterations=max_iter,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0


def cmd_pipeline_sync(args: argparse.Namespace) -> int:
    result = dispatch("pipeline_sync", {"design_dir": args.design})
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0


def cmd_gate_report(args: argparse.Namespace) -> int:
    p = _paths(args)
    out = resolve_project_path(p.design, args.out, p.reports / "gate_report.json")
    params: dict = {"design_dir": str(p.design), "manifest_path": str(p.manifest), "output_path": str(out)}
    if args.sim_raw:
        params["sim_raw_path"] = str(resolve_project_path(p.design, args.sim_raw, p.reports / "sim" / "sim_waveform.csv"))
    if args.operating_point:
        params["operating_point"] = json.loads(args.operating_point)
    if args.stress_sweep:
        params["stress_sweep"] = True
        params["profile"] = args.profile
    if getattr(args, "rules", "auto") == "none":
        params["rules"] = "none"
    result = dispatch("gate_report", params)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def cmd_kicad_sim_fields(args: argparse.Namespace) -> int:
    from benchgate.kicad.sim_fields_safe import apply_sim_fields_safe

    p = _paths(args)
    sch = resolve_project_path(p.design, args.schematic, p.design / f"{p.design.name}.kicad_sch")
    if not sch.exists():
        for candidate in p.design.glob("*.kicad_sch"):
            sch = candidate
            break
    changed = apply_sim_fields_safe(
        sch,
        args.reference,
        sim_library=args.library,
        sim_name=args.sim_name,
        sim_pins=args.pins or "",
    )
    print(json.dumps({"schematic": str(sch), "reference": args.reference, "updated": changed}, indent=2))
    return 0


def _lab_overrides(args: argparse.Namespace) -> dict:
    out = {}
    for role in ("scope", "dmm", "awg"):
        val = getattr(args, role, None)
        if val:
            out[role] = val
    return out


def cmd_lab_list(args: argparse.Namespace) -> int:
    result = dispatch("lab_list", {"design_dir": args.design})
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def _reading_dict(r) -> dict:
    return {
        "value": r.value,
        "unit": r.unit,
        "quantity": r.quantity.value,
        "normalized_value": r.normalized_value,
        "normalized_unit": r.normalized_unit,
        "flags": {k: v for k, v in r.flags.items() if v},
        "timestamp": r.timestamp.isoformat(),
    }


def cmd_lab_read(args: argparse.Namespace) -> int:
    if args.continuous:
        import time

        from benchgate.instruments import load_bench

        p = benchgate_paths(args.design)
        bench = load_bench(p.instruments, project_lab_path=p.lab_config)
        inst = bench.select(role=args.role, instrument=args.instrument)
        try:
            while True:
                print(json.dumps(_reading_dict(inst.read()), ensure_ascii=False), flush=True)
                time.sleep(max(0.0, args.interval))
        except KeyboardInterrupt:
            pass
        finally:
            inst.disconnect()
        return 0

    params = {"design_dir": args.design, "role": args.role, "count": args.count}
    if args.instrument:
        params["instrument"] = args.instrument
    result = dispatch("lab_read", params)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def cmd_lab_capture(args: argparse.Namespace) -> int:
    params: dict = {"design_dir": args.design, "role": args.role}
    if args.instrument:
        params["instrument"] = args.instrument
    if args.channel is not None:
        params["channel"] = args.channel
    if args.component_ref:
        params["component_ref"] = args.component_ref
    result = dispatch("lab_capture_waveform", params)
    if args.out:
        from benchgate.lab.store import LabDataStore, dump_waveform_csv

        p = benchgate_paths(args.design)
        wf = LabDataStore(p.captured).load_waveform(result["session_id"], "scope_ch1")
        Path(args.out).write_text(dump_waveform_csv(wf), encoding="utf-8")
        result["csv"] = args.out
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def cmd_lab_characterize(args: argparse.Namespace) -> int:
    params = {
        "design_dir": args.design,
        "component_ref": args.component_ref,
        "mpn": args.mpn,
        "kicad_key": args.kicad_key,
        **_lab_overrides(args),
    }
    result = dispatch("lab_capture", params)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0


def cmd_lab_query_sessions(args: argparse.Namespace) -> int:
    params = {"design_dir": args.design}
    for key in ("component_ref", "since", "until"):
        val = getattr(args, key)
        if val:
            params[key] = val
    result = dispatch("lab_query_sessions", params)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def cmd_lab_query_metric(args: argparse.Namespace) -> int:
    params = {"design_dir": args.design, "metric": args.metric}
    for key in ("component_ref", "since", "until"):
        val = getattr(args, key)
        if val:
            params[key] = val
    result = dispatch("lab_metric_series", params)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def cmd_lab_query_drift(args: argparse.Namespace) -> int:
    params = {"design_dir": args.design, "metric": args.metric}
    for key in ("component_ref", "since", "until"):
        val = getattr(args, key)
        if val:
            params[key] = val
    result = dispatch("lab_metric_drift", params)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def cmd_lab_query_waveform(args: argparse.Namespace) -> int:
    from benchgate.lab.store import LabDataStore, dump_waveform_csv

    p = benchgate_paths(args.design)
    store = LabDataStore(p.captured)
    wf = store.load_waveform(args.session, args.channel, t_start=args.t_start, t_end=args.t_end)
    if args.out:
        Path(args.out).write_text(dump_waveform_csv(wf), encoding="utf-8")
        print(json.dumps({"session_id": args.session, "channel": args.channel, "samples": len(wf), "csv": args.out}, indent=2))
    else:
        print(json.dumps({"session_id": args.session, "channel": args.channel, "samples": len(wf), "sample_rate_hz": wf.sample_rate_hz}, indent=2))
    return 0


def cmd_model_build(args: argparse.Namespace) -> int:
    params: dict = {
        "design_dir": args.design,
        "kicad_key": args.kicad_key,
        "provider": args.provider,
    }
    if args.source_file:
        params["source_file"] = args.source_file
    if args.sim_name:
        params["sim_name"] = args.sim_name
    if args.mpn:
        params["mpn"] = args.mpn
    if args.from_meas:
        params["from_meas"] = args.from_meas
    if args.lib:
        params["lib_path"] = args.lib
    if args.reference:
        params["reference"] = args.reference
    if args.pins:
        params["pins"] = args.pins
    if args.notes:
        params["notes"] = args.notes
    if args.valid_range:
        params["valid_range"] = json.loads(args.valid_range)
    if args.metrics:
        params["metrics"] = json.loads(args.metrics)
    result = dispatch("model_build", params)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def cmd_spec_set(args: argparse.Namespace) -> int:
    params: dict = {
        "design_dir": args.design,
        "kicad_key": args.kicad_key,
        "spec": json.loads(args.spec),
    }
    if args.reference:
        params["reference"] = args.reference
    result = dispatch("spec_set", params)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def cmd_model_status(args: argparse.Namespace) -> int:
    params: dict = {"design_dir": args.design}
    if args.manifest:
        params["manifest_path"] = args.manifest
    result = dispatch("model_status", params)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def cmd_agent_tools(_: argparse.Namespace) -> int:
    print(json.dumps(list_tools(), indent=2, ensure_ascii=False))
    return 0


def cmd_agent_call(args: argparse.Namespace) -> int:
    params = json.loads(args.params) if args.params else {}
    result = dispatch(args.tool, params)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0


def cmd_mcp_serve(_: argparse.Namespace) -> int:
    from benchgate.mcp_server import main as mcp_main

    mcp_main()
    return 0


def main(argv: list[str] | None = None) -> int:
    from benchgate.instruments.errors import CapabilityError, ConfigError, InstrumentError

    parser = argparse.ArgumentParser(
        prog="benchgate",
        description=CLI_DESCRIPTION,
        epilog=ROOT_EPILOG,
        formatter_class=_help_formatter(),
    )
    parser.add_argument("--version", action="version", version=f"benchgate {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")

    p_map = sub.add_parser("mapping", help="KiCad → manifest mapping")
    map_sub = p_map.add_subparsers(dest="map_cmd", required=True)
    ms = map_sub.add_parser("sync", help="Sync schematic → manifest.yaml")
    _add_design_arg(ms)
    ms.add_argument("--manifest", default=None, help="Override manifest path (default: <design>/models/manifest.yaml)")
    ms.set_defaults(func=cmd_mapping_sync)
    mst = map_sub.add_parser("status")
    _add_design_arg(mst)
    mst.add_argument("--manifest", default=None, help="Override manifest path (default: <design>/models/manifest.yaml)")
    mst.set_defaults(func=cmd_mapping_status)

    p_sim = sub.add_parser("sim", help="ngspice simulation")
    sim_sub = p_sim.add_subparsers(dest="sim_cmd", required=True)
    sr = sim_sub.add_parser("run")
    _add_design_arg(sr)
    sr.add_argument("--manifest", default=None, help="Override manifest path (default: <design>/models/manifest.yaml)")
    sr.add_argument("--out", default=None, help="Output directory (default: <design>/reports/sim)")
    sr.add_argument("--profile", default="default")
    sr.add_argument(
        "--fail-on-preflight",
        action="store_true",
        help="Abort before ngspice when preflight reports errors",
    )
    sr.set_defaults(func=cmd_sim_run)
    spf = sim_sub.add_parser(
        "preflight",
        help="Export/prepare netlist and run preflight checks without ngspice",
    )
    _add_design_arg(spf)
    spf.add_argument("--manifest", default=None, help="Override manifest path")
    spf.add_argument("--out", default=None, help="Output directory (default: <design>/reports/sim)")
    spf.add_argument("--profile", default="default")
    spf.set_defaults(func=cmd_sim_preflight)
    ssw = sim_sub.add_parser(
        "stress-sweep",
        help="Sweep profile stress_sweep axes and aggregate worst-case component stress",
    )
    _add_design_arg(ssw)
    ssw.add_argument("--manifest", default=None, help="Override manifest path")
    ssw.add_argument("--out", default=None, help="Output directory (default: <design>/reports/stress_sweep)")
    ssw.add_argument("--profile", default="default")
    ssw.set_defaults(func=cmd_sim_stress_sweep)
    sw = sim_sub.add_parser(
        "sweep",
        help="Run a profile over a grid of param/component overrides; collect one metric per point",
    )
    _add_design_arg(sw)
    sw.add_argument("--manifest", default=None, help="Override manifest path")
    sw.add_argument("--out", default=None, help="Output directory (default: <design>/reports/sim_sweep)")
    sw.add_argument("--profile", default="default")
    sw.add_argument("--metric", required=True, help="signal[:metric[:window_after]], e.g. 'v(n_hdr):min:250u'")
    sw.add_argument("--param", action="append", default=[], help="Sweep a .param: NAME=v1,v2,... (repeatable)")
    sw.add_argument("--set", action="append", default=[], help="Sweep an element value: REF=v1,v2,... (repeatable)")
    sw.add_argument("--pass-gte", dest="pass_gte", type=float, default=None, help="Mark metric>=X as pass")
    sw.add_argument("--pass-lte", dest="pass_lte", type=float, default=None, help="Mark metric<=X as pass")
    sw.set_defaults(func=cmd_sim_sweep)
    sc = sim_sub.add_parser("cosim", help="Closed-loop cosim with firmware control.c")
    _add_design_arg(sc)
    sc.add_argument("--manifest", default=None, help="Override manifest path (default: <design>/models/manifest.yaml)")
    sc.add_argument("--out", default=None, help="Output directory (default: <design>/reports/sim_cosim)")
    sc.add_argument("--profile", default="hbridge_pwm_closed")
    sc.set_defaults(func=cmd_sim_cosim)
    sd = sim_sub.add_parser(
        "diagnose",
        help="Summarize preflight/sim_report/ngspice.log into actionable findings",
    )
    _add_design_arg(sd)
    sd.set_defaults(func=cmd_sim_diagnose)
    stol = sim_sub.add_parser(
        "tolerance",
        help="LHS tolerance study over blocks.yaml tolerances (Monte Carlo M1)",
    )
    _add_design_arg(stol)
    stol.add_argument("--manifest", default=None, help="Override manifest path")
    stol.add_argument("--out", default=None, help="Output directory (default: reports/mc_tolerance)")
    stol.add_argument("--profile", default="charge_pump")
    stol.add_argument("--samples", type=int, default=200, help="LHS sample count (default 200)")
    stol.add_argument("--seed", type=int, default=42)
    stol.add_argument(
        "--strategy",
        choices=["lhs", "adaptive", "sequential"],
        default="lhs",
        help="lhs | adaptive (warmup+refine) | sequential (CI stopping, M3+)",
    )
    stol.add_argument(
        "--warmup-ratio",
        type=float,
        default=0.25,
        help="Fraction of samples for adaptive warmup phase (default 0.25)",
    )
    stol.add_argument("--surrogate-degree", type=int, default=2, help="Surrogate poly degree (1=linear, 2=default)")
    stol.add_argument("--sequential-batch", type=int, default=25, help="Batch size for --strategy sequential")
    stol.add_argument("--sequential-ci-width", type=float, default=5.0, help="Stop when Wilson yield CI width <= pct")
    stol.add_argument("--sequential-min-samples", type=int, default=50, help="Min samples before sequential stop")
    stol.set_defaults(func=cmd_sim_tolerance)

    p_kicad = sub.add_parser("kicad", help="KiCad schematic utilities (KiCad 10-safe)")
    kicad_sub = p_kicad.add_subparsers(dest="kicad_cmd", required=True)
    ks = kicad_sub.add_parser("sim-fields", help="Set Sim.Library/Name/Pins via text edit (no Schematic.save)")
    _add_design_arg(ks)
    ks.add_argument("--reference", required=True, help="Component reference, e.g. U1")
    ks.add_argument("--library", required=True, help="Sim.Library path")
    ks.add_argument("--sim-name", dest="sim_name", required=True, help="Sim.Name subckt/model name")
    ks.add_argument("--pins", default="", help="Sim.Pins pin map")
    ks.add_argument("--schematic", default=None, help="Override .kicad_sch path")
    ks.set_defaults(func=cmd_kicad_sim_fields)

    p_watch = sub.add_parser("watch", help="Agent pipeline: blocks.yaml + KiCad change triggers")
    watch_sub = p_watch.add_subparsers(dest="watch_cmd", required=True)
    wo = watch_sub.add_parser(
        "once",
        help="Pipeline sync → mapping → sim → gate",
        epilog=WATCH_ONCE_EPILOG,
        formatter_class=_help_formatter(),
    )
    _add_design_arg(wo)
    wo.add_argument("--no-sim", action="store_true", help="Skip ngspice batch sim")
    wo.add_argument("--no-pipeline", action="store_true", help="Skip models/blocks.yaml sync")
    wo.add_argument("--no-gate", action="store_true", help="Skip gate report (spec + valid_range)")
    wo.add_argument("--no-auto-capture", action="store_true", help="Skip auto lab capture for pending parts")
    wo.add_argument(
        "--auto-capture-dry-run",
        action="store_true",
        help="List pending auto-capture candidates without calling lab",
    )
    wo.add_argument("--no-tolerance", action="store_true", help="Skip sim tolerance when blocks.yaml defines tolerances")
    wo.add_argument("--tolerance-strategy", default="adaptive", choices=["lhs", "adaptive", "sequential"])
    wo.add_argument("--tolerance-samples", type=int, default=200)
    wo.add_argument("--profile", default="default", help="sim_profiles.yaml block name")
    wo.set_defaults(func=cmd_watch_once)
    wl = watch_sub.add_parser(
        "loop",
        help="Continuously watch KiCad/blocks changes and run watch_once pipeline",
    )
    _add_design_arg(wl)
    wl.add_argument("--no-sim", action="store_true", help="Skip ngspice batch sim")
    wl.add_argument("--no-pipeline", action="store_true", help="Skip models/blocks.yaml sync")
    wl.add_argument("--no-gate", action="store_true", help="Skip gate report")
    wl.add_argument("--no-auto-capture", action="store_true", help="Skip auto lab capture for pending parts")
    wl.add_argument("--auto-capture-dry-run", action="store_true", help="Dry-run auto capture only")
    wl.add_argument("--no-tolerance", action="store_true", help="Skip sim tolerance when blocks.yaml defines tolerances")
    wl.add_argument("--tolerance-strategy", default="adaptive", choices=["lhs", "adaptive", "sequential"])
    wl.add_argument("--tolerance-samples", type=int, default=200)
    wl.add_argument("--profile", default="default", help="sim_profiles.yaml block name")
    wl.add_argument("--interval", type=float, default=2.0, help="Seconds between polls (default 2)")
    wl.add_argument("--debounce", type=float, default=1.0, help="Extra wait after a change batch (default 1)")
    wl.add_argument(
        "--max-iterations",
        type=int,
        default=0,
        help="Stop after N polls (0 = run until interrupted)",
    )
    wl.set_defaults(func=cmd_watch_loop)

    p_pipeline = sub.add_parser("pipeline", help="Agent automation from models/blocks.yaml")
    pipeline_sub = p_pipeline.add_subparsers(dest="pipeline_cmd", required=True)
    ps = pipeline_sub.add_parser(
        "sync",
        help="Build local subckt models + spec/metrics from blocks.yaml (no manual model build)",
        epilog=PIPELINE_SYNC_EPILOG,
        formatter_class=_help_formatter(),
    )
    _add_design_arg(ps)
    ps.set_defaults(func=cmd_pipeline_sync)

    p_gate = sub.add_parser("gate", help="Bench vs sim quality")
    gate_sub = p_gate.add_subparsers(dest="gate_cmd", required=True)
    gr = gate_sub.add_parser("report")
    _add_design_arg(gr)
    gr.add_argument("--manifest", default=None, help="Override manifest path (default: <design>/models/manifest.yaml)")
    gr.add_argument("--out", default=None, help="Output JSON path (default: <design>/reports/gate_report.json)")
    gr.add_argument("--sim-raw", default=None)
    gr.add_argument(
        "--operating-point",
        dest="operating_point",
        default=None,
        help='JSON of actual operating point for valid_range checks, e.g. \'{"vsupply_v":5.0,"temp_c":25}\'',
    )
    gr.add_argument(
        "--stress-sweep",
        action="store_true",
        help="Run profile stress_sweep before writing gate report",
    )
    gr.add_argument("--profile", default="default", help="sim_profiles.yaml block for stress_sweep")
    gr.add_argument(
        "--rules",
        choices=["auto", "none"],
        default="auto",
        help="Rule packs for sign-off: auto=corp-derating + design models/rules (default auto)",
    )
    gr.set_defaults(func=cmd_gate_report)

    p_lab = sub.add_parser("lab", help="Instrument control: capture, read, query")
    lab_sub = p_lab.add_subparsers(dest="lab_cmd", required=True)

    ll = lab_sub.add_parser("list", help="List instruments and effective role bindings")
    _add_design_arg(ll)
    ll.set_defaults(func=cmd_lab_list)

    lr = lab_sub.add_parser("read", help="Read scalar value(s) (default role: dmm)")
    _add_design_arg(lr)
    lr.add_argument("--role", default="dmm")
    lr.add_argument("--instrument", default=None, help="Explicit instrument name (overrides role)")
    lr.add_argument("--count", type=int, default=1)
    lr.add_argument("--continuous", action="store_true", help="Stream readings until Ctrl-C")
    lr.add_argument("--interval", type=float, default=0.25, help="Seconds between continuous reads")
    lr.set_defaults(func=cmd_lab_read)

    lc = lab_sub.add_parser("capture", help="Capture a waveform (default role: scope)")
    _add_design_arg(lc)
    lc.add_argument("--role", default="scope")
    lc.add_argument("--instrument", default=None)
    lc.add_argument("--channel", type=int, default=None)
    lc.add_argument("--component-ref", dest="component_ref", default=None)
    lc.add_argument("--out", default=None, help="Export captured waveform to CSV")
    lc.set_defaults(func=cmd_lab_capture)

    lch = lab_sub.add_parser("characterize", help="Capture + fit + write subckt/manifest")
    _add_design_arg(lch)
    lch.add_argument("--component-ref", dest="component_ref", required=True)
    lch.add_argument("--mpn", required=True)
    lch.add_argument("--kicad-key", dest="kicad_key", required=True)
    lch.add_argument("--scope", default=None)
    lch.add_argument("--dmm", default=None)
    lch.add_argument("--awg", default=None)
    lch.set_defaults(func=cmd_lab_characterize)

    lq = lab_sub.add_parser("query", help="Query stored sessions / metrics / waveforms")
    lq_sub = lq.add_subparsers(dest="lab_query_cmd", required=True)
    lqs = lq_sub.add_parser("sessions")
    _add_design_arg(lqs)
    lqs.add_argument("--component-ref", dest="component_ref", default=None)
    lqs.add_argument("--since", default=None)
    lqs.add_argument("--until", default=None)
    lqs.set_defaults(func=cmd_lab_query_sessions)
    lqm = lq_sub.add_parser("metric")
    _add_design_arg(lqm)
    lqm.add_argument("--metric", required=True)
    lqm.add_argument("--component-ref", dest="component_ref", default=None)
    lqm.add_argument("--since", default=None)
    lqm.add_argument("--until", default=None)
    lqm.set_defaults(func=cmd_lab_query_metric)
    lqd = lq_sub.add_parser("drift", help="Metric trend + stats across sessions")
    _add_design_arg(lqd)
    lqd.add_argument("--metric", required=True)
    lqd.add_argument("--component-ref", dest="component_ref", default=None)
    lqd.add_argument("--since", default=None)
    lqd.add_argument("--until", default=None)
    lqd.set_defaults(func=cmd_lab_query_drift)

    lqw = lq_sub.add_parser("waveform")
    _add_design_arg(lqw)
    lqw.add_argument("--session", required=True)
    lqw.add_argument("--channel", default="scope_ch1")
    lqw.add_argument("--t-start", dest="t_start", type=float, default=None)
    lqw.add_argument("--t-end", dest="t_end", type=float, default=None)
    lqw.add_argument("--out", default=None, help="Export to CSV")
    lqw.set_defaults(func=cmd_lab_query_waveform)

    p_model = sub.add_parser("model", help="Local model providers (LTspice/… → ngspice subckt)")
    model_sub = p_model.add_subparsers(dest="model_cmd", required=True)
    mb = model_sub.add_parser("build", help="Build an ngspice model from a provider source and register it")
    _add_design_arg(mb)
    mb.add_argument("--kicad-key", dest="kicad_key", required=True)
    mb.add_argument("--reference", default=None)
    mb.add_argument("--provider", default="ltspice", choices=["ltspice", "datasheet", "bench", "vendor"])
    mb.add_argument("--from", dest="source_file", default=None, help="LTspice-exported .net/.cir (ltspice provider)")
    mb.add_argument("--lib", default=None, help="Vendor/bench .lib path (vendor/bench provider)")
    mb.add_argument("--sim-name", dest="sim_name", default=None, help="Subckt/model name to emit")
    mb.add_argument("--mpn", default=None, help="MPN for datasheet provider (default: manifest value)")
    mb.add_argument(
        "--from-meas",
        dest="from_meas",
        default=None,
        help="Parse LTspice/ngspice .MEAS log into provenance.metrics (merged with --metrics)",
    )
    mb.add_argument("--pins", default=None, help="External pins, space-separated (ltspice flat netlist wrap)")
    mb.add_argument(
        "--valid-range",
        dest="valid_range",
        default=None,
        help='JSON of valid operating ranges, e.g. \'{"vsupply_v":[4.5,5.5],"temp_c":[-10,85]}\'',
    )
    mb.add_argument(
        "--metrics",
        default=None,
        help='JSON of achieved metrics, e.g. \'{"vout_ripple_mv":12,"eff_pct":92}\'',
    )
    mb.add_argument("--notes", default=None)
    mb.set_defaults(func=cmd_model_build)
    mstat = model_sub.add_parser("status", help="Per-component model source / valid_range / spec / metrics")
    _add_design_arg(mstat)
    mstat.add_argument("--manifest", default=None, help="Override manifest path")
    mstat.set_defaults(func=cmd_model_status)

    p_spec = sub.add_parser("spec", help="Top-down performance budgets (spec) per component")
    spec_sub = p_spec.add_subparsers(dest="spec_cmd", required=True)
    ss = spec_sub.add_parser("set", help="Set a component's required performance budget")
    _add_design_arg(ss)
    ss.add_argument("--kicad-key", dest="kicad_key", required=True)
    ss.add_argument("--reference", default=None)
    ss.add_argument(
        "--spec",
        required=True,
        help='JSON budget {metric:[min,max]}, e.g. \'{"vout_ripple_mv":[0,15],"eff_pct":[90,100]}\'',
    )
    ss.set_defaults(func=cmd_spec_set)

    p_agent = sub.add_parser("agent", help="Agent tool registry")
    agent_sub = p_agent.add_subparsers(dest="agent_cmd", required=True)
    at = agent_sub.add_parser("tools")
    at.set_defaults(func=cmd_agent_tools)
    ac = agent_sub.add_parser("call")
    ac.add_argument("tool")
    ac.add_argument("--params", default="{}", help="JSON object")
    ac.set_defaults(func=cmd_agent_call)

    p_mcp = sub.add_parser("mcp", help="Model Context Protocol server (stdio)")
    mcp_sub = p_mcp.add_subparsers(dest="mcp_cmd", required=True)
    msrv = mcp_sub.add_parser("serve", help="Run MCP server on stdio exposing agent tools")
    msrv.set_defaults(func=cmd_mcp_serve)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"benchgate: configuration error: {exc}", file=sys.stderr)
        return 2
    except CapabilityError as exc:
        print(f"benchgate: capability error: {exc}", file=sys.stderr)
        return 2
    except InstrumentError as exc:
        print(f"benchgate: instrument error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nbenchgate: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
