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


def _paths(args: argparse.Namespace):
    return benchgate_paths(args.design, manifest=args.manifest)


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
        },
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("success") else 1


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
    result = dispatch(
        "watch_once",
        {"design_dir": args.design, "run_sim": not args.no_sim},
    )
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
    result = dispatch("gate_report", params)
    print(json.dumps(result, indent=2, ensure_ascii=False))
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
        "source_file": args.source_file,
        "sim_name": args.sim_name,
    }
    if args.reference:
        params["reference"] = args.reference
    if args.pins:
        params["pins"] = args.pins
    if args.notes:
        params["notes"] = args.notes
    if args.valid_range:
        params["valid_range"] = json.loads(args.valid_range)
    result = dispatch("model_build", params)
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


def main(argv: list[str] | None = None) -> int:
    from benchgate.instruments.errors import CapabilityError, ConfigError, InstrumentError

    parser = argparse.ArgumentParser(
        prog="benchgate",
        description="Bench capture → SPICE models → regression sim → quality gate",
    )
    parser.add_argument("--version", action="version", version=f"benchgate {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_map = sub.add_parser("mapping", help="KiCad → manifest mapping")
    map_sub = p_map.add_subparsers(dest="map_cmd", required=True)
    ms = map_sub.add_parser("sync", help="Sync schematic → manifest.yaml")
    ms.add_argument("--design", default="design", help="KiCad project directory")
    ms.add_argument("--manifest", default=None, help="Override manifest path (default: <design>/models/manifest.yaml)")
    ms.set_defaults(func=cmd_mapping_sync)
    mst = map_sub.add_parser("status")
    mst.add_argument("--design", default="design", help="KiCad project directory")
    mst.add_argument("--manifest", default=None, help="Override manifest path (default: <design>/models/manifest.yaml)")
    mst.set_defaults(func=cmd_mapping_status)

    p_sim = sub.add_parser("sim", help="ngspice simulation")
    sim_sub = p_sim.add_subparsers(dest="sim_cmd", required=True)
    sr = sim_sub.add_parser("run")
    sr.add_argument("--design", default="design", help="KiCad project directory")
    sr.add_argument("--manifest", default=None, help="Override manifest path (default: <design>/models/manifest.yaml)")
    sr.add_argument("--out", default=None, help="Output directory (default: <design>/reports/sim)")
    sr.add_argument("--profile", default="default")
    sr.set_defaults(func=cmd_sim_run)
    sc = sim_sub.add_parser("cosim", help="Closed-loop cosim with firmware control.c")
    sc.add_argument("--design", default="design", help="KiCad project directory")
    sc.add_argument("--manifest", default=None, help="Override manifest path (default: <design>/models/manifest.yaml)")
    sc.add_argument("--out", default=None, help="Output directory (default: <design>/reports/sim_cosim)")
    sc.add_argument("--profile", default="hbridge_pwm_closed")
    sc.set_defaults(func=cmd_sim_cosim)

    p_watch = sub.add_parser("watch", help="Design change triggers")
    watch_sub = p_watch.add_subparsers(dest="watch_cmd", required=True)
    wo = watch_sub.add_parser("once")
    wo.add_argument("--design", default="design")
    wo.add_argument("--no-sim", action="store_true")
    wo.set_defaults(func=cmd_watch_once)

    p_gate = sub.add_parser("gate", help="Bench vs sim quality")
    gate_sub = p_gate.add_subparsers(dest="gate_cmd", required=True)
    gr = gate_sub.add_parser("report")
    gr.add_argument("--design", default="design", help="KiCad project directory")
    gr.add_argument("--manifest", default=None, help="Override manifest path (default: <design>/models/manifest.yaml)")
    gr.add_argument("--out", default=None, help="Output JSON path (default: <design>/reports/gate_report.json)")
    gr.add_argument("--sim-raw", default=None)
    gr.add_argument(
        "--operating-point",
        dest="operating_point",
        default=None,
        help='JSON of actual operating point for valid_range checks, e.g. \'{"vsupply_v":5.0,"temp_c":25}\'',
    )
    gr.set_defaults(func=cmd_gate_report)

    p_lab = sub.add_parser("lab", help="Instrument control: capture, read, query")
    lab_sub = p_lab.add_subparsers(dest="lab_cmd", required=True)

    ll = lab_sub.add_parser("list", help="List instruments and effective role bindings")
    ll.add_argument("--design", default="design")
    ll.set_defaults(func=cmd_lab_list)

    lr = lab_sub.add_parser("read", help="Read scalar value(s) (default role: dmm)")
    lr.add_argument("--design", default="design")
    lr.add_argument("--role", default="dmm")
    lr.add_argument("--instrument", default=None, help="Explicit instrument name (overrides role)")
    lr.add_argument("--count", type=int, default=1)
    lr.add_argument("--continuous", action="store_true", help="Stream readings until Ctrl-C")
    lr.add_argument("--interval", type=float, default=0.25, help="Seconds between continuous reads")
    lr.set_defaults(func=cmd_lab_read)

    lc = lab_sub.add_parser("capture", help="Capture a waveform (default role: scope)")
    lc.add_argument("--design", default="design")
    lc.add_argument("--role", default="scope")
    lc.add_argument("--instrument", default=None)
    lc.add_argument("--channel", type=int, default=None)
    lc.add_argument("--component-ref", dest="component_ref", default=None)
    lc.add_argument("--out", default=None, help="Export captured waveform to CSV")
    lc.set_defaults(func=cmd_lab_capture)

    lch = lab_sub.add_parser("characterize", help="Capture + fit + write subckt/manifest")
    lch.add_argument("--design", default="design")
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
    lqs.add_argument("--design", default="design")
    lqs.add_argument("--component-ref", dest="component_ref", default=None)
    lqs.add_argument("--since", default=None)
    lqs.add_argument("--until", default=None)
    lqs.set_defaults(func=cmd_lab_query_sessions)
    lqm = lq_sub.add_parser("metric")
    lqm.add_argument("--design", default="design")
    lqm.add_argument("--metric", required=True)
    lqm.add_argument("--component-ref", dest="component_ref", default=None)
    lqm.add_argument("--since", default=None)
    lqm.add_argument("--until", default=None)
    lqm.set_defaults(func=cmd_lab_query_metric)
    lqd = lq_sub.add_parser("drift", help="Metric trend + stats across sessions")
    lqd.add_argument("--design", default="design")
    lqd.add_argument("--metric", required=True)
    lqd.add_argument("--component-ref", dest="component_ref", default=None)
    lqd.add_argument("--since", default=None)
    lqd.add_argument("--until", default=None)
    lqd.set_defaults(func=cmd_lab_query_drift)

    lqw = lq_sub.add_parser("waveform")
    lqw.add_argument("--design", default="design")
    lqw.add_argument("--session", required=True)
    lqw.add_argument("--channel", default="scope_ch1")
    lqw.add_argument("--t-start", dest="t_start", type=float, default=None)
    lqw.add_argument("--t-end", dest="t_end", type=float, default=None)
    lqw.add_argument("--out", default=None, help="Export to CSV")
    lqw.set_defaults(func=cmd_lab_query_waveform)

    p_model = sub.add_parser("model", help="Local model providers (LTspice/… → ngspice subckt)")
    model_sub = p_model.add_subparsers(dest="model_cmd", required=True)
    mb = model_sub.add_parser("build", help="Build an ngspice subckt from a .net/.cir and register it")
    mb.add_argument("--design", default="design", help="KiCad project directory")
    mb.add_argument("--kicad-key", dest="kicad_key", required=True)
    mb.add_argument("--reference", default=None)
    mb.add_argument("--provider", default="ltspice", choices=["ltspice"])
    mb.add_argument("--from", dest="source_file", required=True, help="LTspice-exported .net/.cir netlist")
    mb.add_argument("--sim-name", dest="sim_name", required=True, help="Subckt name to emit")
    mb.add_argument("--pins", default=None, help="External pins, space-separated (required to wrap a flat netlist)")
    mb.add_argument(
        "--valid-range",
        dest="valid_range",
        default=None,
        help='JSON of valid operating ranges, e.g. \'{"vsupply_v":[4.5,5.5],"temp_c":[-10,85]}\'',
    )
    mb.add_argument("--notes", default=None)
    mb.set_defaults(func=cmd_model_build)
    mstat = model_sub.add_parser("status", help="Per-component model source / valid_range")
    mstat.add_argument("--design", default="design", help="KiCad project directory")
    mstat.add_argument("--manifest", default=None, help="Override manifest path")
    mstat.set_defaults(func=cmd_model_status)

    p_agent = sub.add_parser("agent", help="Agent tool registry")
    agent_sub = p_agent.add_subparsers(dest="agent_cmd", required=True)
    at = agent_sub.add_parser("tools")
    at.set_defaults(func=cmd_agent_tools)
    ac = agent_sub.add_parser("call")
    ac.add_argument("tool")
    ac.add_argument("--params", default="{}", help="JSON object")
    ac.set_defaults(func=cmd_agent_call)

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
