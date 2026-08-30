"""Dispatch agent tool calls to benchgate modules."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchgate.agent.tools import TOOLS
from benchgate.gate.report import write_gate_report
from benchgate.io.manifest import load_manifest, save_manifest
from benchgate.rules.loader import default_rule_pack_paths
from benchgate.kicad.project import KiCadProject
from benchgate.kicad.spice_fields import apply_model_to_reference
from benchgate.lab.capture import LabSession, capture_and_fit
from benchgate.lab.fit import measured_to_subckt, write_subckt
from benchgate.lab.store import LabDataStore
from benchgate.mapping.engine import apply_measured_model, mapping_status, sync_project
from benchgate.paths import benchgate_paths, resolve_project_path
from benchgate.schemas import ComponentMapping, MappingManifest, ModelProvenance, ModelSource
from benchgate.watch.trigger import watch_once


def _paths_for_design(design_dir: str | Path, args: dict[str, Any]):
    return benchgate_paths(
        design_dir,
        manifest=args.get("manifest_path"),
    )


def _role_overrides(args: dict[str, Any]) -> dict[str, str | None]:
    from benchgate.instruments.capabilities import ROLE_CAPABILITY

    return {role: args[role] for role in ROLE_CAPABILITY if args.get(role)}


def _open_bench(paths, args: dict[str, Any]):
    from benchgate.instruments import load_bench

    return load_bench(
        paths.instruments,
        project_lab_path=paths.lab_config,
        overrides=_role_overrides(args),
    )


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _resolve_provenance_dict(args: dict[str, Any], key: str, existing: dict | None) -> dict:
    """Merge agent/CLI dict fields; ``None`` or absent → keep existing (or {})."""
    fallback = existing if isinstance(existing, dict) else {}
    if key not in args:
        return fallback
    val = args[key]
    if val is None:
        return fallback
    return val


def dispatch(name: str, args: dict[str, Any]) -> Any:
    if name not in TOOLS:
        raise KeyError(f"Unknown tool: {name}")

    if name == "benchgate_version":
        import benchgate

        from benchgate.agent.tools import TOOLS as _TOOLS

        return {
            "version": benchgate.__version__,
            "install_path": str(Path(benchgate.__file__).resolve().parent),
            "tool_count": len(_TOOLS),
            "tools": sorted(_TOOLS.keys()),
        }

    if name == "mapping_sync":
        p = _paths_for_design(args["design_dir"], args)
        manifest = sync_project(
            p.design,
            p.manifest,
            p.models,
            subckt_dir=p.subckt,
            global_models_dir=p.global_models,
        )
        return mapping_status(manifest)

    if name == "mapping_status":
        p = _paths_for_design(args.get("design_dir", "design"), args)
        mp = Path(args["manifest_path"]) if args.get("manifest_path") else p.manifest
        if not mp.is_absolute():
            mp = resolve_project_path(p.design, mp, p.manifest)
        return mapping_status(load_manifest(mp, global_models_dir=p.global_models))

    if name == "model_build":
        from benchgate.mapping.engine import build_model
        from benchgate.providers.factory import create_model_provider
        from benchgate.providers.meas_log import merge_metrics, parse_meas_file

        p = _paths_for_design(args["design_dir"], args)
        provider_name = args.get("provider", "ltspice")
        source_file = (
            resolve_project_path(p.design, args["source_file"], p.design)
            if args.get("source_file")
            else None
        )
        pins = args["pins"].split() if args.get("pins") else None
        manifest = (
            load_manifest(p.manifest, global_models_dir=p.global_models)
            if p.manifest.exists()
            else MappingManifest()
        )
        entry = manifest.find(args["kicad_key"]) or ComponentMapping(kicad_key=args["kicad_key"])
        existing = entry.provenance
        valid_range = _resolve_provenance_dict(args, "valid_range", existing.valid_range if existing else None)
        log_metrics: dict[str, float] = {}
        if args.get("from_meas"):
            meas_path = resolve_project_path(p.design, args["from_meas"], p.design)
            log_metrics = parse_meas_file(meas_path)
        base_metrics = _resolve_provenance_dict(args, "metrics", existing.metrics if existing else None)
        metrics = merge_metrics(log_metrics, base_metrics)
        sim_name = args.get("sim_name") or args.get("mpn") or entry.metadata.get("value")
        lib_path = None
        if args.get("lib_path"):
            lib_path = resolve_project_path(p.design, args["lib_path"], p.design)
        elif provider_name in ("bench", "vendor") and args.get("source_file"):
            lib_path = resolve_project_path(p.design, args["source_file"], p.design)
        provider = create_model_provider(
            provider_name,
            entry=entry,
            source_file=source_file,
            sim_name=sim_name,
            pins=pins,
            mpn=args.get("mpn"),
            lib_path=lib_path,
            valid_range=valid_range,
            metrics=metrics,
            notes=args.get("notes"),
            sim_pins=args.get("sim_pins"),
        )
        if args.get("reference"):
            entry.reference = args["reference"]
        entry = build_model(manifest, entry, provider, workdir=p.subckt)
        save_manifest(manifest, p.manifest, global_models_dir=p.global_models)
        prov = entry.provenance
        return {
            "kicad_key": entry.kicad_key,
            "provider": provider_name,
            "source": prov.source.value if prov else None,
            "subckt": str(entry.sim_library),
            "sim_name": entry.sim_name,
            "sim_pins": entry.sim_pins,
            "metrics": prov.metrics if prov else {},
            "warnings": prov.notes if prov else None,
        }

    if name == "spec_set":
        p = _paths_for_design(args["design_dir"], args)
        manifest = (
            load_manifest(p.manifest, global_models_dir=p.global_models)
            if p.manifest.exists()
            else MappingManifest()
        )
        entry = manifest.find(args["kicad_key"]) or ComponentMapping(kicad_key=args["kicad_key"])
        if args.get("reference"):
            entry.reference = args["reference"]
        entry.spec = args["spec"]
        manifest.upsert(entry)
        save_manifest(manifest, p.manifest, global_models_dir=p.global_models)
        return {"kicad_key": entry.kicad_key, "spec": entry.spec}

    if name == "model_status":
        p = _paths_for_design(args.get("design_dir", "design"), args)
        mp = Path(args["manifest_path"]) if args.get("manifest_path") else p.manifest
        if not mp.is_absolute():
            mp = resolve_project_path(p.design, mp, p.manifest)
        manifest = load_manifest(mp, global_models_dir=p.global_models)
        return {
            "entries": [
                {
                    "kicad_key": e.kicad_key,
                    "reference": e.reference,
                    "status": e.status,
                    "spice_kind": e.spice_kind.value,
                    "source": e.provenance.source.value if e.provenance else None,
                    "sim_name": e.sim_name,
                    "valid_range": e.provenance.valid_range if e.provenance else {},
                    "metrics": e.provenance.metrics if e.provenance else {},
                    "spec": e.spec or {},
                }
                for e in manifest.entries
            ]
        }

    if name == "pipeline_sync":
        from benchgate.pipeline.local_blocks import sync_local_blocks

        p = _paths_for_design(args["design_dir"], args)
        return sync_local_blocks(
            models_dir=p.models,
            manifest_path=p.manifest,
            subckt_dir=p.subckt,
            global_models_dir=p.global_models,
            blocks_yaml=p.blocks_yaml,
            tmp_dir=p.tmp_root / "pipeline",
        )

    if name == "lab_capture":
        p = _paths_for_design(args["design_dir"], args)
        bench = _open_bench(p, args)
        store = LabDataStore(p.captured)
        ref = args["component_ref"]
        mpn = args["mpn"]
        kicad_key = args["kicad_key"]
        tags = args.get("tags") or ["characterize"]
        with LabSession(bench) as session:
            measured, meta = capture_and_fit(
                session,
                store,
                component_ref=ref,
                mpn=mpn,
                kicad_key=kicad_key,
                design=str(p.design),
                tags=tags,
            )
        subckt_name = f"MEAS_{ref}".upper()
        subckt_path = p.subckt / f"{subckt_name}.lib"
        write_subckt(subckt_path, measured_to_subckt(measured.params, subckt_name))
        manifest = (
            load_manifest(p.manifest, global_models_dir=p.global_models)
            if p.manifest.exists()
            else MappingManifest()
        )
        entry = apply_measured_model(
            manifest,
            kicad_key,
            lib_path=subckt_path.relative_to(p.global_models),
            sim_name=subckt_name,
            measured_params=measured.params,
        )
        entry.reference = ref
        entry.provenance = ModelProvenance(
            source=ModelSource.BENCH,
            generated_at=measured.captured_at,
            tool="benchgate lab fit",
            metrics=dict(measured.params),
            measured=measured,
        )
        save_manifest(manifest, p.manifest, global_models_dir=p.global_models)
        out = {
            "measured": measured.params,
            "subckt": str(subckt_path),
            "kicad_key": kicad_key,
            "session_id": meta.session_id,
            "tags": tags,
        }
        if args.get("rerun_sim", True):
            from benchgate.sim.pipeline import run_project_sim

            sim_dir = p.reports / "sim"
            sim_report, _ = run_project_sim(
                p.design,
                p.manifest,
                sim_dir,
                sim_profile_path=p.sim_profile,
                profile=str(args.get("profile", "default")),
            )
            gate_path = p.reports / "gate_report.json"
            gate = write_gate_report(
                p.manifest,
                gate_path,
                captured_dir=p.captured,
                sim_dir=sim_dir,
                sim_report_path=sim_dir / "sim_report.json",
                sim_profile_path=p.sim_profile,
                profile=str(args.get("profile", "default")),
                design_dir=p.design,
            )
            out["sim"] = sim_report.to_dict()
            out["gate"] = gate.to_dict()
        return out

    if name == "lab_list":
        p = _paths_for_design(args["design_dir"], args)
        bench = _open_bench(p, args)
        return {
            "instruments": {
                n: {
                    "driver": c.driver,
                    "address": c.address,
                    "transport": c.transport,
                    "capabilities": sorted(bench.capabilities(n)),
                }
                for n, c in bench.instruments.items()
            },
            "roles": bench.roles,
            "defaults": bench.defaults,
        }

    if name == "lab_read":
        p = _paths_for_design(args["design_dir"], args)
        bench = _open_bench(p, args)
        inst = bench.select(role=args.get("role", "dmm"), instrument=args.get("instrument"))
        count = int(args.get("count", 1))
        readings = []
        try:
            for _ in range(max(1, count)):
                r = inst.read()  # type: ignore[attr-defined]
                readings.append(
                    {
                        "value": r.value,
                        "unit": r.unit,
                        "quantity": r.quantity.value,
                        "normalized_value": r.normalized_value,
                        "normalized_unit": r.normalized_unit,
                        "flags": {k: v for k, v in r.flags.items() if v},
                        "timestamp": r.timestamp.isoformat(),
                    }
                )
        finally:
            inst.disconnect()
        return {"instrument": inst.name, "readings": readings}

    if name == "lab_capture_waveform":
        p = _paths_for_design(args["design_dir"], args)
        bench = _open_bench(p, args)
        store = LabDataStore(p.captured)
        with LabSession(bench) as session:
            channel = int(args.get("channel", session.config.scope_channel))
            wf = session.capture_step_response(channel)
            meta = store.write_session(
                component_ref=args.get("component_ref"),
                design=str(p.design),
                waveforms={"scope_ch1": wf},
                roles=session.roles_map(),
                instruments=session.provenance(),
                tags=args.get("tags"),
            )
        return {
            "session_id": meta.session_id,
            "channel": wf.channel,
            "samples": len(wf),
            "sample_rate_hz": wf.sample_rate_hz,
            "path": str((meta.path or p.captured) / "scope_ch1.npz"),
        }

    if name == "lab_sa_sweep":
        from benchgate.instruments.types import ScanConfig

        p = _paths_for_design(args["design_dir"], args)
        bench = _open_bench(p, args)
        inst = bench.select(role="sa", instrument=args.get("instrument"))
        store = LabDataStore(p.captured)
        try:
            cfg = ScanConfig(
                center_mhz=args.get("center_mhz"),
                span_mhz=args.get("span_mhz"),
                start_mhz=args.get("start_mhz"),
                stop_mhz=args.get("stop_mhz"),
                reference_dbm=args.get("reference_dbm"),
                attenuation=args.get("attenuation"),
            )
            if any(
                v is not None
                for v in (
                    cfg.center_mhz,
                    cfg.span_mhz,
                    cfg.start_mhz,
                    cfg.stop_mhz,
                    cfg.reference_dbm,
                    cfg.attenuation,
                )
            ):
                inst.configure_scan(cfg)  # type: ignore[attr-defined]
            spec = inst.capture_spectrum()  # type: ignore[attr-defined]
            meta = store.write_session(
                component_ref=args.get("component_ref"),
                design=str(p.design),
                spectra={"sa_trace": spec},
                roles={"sa": bench.instrument_for_role("sa")},
                instruments={"sa": inst.info.as_provenance("sa").get("sa_idn", inst.identify())},
                tags=args.get("tags"),
            )
        finally:
            inst.disconnect()
        return {
            "session_id": meta.session_id,
            "trace": spec.trace,
            "points": len(spec),
            "freq_start_hz": float(spec.freq_hz[0]) if len(spec) else None,
            "freq_stop_hz": float(spec.freq_hz[-1]) if len(spec) else None,
            "path": str((meta.path or p.captured) / "sa_trace.npz"),
            "metadata": spec.metadata,
        }

    if name == "lab_sa_peak":
        from benchgate.instruments.types import PeakMode

        p = _paths_for_design(args["design_dir"], args)
        bench = _open_bench(p, args)
        inst = bench.select(role="sa", instrument=args.get("instrument"))
        try:
            mode = PeakMode(args.get("mode", "AVR"))
            r = inst.measure_peak(mode)  # type: ignore[attr-defined]
        finally:
            inst.disconnect()
        return {
            "instrument": inst.name,
            "peak": {
                "value": r.value,
                "unit": r.unit,
                "mode": mode.value,
                "timestamp": r.timestamp.isoformat(),
            },
        }

    if name == "lab_sa_floor":
        p = _paths_for_design(args["design_dir"], args)
        bench = _open_bench(p, args)
        inst = bench.select(role="sa", instrument=args.get("instrument"))
        try:
            r = inst.measure_floor()  # type: ignore[attr-defined]
        finally:
            inst.disconnect()
        return {
            "instrument": inst.name,
            "floor": {
                "value": r.value,
                "unit": r.unit,
                "timestamp": r.timestamp.isoformat(),
            },
        }

    if name == "lab_sa_gen":
        p = _paths_for_design(args["design_dir"], args)
        bench = _open_bench(p, args)
        inst = bench.select(role="rfgen", instrument=args.get("instrument"))
        try:
            if "enabled" in args:
                inst.set_generator_enabled(bool(args["enabled"]))  # type: ignore[attr-defined]
            if args.get("frequency_mhz") is not None:
                inst.set_generator_frequency_mhz(float(args["frequency_mhz"]))  # type: ignore[attr-defined]
            if args.get("power_dbm") is not None:
                inst.set_generator_power_dbm(int(args["power_dbm"]))  # type: ignore[attr-defined]
            if args.get("attenuator") is not None:
                inst.set_generator_attenuator(int(args["attenuator"]))  # type: ignore[attr-defined]
            status = {
                "enabled": inst.query_generator_enabled(),  # type: ignore[attr-defined]
                "frequency_mhz": inst.query_generator_frequency_mhz(),  # type: ignore[attr-defined]
            }
        finally:
            inst.disconnect()
        return {"instrument": inst.name, "generator": status}

    if name == "lab_sa_cal":
        from benchgate.instruments.types import CalStandard, SparamKind

        p = _paths_for_design(args["design_dir"], args)
        bench = _open_bench(p, args)
        inst = bench.select(role="vna", instrument=args.get("instrument"))
        try:
            param = SparamKind(args["param"])
            standard = CalStandard(args.get("standard", "OPEN"))
            enabled = bool(args.get("enabled", True))
            inst.calibrate_sparam(param, standard, enabled=enabled)  # type: ignore[attr-defined]
        finally:
            inst.disconnect()
        return {
            "instrument": inst.name,
            "calibration": {
                "param": param.value,
                "standard": standard.value,
                "enabled": enabled,
            },
        }

    if name == "lab_sa_sparam":
        from benchgate.instruments.types import SparamKind

        p = _paths_for_design(args["design_dir"], args)
        bench = _open_bench(p, args)
        inst = bench.select(role="vna", instrument=args.get("instrument"))
        store = LabDataStore(p.captured)
        try:
            param = SparamKind(args.get("param", "S21"))
            spec = inst.capture_sparam_trace(param)  # type: ignore[attr-defined]
            meta = store.write_session(
                component_ref=args.get("component_ref"),
                design=str(p.design),
                spectra={f"sa_{param.value.lower()}": spec},
                roles={"vna": bench.instrument_for_role("vna")},
                instruments={"vna": inst.info.as_provenance("vna").get("vna_idn", inst.identify())},
                tags=args.get("tags"),
            )
        finally:
            inst.disconnect()
        return {
            "session_id": meta.session_id,
            "param": param.value,
            "points": len(spec),
            "path": str((meta.path or p.captured) / f"sa_{param.value.lower()}.npz"),
            "metadata": spec.metadata,
        }

    if name == "lab_query_sessions":
        p = _paths_for_design(args["design_dir"], args)
        store = LabDataStore(p.captured)
        metas = store.list_sessions(
            component_ref=args.get("component_ref"),
            since=_parse_iso(args.get("since")),
            until=_parse_iso(args.get("until")),
            tags=args.get("tags"),
        )
        return {"sessions": [m.to_dict() for m in metas]}

    if name == "lab_metric_series":
        p = _paths_for_design(args["design_dir"], args)
        store = LabDataStore(p.captured)
        rows = store.metric_series(
            args["metric"],
            component_ref=args.get("component_ref"),
            since=_parse_iso(args.get("since")),
            until=_parse_iso(args.get("until")),
        )
        return {
            "metric": args["metric"],
            "series": [
                {
                    "captured_at": r["captured_at"].isoformat(),
                    "session_id": r["session_id"],
                    "component_ref": r["component_ref"],
                    "value": r["value"],
                }
                for r in rows
            ],
        }

    if name == "lab_metric_drift":
        from benchgate.lab.analyze import drift, metric_stats

        p = _paths_for_design(args["design_dir"], args)
        store = LabDataStore(p.captured)
        rows = store.metric_series(
            args["metric"],
            component_ref=args.get("component_ref"),
            since=_parse_iso(args.get("since")),
            until=_parse_iso(args.get("until")),
        )
        return {
            "metric": args["metric"],
            "stats": metric_stats(rows).to_dict(),
            "drift": drift(rows).to_dict(),
        }

    if name == "lab_apply_model":
        p = _paths_for_design(args["design_dir"], args)
        project = KiCadProject.load(p.design)
        lib = Path(args["sim_library"])
        if not lib.is_absolute():
            lib = (p.global_models / lib).resolve()
        apply_model_to_reference(
            project.schematic,
            args["reference"],
            sim_library=lib,
            sim_name=args["sim_name"],
            sim_pins=args.get("sim_pins", ""),
        )
        manifest = (
            load_manifest(p.manifest, global_models_dir=p.global_models)
            if p.manifest.exists()
            else MappingManifest()
        )
        apply_measured_model(
            manifest,
            args["kicad_key"],
            lib_path=lib.relative_to(p.global_models) if lib.is_relative_to(p.global_models) else lib,
            sim_name=args["sim_name"],
            sim_pins=args.get("sim_pins", ""),
        )
        entry = manifest.find(args["kicad_key"])
        if entry:
            entry.reference = args["reference"]
        save_manifest(manifest, p.manifest, global_models_dir=p.global_models)
        return {"ok": True, "reference": args["reference"]}

    if name == "sim_run":
        from benchgate.sim.pipeline import run_project_sim
        p = _paths_for_design(args["design_dir"], args)
        mp = Path(args["manifest_path"]) if args.get("manifest_path") else p.manifest
        if not mp.is_absolute():
            mp = resolve_project_path(p.design, mp, p.manifest)
        out_dir = Path(args["output_dir"]) if args.get("output_dir") else p.reports / "sim"
        if args.get("output_dir") and not out_dir.is_absolute():
            out_dir = resolve_project_path(p.design, out_dir, p.reports / "sim")
        elif not args.get("output_dir"):
            out_dir = p.reports / "sim"
        profile = args.get("profile", "default")
        report, result = run_project_sim(
            p.design,
            mp,
            out_dir,
            sim_profile_path=p.sim_profile,
            profile=profile,
            fail_on_preflight_error=bool(args.get("fail_on_preflight")),
        )
        return {
            "success": report.success,
            "report": report.to_dict(),
            "stderr": result.stderr,
        }

    if name == "sim_stress_sweep":
        from benchgate.sim.stress_sweep import run_stress_sweep

        p = _paths_for_design(args["design_dir"], args)
        mp = Path(args["manifest_path"]) if args.get("manifest_path") else p.manifest
        if not mp.is_absolute():
            mp = resolve_project_path(p.design, mp, p.manifest)
        out_dir = Path(args["output_dir"]) if args.get("output_dir") else p.reports / "stress_sweep"
        if args.get("output_dir") and not out_dir.is_absolute():
            out_dir = resolve_project_path(p.design, out_dir, p.reports / "stress_sweep")
        elif not args.get("output_dir"):
            out_dir = p.reports / "stress_sweep"
        report = run_stress_sweep(
            p.design,
            mp,
            out_dir,
            sim_profile_path=p.sim_profile,
            profile=args.get("profile", "default"),
        )
        return {"success": bool(report.to_dict().get("worst", {}).get("passed")), "report": report.to_dict()}

    if name == "sim_sweep":
        from benchgate.sim.sweep import run_sweep

        p = _paths_for_design(args["design_dir"], args)
        mp = Path(args["manifest_path"]) if args.get("manifest_path") else p.manifest
        if not mp.is_absolute():
            mp = resolve_project_path(p.design, mp, p.manifest)
        out_dir = Path(args["output_dir"]) if args.get("output_dir") else p.reports / "sim_sweep"
        if args.get("output_dir") and not out_dir.is_absolute():
            out_dir = resolve_project_path(p.design, out_dir, p.reports / "sim_sweep")
        elif not args.get("output_dir"):
            out_dir = p.reports / "sim_sweep"
        def norm(d: dict | None) -> dict[str, list[str]]:
            return {k: [str(x) for x in v] for k, v in (d or {}).items()}

        report = run_sweep(
            p.design,
            mp,
            out_dir,
            sim_profile_path=p.sim_profile,
            profile=args.get("profile", "default"),
            metric_spec=args.get("metric"),
            metrics=[str(m) for m in args.get("metrics") or []] or None,
            params=norm(args.get("params")),
            sets=norm(args.get("sets")),
            pass_gte=args.get("pass_gte"),
            pass_lte=args.get("pass_lte"),
        )
        return {"success": True, "report": report.to_dict()}

    if name == "sim_block_sweep":
        from benchgate.sim.sweep import run_block_sweep

        p = _paths_for_design(args["design_dir"], args)
        netlist = Path(args["netlist"])
        if not netlist.is_absolute():
            netlist = (p.design / netlist).resolve()
        out_dir = Path(args["output_dir"]) if args.get("output_dir") else p.reports / "block_sweep"
        if not out_dir.is_absolute():
            out_dir = resolve_project_path(p.design, out_dir, p.reports / "block_sweep")

        def norm_block(d: dict | None) -> dict[str, list[str]]:
            return {k: [str(x) for x in v] for k, v in (d or {}).items()}

        metrics = [str(m) for m in args.get("metrics") or []]
        if not metrics and args.get("metric"):
            metrics = [str(args["metric"])]
        report = run_block_sweep(
            netlist,
            out_dir,
            metrics=metrics,
            params=norm_block(args.get("params")),
            sets=norm_block(args.get("sets")),
            pass_gte=args.get("pass_gte"),
            pass_lte=args.get("pass_lte"),
        )
        payload = report.to_dict()
        failed = [pt for pt in payload["points"] if pt.get("passed") is False]
        return {"success": not failed, "report": payload}

    if name == "sim_cosim":
        from benchgate.cosim.runner import run_cosim

        p = _paths_for_design(args["design_dir"], args)
        mp = Path(args["manifest_path"]) if args.get("manifest_path") else p.manifest
        if not mp.is_absolute():
            mp = resolve_project_path(p.design, mp, p.manifest)
        out_dir = Path(args["output_dir"]) if args.get("output_dir") else p.reports / "sim_cosim"
        if args.get("output_dir") and not out_dir.is_absolute():
            out_dir = resolve_project_path(p.design, out_dir, p.reports / "sim_cosim")
        elif not args.get("output_dir"):
            out_dir = p.reports / "sim_cosim"
        profile = args.get("profile", "hbridge_pwm_closed")
        report, result = run_cosim(
            p.design,
            mp,
            out_dir,
            sim_profile_path=p.sim_profile,
            profile=profile,
            build_dir=p.cosim_build,
        )
        return {
            "success": report.success,
            "report": report.to_dict(),
            "stderr": result.stderr,
        }

    if name == "gate_report":
        p = _paths_for_design(args["design_dir"], args)
        mp = Path(args["manifest_path"]) if args.get("manifest_path") else p.manifest
        if not mp.is_absolute():
            mp = resolve_project_path(p.design, mp, p.manifest)
        out = Path(args.get("output_path", p.reports / "gate_report.json"))
        if not out.is_absolute():
            out = resolve_project_path(p.design, out, p.reports / "gate_report.json")
        sim_raw = Path(args["sim_raw_path"]) if args.get("sim_raw_path") else None
        if sim_raw and not sim_raw.is_absolute():
            sim_raw = resolve_project_path(p.design, sim_raw, p.reports / "sim" / "sim_waveform.csv")
        stress_sweep_path: Path | None = None
        if args.get("stress_sweep"):
            from benchgate.sim.stress_sweep import run_stress_sweep

            profile = str(args.get("profile", "default"))
            sweep_dir = p.reports / "stress_sweep"
            sweep_report = run_stress_sweep(
                p.design,
                mp,
                sweep_dir,
                sim_profile_path=p.sim_profile,
                profile=profile,
            )
            stress_sweep_path = Path(sweep_report.report_path) if sweep_report.report_path else None
        rule_pack_paths = None
        if args.get("rules") != "none":
            rule_pack_paths = default_rule_pack_paths(home=p.home, design=p.design)
        mc_path = p.reports / "mc_tolerance" / "mc_tolerance.json"
        sim_dir = p.reports / "sim"
        profile = str(args.get("profile", "default"))
        report = write_gate_report(
            mp,
            out,
            captured_dir=p.captured,
            sim_dir=sim_dir if sim_dir.is_dir() else None,
            sim_raw_path=sim_raw or (sim_dir / "sim_waveform.csv"),
            operating_point=args.get("operating_point"),
            sim_report_path=sim_dir / "sim_report.json",
            stress_sweep_path=stress_sweep_path,
            monte_carlo_path=mc_path if mc_path.is_file() else None,
            rule_pack_paths=rule_pack_paths,
            sim_profile_path=p.sim_profile,
            profile=profile,
            design_dir=p.design,
            blocks_yaml=p.blocks_yaml,
        )
        return report.to_dict()

    if name == "diagnose":
        from benchgate.diagnose import diagnose_project

        p = _paths_for_design(args["design_dir"], args)
        return diagnose_project(
            p.reports,
            captured_dir=p.captured,
            gate_report_path=Path(args["gate_report_path"]) if args.get("gate_report_path") else None,
        )

    if name == "lab_compare_waveforms":
        from benchgate.lab.analyze import compare_waveforms
        from benchgate.bench_compare import load_sim_waveform_csv

        p = _paths_for_design(args["design_dir"], args)
        store = LabDataStore(p.captured)
        bench_wf = store.load_waveform(args["session_id"], args.get("bench_channel", "scope_ch1"))
        sim_path = Path(args["sim_csv"])
        if not sim_path.is_absolute():
            sim_path = p.reports / "sim" / sim_path
        sim_wf = load_sim_waveform_csv(sim_path)
        if sim_wf is None:
            raise FileNotFoundError(f"sim waveform not found: {sim_path}")
        cmp = compare_waveforms(bench_wf, sim_wf)
        from benchgate.gate.report import waveform_status_from_comparison

        result = cmp.to_dict()
        result["waveform_status"] = waveform_status_from_comparison(result)
        return result

    if name == "sim_tolerance":
        from benchgate.sim.tolerance import run_tolerance_study

        p = _paths_for_design(args["design_dir"], args)
        mp = Path(args["manifest_path"]) if args.get("manifest_path") else p.manifest
        if not mp.is_absolute():
            mp = resolve_project_path(p.design, mp, p.manifest)
        out_dir = Path(args.get("output_dir", p.reports / "mc_tolerance"))
        if not out_dir.is_absolute():
            out_dir = resolve_project_path(p.design, out_dir, p.reports / "mc_tolerance")
        report = run_tolerance_study(
            p.design,
            mp,
            out_dir,
            blocks_yaml=p.blocks_yaml,
            sim_profile_path=p.sim_profile,
            profile=str(args.get("profile", "charge_pump")),
            n_samples=int(args.get("n_samples", 200)),
            seed=int(args.get("seed", 42)),
            strategy=str(args.get("strategy", "lhs")),
            warmup_ratio=float(args.get("warmup_ratio", 0.25)),
            surrogate_degree=int(args.get("surrogate_degree", 2)),
            sequential_batch=int(args.get("sequential_batch", 25)),
            sequential_ci_width=float(args.get("sequential_ci_width", 5.0)),
            sequential_min_samples=int(args.get("sequential_min_samples", 50)),
            jobs=int(args.get("jobs", 4)),
            sim_tier=args.get("sim_tier"),
            tran_step=args.get("tran_step"),
            tran_stop=args.get("tran_stop"),
            maxstep=args.get("maxstep"),
        )
        return report.to_dict()

    if name == "sim_diagnose":
        p = _paths_for_design(args["design_dir"], args)
        from benchgate.sim.diagnose import diagnose_sim

        return diagnose_sim(p.reports)

    if name == "watch_once":
        p = _paths_for_design(args["design_dir"], args)
        return watch_once(
            p.design,
            manifest_path=p.manifest,
            models_dir=p.models,
            reports_dir=p.reports,
            state_path=p.state,
            sim_profile_path=p.sim_profile,
            profile=str(args.get("profile", "default")),
            subckt_dir=p.subckt,
            global_models_dir=p.global_models,
            blocks_yaml=p.blocks_yaml,
            tmp_dir=p.tmp_root / "pipeline",
            run_pipeline=bool(args.get("run_pipeline", True)),
            run_sim=bool(args.get("run_sim", True)),
            run_gate=bool(args.get("run_gate", True)),
            run_auto_capture=bool(args.get("run_auto_capture", True)),
            auto_capture_dry_run=bool(args.get("auto_capture_dry_run", False)),
            run_tolerance=bool(args.get("run_tolerance", True)),
            tolerance_samples=int(args.get("tolerance_samples", 200)),
            tolerance_strategy=str(args.get("tolerance_strategy", "auto")),
            tolerance_seed=int(args.get("tolerance_seed", 42)),
            tolerance_jobs=int(args.get("tolerance_jobs", 4)),
        )

    if name == "watch_loop":
        from benchgate.watch.loop import watch_loop

        p = _paths_for_design(args["design_dir"], args)
        max_iter = args.get("max_iterations")
        if max_iter == 0:
            max_iter = None
        return watch_loop(
            p.design,
            manifest_path=p.manifest,
            models_dir=p.models,
            reports_dir=p.reports,
            state_path=p.state,
            sim_profile_path=p.sim_profile,
            profile=str(args.get("profile", "default")),
            subckt_dir=p.subckt,
            global_models_dir=p.global_models,
            blocks_yaml=p.blocks_yaml,
            tmp_dir=p.tmp_root / "pipeline",
            run_pipeline=bool(args.get("run_pipeline", True)),
            run_sim=bool(args.get("run_sim", True)),
            run_gate=bool(args.get("run_gate", True)),
            run_auto_capture=bool(args.get("run_auto_capture", True)),
            auto_capture_dry_run=bool(args.get("auto_capture_dry_run", False)),
            interval_s=float(args.get("interval_s", 2.0)),
            debounce_s=float(args.get("debounce_s", 1.0)),
            max_iterations=max_iter,
            run_tolerance=bool(args.get("run_tolerance", True)),
            tolerance_samples=int(args.get("tolerance_samples", 200)),
            tolerance_strategy=str(args.get("tolerance_strategy", "auto")),
            tolerance_seed=int(args.get("tolerance_seed", 42)),
            tolerance_jobs=int(args.get("tolerance_jobs", 4)),
        )

    if name == "lab_thermal_capture":
        return _lab_thermal_capture(args)

    if name == "lab_thermal_hotspot":
        return _lab_thermal_hotspot(args)

    if name == "lab_thermal_calibrate":
        return _lab_thermal_calibrate(args)

    if name == "lab_thermal_map":
        return _lab_thermal_map(args)
    if name == "lab_thermal_register":
        return _lab_thermal_register(args)

    if name == "lab_thermal_verify_refs":
        return _lab_thermal_verify_refs(args)

    if name == "lab_thermal_baseline":
        return _lab_thermal_baseline(args)

    if name == "lab_thermal_alert":
        return _lab_thermal_alert(args)

    if name == "lab_thermal_watch":
        return _lab_thermal_watch(args)

    raise NotImplementedError(name)


def _geometry_from_args(args: dict[str, Any]):
    from benchgate.lab.field2d import FrameGeometry

    return FrameGeometry(
        origin=str(args.get("origin") or "top_left"),
        x_scale=float(args.get("scale_x", 1.0)),
        y_scale=float(args.get("scale_y", 1.0)),
        unit=str(args.get("coord_unit") or "px"),
        flip_x=bool(args.get("flip_x", False)),
        flip_y=bool(args.get("flip_y", False)),
        rotate_quadrants=int(args.get("rotate_quadrants", 0)),
    )


def _reduce_series(series, reduce: str):
    import numpy as np

    from benchgate.instruments.types import Frame2D

    if reduce == "mean":
        values = series.values.mean(axis=0)
    elif reduce == "last":
        values = series.values[-1]
    elif reduce == "median":
        values = np.median(series.values, axis=0)
    else:
        values = series.values.max(axis=0)
    return Frame2D(
        values=np.asarray(values, dtype=float),
        unit=series.unit,
        quantity=series.quantity,
        timestamp=series.t0_utc,
        mask=series.mask,
        calibration=series.calibration,
        metadata=dict(series.metadata),
    )


def _thermal_cfg(args: dict[str, Any]) -> dict[str, Any]:
    from benchgate.lab.thermal import load_thermal_config

    p = _paths_for_design(args["design_dir"], args)
    return load_thermal_config(p.lab_config)


def _lab_thermal_capture(args: dict[str, Any]) -> dict[str, Any]:
    from benchgate.instruments.types import Frame2D
    from benchgate.lab.thermal import apply_thermal_defaults, summarize_thermal

    args = apply_thermal_defaults(args, _thermal_cfg(args))
    p = _paths_for_design(args["design_dir"], args)
    bench = _open_bench(p, args)
    inst = bench.select(role="thermal", instrument=args.get("instrument"))
    store = LabDataStore(p.captured)
    frames_n = max(1, int(args.get("frames", 1)))
    warmup_s = float(args.get("warmup_s", 0.0))
    try:
        if warmup_s > 0:
            import time

            time.sleep(warmup_s)
        if frames_n == 1:
            frame = inst.capture_frame()  # type: ignore[attr-defined]
        else:
            series = inst.capture_burst(frames_n)  # type: ignore[attr-defined]
            frame = _reduce_series(series, str(args.get("reduce") or "max"))
        emissivity = 1.0
        if hasattr(inst, "get_emissivity"):
            try:
                emissivity = float(inst.get_emissivity())
            except Exception:
                emissivity = float(frame.metadata.get("emissivity", 1.0) or 1.0)
        if args.get("apply_calibration"):
            from benchgate.lab.thermal import apply_calibration, calibration_path, load_calibration

            cal_path = calibration_path(inst.identify())
            if not cal_path.is_file():
                raise FileNotFoundError(
                    f"no calibration at {cal_path}; run lab thermal calibrate first"
                )
            frame = apply_calibration(frame, load_calibration(cal_path))
        geometry = _geometry_from_args(args)
        threshold = args.get("threshold")
        derived = summarize_thermal(
            frame,
            geometry=geometry,
            threshold=None if threshold is None else float(threshold),
            instrument_idn=inst.identify(),
            emissivity=emissivity,
            warmup_s=warmup_s,
            distance_mm=args.get("distance_mm"),
            ambient_bin=str(args.get("ambient_bin") or "unknown"),
        )
        from benchgate.lab.thermal import fixture_id

        fid = fixture_id(
            instrument_idn=inst.identify(),
            emissivity=emissivity,
            warmup_s=warmup_s,
            distance_mm=args.get("distance_mm"),
            ambient_bin=str(args.get("ambient_bin") or "unknown"),
        )
        meta_extra = dict(frame.metadata)
        meta_extra["fixture_id"] = fid
        meta_extra["warmup_s"] = warmup_s
        meta_extra["ambient_bin"] = str(args.get("ambient_bin") or "unknown")
        if args.get("distance_mm") is not None:
            meta_extra["distance_mm"] = float(args["distance_mm"])
        frame = Frame2D(
            values=frame.values,
            unit=frame.unit,
            quantity=frame.quantity,
            timestamp=frame.timestamp,
            mask=frame.mask,
            calibration=frame.calibration,
            metadata=meta_extra,
        )
        tags = list(args.get("tags") or [])
        tags.append(f"fixture:{fid}")
        meta = store.write_session(
            component_ref=args.get("component_ref"),
            design=str(p.design),
            frames={"thermal": frame},
            derived=derived,
            roles={"thermal": bench.instrument_for_role("thermal")},
            instruments={"thermal": inst.identify()},
            tags=tags,
            notes=f"unit={frame.unit} fixture_id={fid}",
        )
    finally:
        inst.disconnect()
    hits = None
    resolution: dict[str, Any] = {}
    if args.get("homography") or args.get("homography_file"):
        resolution = _homography_resolution(args)
        hits = _map_hotspot_to_board(
            args,
            hotspot_xy=(derived["hotspot_x"], derived["hotspot_y"]),
        )
    return {
        "session_id": meta.session_id,
        "unit": frame.unit,
        "shape": [frame.height, frame.width],
        "derived": derived,
        "fixture_id": fid,
        "path": str((meta.path or p.captured) / "thermal.npz"),
        "kicad_hits": hits,
        "px_per_mm": resolution.get("px_per_mm"),
        "coarse_warning": resolution.get("coarse_warning"),
    }


def _lab_thermal_hotspot(args: dict[str, Any]) -> dict[str, Any]:
    from benchgate.lab.thermal import summarize_thermal

    p = _paths_for_design(args["design_dir"], args)
    store = LabDataStore(p.captured)
    session_id = args.get("session_id") or args.get("session")
    if not session_id:
        sessions = [m for m in store.list_sessions() if any(c.kind == "frame2d" for c in m.channels)]
        if not sessions:
            raise FileNotFoundError("no frame2d sessions")
        session_id = sessions[-1].session_id
    frame = store.load_frame2d(session_id, str(args.get("channel") or "thermal"))
    geometry = _geometry_from_args(args)
    threshold = args.get("threshold")
    stored_fid = frame.metadata.get("fixture_id")
    derived = summarize_thermal(
        frame,
        geometry=geometry,
        threshold=None if threshold is None else float(threshold),
        instrument_idn=str(frame.metadata.get("idn") or ""),
        emissivity=float(frame.metadata.get("emissivity") or 1.0),
        known_fixture_id=str(stored_fid) if stored_fid else None,
    )
    return {
        "session_id": session_id,
        "unit": frame.unit,
        "derived": derived,
        "fixture_id": stored_fid,
    }


def _lab_thermal_calibrate(args: dict[str, Any]) -> dict[str, Any]:
    from benchgate.lab.thermal import affine_from_points, calibration_path, save_calibration

    points_raw = args.get("points") or []
    parsed: list[tuple[float, float]] = []
    for item in points_raw:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            parsed.append((float(item[0]), float(item[1])))
        else:
            count_s, deg_s = str(item).split(":", 1)
            parsed.append((float(count_s), float(deg_s)))
    idn = str(args.get("instrument_idn") or "umeko-dec-h")
    cal = affine_from_points(parsed, instrument_idn=idn)
    path = calibration_path(idn)
    if args.get("path"):
        path = Path(args["path"])
    save_calibration(cal, path)
    return {"kind": cal.kind, "slope": cal.slope, "offset": cal.offset, "path": str(path)}


def _homography_items(args: dict[str, Any]) -> list[str]:
    items = list(args.get("homography") or [])
    path = args.get("homography_file")
    if items:
        return items
    if path:
        from benchgate.lab.thermal import load_homography

        data = load_homography(Path(path))
        pairs = data.get("pairs") or []
        if len(pairs) < 4:
            raise ValueError(f"homography file {path} has fewer than 4 pairs")
        return [str(p) for p in pairs]
    raise ValueError("homography requires four px,py:mmx,mmy pairs or homography_file")


def _homography_resolution(args: dict[str, Any]) -> dict[str, Any]:
    from benchgate.lab.board_map import edge_px_per_mm, parse_homography_points
    from benchgate.lab.thermal import load_homography

    src, dst = parse_homography_points(_homography_items(args))
    px_x, px_y = edge_px_per_mm(src, dst)
    warning = ""
    path = args.get("homography_file")
    if path:
        data = load_homography(Path(path))
        stored = data.get("px_per_mm")
        if isinstance(stored, list) and len(stored) >= 2:
            px_x, px_y = float(stored[0]), float(stored[1])
        if data.get("coarse_warning"):
            warning = str(data["coarse_warning"])
    if not warning and min(px_x, px_y) < 1.0:
        warning = (
            "32x32 over this rectangle is coarser than 1 px/mm; "
            "small footprints are candidates only"
        )
    return {"px_per_mm": [px_x, px_y], "coarse_warning": warning}


def _map_hotspot_to_board(args: dict[str, Any], *, hotspot_xy: tuple[float, float]) -> list[dict[str, Any]]:
    from benchgate.lab.board_map import (
        apply_homography,
        attach_schematic_to_hits,
        default_board_margin_mm,
        default_hit_distance_mm,
        hit_footprints,
        homography_from_points,
        load_board_outline,
        load_pcb_footprints,
        load_schematic_index,
        parse_homography_points,
        point_in_board,
    )

    resolution = _homography_resolution(args)
    src, dst = parse_homography_points(_homography_items(args))
    h = homography_from_points(src, dst)
    x_mm, y_mm = apply_homography(h, hotspot_xy)
    extra = {
        "x_mm": x_mm,
        "y_mm": y_mm,
        "px_per_mm": resolution["px_per_mm"],
        "coarse_warning": resolution["coarse_warning"],
    }
    try:
        outline = load_board_outline(args["design_dir"])
    except (FileNotFoundError, ImportError, ValueError):
        outline = None
    margin_mm = default_board_margin_mm(resolution["px_per_mm"])
    if outline is not None and not point_in_board(x_mm, y_mm, outline, margin_mm=margin_mm):
        return [
            {
                "reference": None,
                "distance_mm": None,
                "inside": False,
                "status": "out_of_board",
                "schematic_status": "no_hit",
                "board_outline_mm": list(outline),
                **extra,
            }
        ]
    try:
        footprints = load_pcb_footprints(args["design_dir"])
        sch_index = load_schematic_index(args["design_dir"])
    except (FileNotFoundError, ImportError):
        return [
            {
                "reference": None,
                "distance_mm": None,
                "inside": False,
                "status": "no_pcb",
                "schematic_status": "no_pcb",
                **extra,
            }
        ]
    max_dist = default_hit_distance_mm(resolution["px_per_mm"])
    hits = hit_footprints(x_mm, y_mm, footprints, max_distance_mm=max_dist)
    rows = attach_schematic_to_hits(hits, sch_index)
    for row in rows:
        row.update(extra)
        row["status"] = "hit" if row.get("inside") else "nearby"
    return rows or [
        {
            "reference": None,
            "distance_mm": None,
            "inside": False,
            "status": "hotspot_unassigned",
            "schematic_status": "no_hit",
            **extra,
        }
    ]


def _lab_thermal_map(args: dict[str, Any]) -> dict[str, Any]:
    p = _paths_for_design(args["design_dir"], args)
    store = LabDataStore(p.captured)
    session_id = args.get("session_id") or args.get("session")
    if not session_id:
        raise ValueError("lab_thermal_map requires session_id")
    meta = store.get_session(session_id)
    xy = (float(meta.derived["hotspot_x"]), float(meta.derived["hotspot_y"]))
    hits = _map_hotspot_to_board(args, hotspot_xy=xy)
    resolution = _homography_resolution(args)
    return {
        "session_id": session_id,
        "hotspot_xy": list(xy),
        "kicad_hits": hits,
        "px_per_mm": resolution["px_per_mm"],
        "coarse_warning": resolution["coarse_warning"],
    }


def _lab_thermal_register(args: dict[str, Any]) -> dict[str, Any]:
    from benchgate.lab.thermal import homography_path, register_rectangle, save_homography

    p = _paths_for_design(args["design_dir"], args)
    store = LabDataStore(p.captured)
    session_id = args.get("session_id") or args.get("session")
    if not session_id:
        sessions = [m for m in store.list_sessions() if any(c.kind == "frame2d" for c in m.channels)]
        if not sessions:
            raise FileNotFoundError("no frame2d sessions")
        session_id = sessions[-1].session_id
    meta = store.get_session(session_id)
    frame = store.load_frame2d(session_id, str(args.get("channel") or "thermal"))
    fid = ""
    for tag in meta.tags or []:
        if str(tag).startswith("fixture:"):
            fid = str(tag).split(":", 1)[1]
            break
    fid = str(args.get("fixture_id") or frame.metadata.get("fixture_id") or fid)
    data = register_rectangle(
        frame,
        length_mm=float(args["length_mm"]),
        width_mm=float(args["width_mm"]),
        threshold=None if args.get("threshold") is None else float(args["threshold"]),
        fixture_id=fid,
        session_id=session_id,
    )
    path = Path(args["path"]) if args.get("path") else homography_path(fid or session_id)
    save_homography(data, path)
    data["path"] = str(path)
    return data


def _lab_thermal_verify_refs(args: dict[str, Any]) -> dict[str, Any]:
    from benchgate.lab.board_map import verify_pcb_schematic_refs

    result = verify_pcb_schematic_refs(args["design_dir"])
    return {
        "ok": result.ok,
        "pcb_count": len(result.pcb_refs),
        "schematic_count": len(result.schematic_refs),
        "common_count": len(result.common),
        "pcb_only": result.pcb_only,
        "schematic_only": result.schematic_only,
        "schematic_only_bom": result.schematic_only_bom,
    }


def _apply_cal_if_requested(args: dict[str, Any], idn: str, frame):
    if not args.get("apply_calibration"):
        return frame
    from benchgate.lab.thermal import apply_calibration, calibration_path, load_calibration

    cal_path = calibration_path(idn)
    if not cal_path.is_file():
        raise FileNotFoundError(f"no calibration at {cal_path}; run lab thermal calibrate first")
    return apply_calibration(frame, load_calibration(cal_path))


def _alert_policy_from_args(args: dict[str, Any]):
    from benchgate.lab.thermal_alert import AlertPolicy

    def _opt(key: str) -> float | None:
        val = args.get(key)
        return None if val is None else float(val)

    return AlertPolicy(
        delta_warn=_opt("delta_warn"),
        delta_fail=_opt("delta_fail"),
        k_sigma_warn=_opt("k_sigma_warn"),
        k_sigma_fail=_opt("k_sigma_fail"),
        min_area_px=int(args["min_area_px"]) if args.get("min_area_px") is not None else 2,
        max_regions=int(args["max_regions"]) if args.get("max_regions") is not None else 5,
        require_baseline=bool(args.get("require_baseline", True)),
    )


def _severity_code(severity: str) -> float:
    return {"none": 0.0, "warn": 1.0, "fail": 2.0}[severity]


def _persist_session_alert(
    store: LabDataStore,
    session_id: str,
    extra_derived: dict[str, float],
    artifact: dict[str, Any],
    *,
    component_ref: str | None = None,
) -> None:
    import yaml

    meta = store.get_session(session_id)
    if meta.path is None:
        raise FileNotFoundError(f"session {session_id} has no path")
    derived = dict(meta.derived)
    derived.update(extra_derived)
    (meta.path / "derived.json").write_text(json.dumps(derived, indent=2), encoding="utf-8")
    (meta.path / "thermal_alert.json").write_text(
        json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    data = yaml.safe_load((meta.path / "session.yaml").read_text(encoding="utf-8"))
    data["derived"] = derived
    if component_ref:
        data["component_ref"] = component_ref
    (meta.path / "session.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )


def _map_alert_regions(args: dict[str, Any], regions) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from dataclasses import asdict

    resolution: dict[str, Any] = {}
    has_h = bool(args.get("homography") or args.get("homography_file"))
    if has_h:
        resolution = _homography_resolution(args)
    mapped: list[dict[str, Any]] = []
    for region in regions:
        row = asdict(region)
        if has_h:
            hits = _map_hotspot_to_board(args, hotspot_xy=(region.centroid_x, region.centroid_y))
            row["kicad_hits"] = hits
            row["status"] = hits[0]["status"] if hits else "hotspot_unassigned"
            row["x_mm"] = hits[0].get("x_mm") if hits else None
            row["y_mm"] = hits[0].get("y_mm") if hits else None
        else:
            row["kicad_hits"] = []
            row["status"] = "unmapped"
        mapped.append(row)
    return mapped, resolution


def _lab_thermal_baseline(args: dict[str, Any]) -> dict[str, Any]:
    import time

    import numpy as np

    from benchgate.lab.thermal import apply_thermal_defaults, baseline_path, fixture_id, save_baseline

    # `thermal.frames` sizes a capture burst; a baseline needs enough samples for a
    # per-pixel sigma, so it takes `thermal.baseline_frames` (or the CLI default).
    cfg = dict(_thermal_cfg(args))
    cfg.pop("frames", None)
    if cfg.get("baseline_frames") is not None:
        cfg["frames"] = cfg["baseline_frames"]
    args = apply_thermal_defaults(args, cfg)
    p = _paths_for_design(args["design_dir"], args)
    bench = _open_bench(p, args)
    inst = bench.select(role="thermal", instrument=args.get("instrument"))
    n = max(1, int(args.get("frames") or 16))
    warmup_s = float(args.get("warmup_s", 0.0))
    try:
        if warmup_s > 0:
            time.sleep(warmup_s)
        if n == 1:
            frame = inst.capture_frame()  # type: ignore[attr-defined]
            stack = np.asarray(frame.values, dtype=float)[np.newaxis, ...]
            unit = frame.unit
            meta_extra = dict(frame.metadata)
        else:
            series = inst.capture_burst(n)  # type: ignore[attr-defined]
            stack = np.asarray(series.values, dtype=float)
            unit = series.unit
            meta_extra = dict(series.metadata)
        values = np.median(stack, axis=0)
        sigma = stack.std(axis=0, ddof=0)
        emissivity = 1.0
        if hasattr(inst, "get_emissivity"):
            try:
                emissivity = float(inst.get_emissivity())
            except Exception:
                emissivity = float(meta_extra.get("emissivity", 1.0) or 1.0)
        fid = fixture_id(
            instrument_idn=inst.identify(),
            emissivity=emissivity,
            warmup_s=warmup_s,
            distance_mm=args.get("distance_mm"),
            ambient_bin=str(args.get("ambient_bin") or "unknown"),
        )
        path = Path(args["path"]) if args.get("path") else baseline_path(fid)
        save_baseline(
            values,
            sigma,
            {
                "fixture_id": fid,
                "instrument_idn": inst.identify(),
                "unit": unit,
                "emissivity": emissivity,
                "ambient_bin": str(args.get("ambient_bin") or "unknown"),
                "distance_mm": args.get("distance_mm"),
                "n_frames": n,
                "captured_at": datetime.now(timezone.utc).isoformat(),
            },
            path,
        )
    finally:
        inst.disconnect()
    return {
        "fixture_id": fid,
        "path": str(path),
        "sidecar": str(path.with_suffix(".yaml")),
        "shape": [int(values.shape[0]), int(values.shape[1])],
        "n_frames": n,
        "unit": unit,
    }


def _lab_thermal_alert(args: dict[str, Any]) -> dict[str, Any]:
    import time

    from benchgate.lab.thermal import (
        apply_thermal_defaults,
        baseline_path,
        fixture_id,
        load_baseline,
        summarize_thermal,
    )
    from benchgate.lab.thermal_alert import alert_result_to_dict, evaluate_alert

    cfg = _thermal_cfg(args)
    args = apply_thermal_defaults(args, cfg)
    p = _paths_for_design(args["design_dir"], args)
    store = LabDataStore(p.captured)
    session_id = args.get("session_id") or args.get("session")
    policy = _alert_policy_from_args(args)
    geometry = _geometry_from_args(args)
    inst = None
    fid = ""
    pending: dict[str, Any] | None = None
    if session_id:
        frame = store.load_frame2d(session_id, str(args.get("channel") or "thermal"))
        idn = str(frame.metadata.get("idn") or "")
        frame = _apply_cal_if_requested(args, idn, frame)
        fid = str(frame.metadata.get("fixture_id") or "")
        if not fid:
            fid = fixture_id(
                instrument_idn=idn,
                emissivity=float(frame.metadata.get("emissivity") or 1.0),
                distance_mm=frame.metadata.get("distance_mm"),
                ambient_bin=str(frame.metadata.get("ambient_bin") or "unknown"),
            )
        meta = store.get_session(session_id)
    else:
        bench = _open_bench(p, args)
        inst = bench.select(role="thermal", instrument=args.get("instrument"))
        frames_n = max(1, int(args.get("frames", 1)))
        warmup_s = float(args.get("warmup_s", 0.0))
        try:
            if warmup_s > 0:
                time.sleep(warmup_s)
            if frames_n == 1:
                frame = inst.capture_frame()  # type: ignore[attr-defined]
            else:
                series = inst.capture_burst(frames_n)  # type: ignore[attr-defined]
                frame = _reduce_series(series, str(args.get("reduce") or "median"))
            emissivity = 1.0
            if hasattr(inst, "get_emissivity"):
                try:
                    emissivity = float(inst.get_emissivity())
                except Exception:
                    emissivity = float(frame.metadata.get("emissivity", 1.0) or 1.0)
            frame = _apply_cal_if_requested(args, inst.identify(), frame)
            fid = fixture_id(
                instrument_idn=inst.identify(),
                emissivity=emissivity,
                warmup_s=warmup_s,
                distance_mm=args.get("distance_mm"),
                ambient_bin=str(args.get("ambient_bin") or "unknown"),
            )
            derived_cap = summarize_thermal(
                frame,
                geometry=geometry,
                instrument_idn=inst.identify(),
                emissivity=emissivity,
                warmup_s=warmup_s,
                distance_mm=args.get("distance_mm"),
                ambient_bin=str(args.get("ambient_bin") or "unknown"),
                known_fixture_id=fid,
            )
            meta_extra = dict(frame.metadata)
            meta_extra["fixture_id"] = fid
            from benchgate.instruments.types import Frame2D

            frame = Frame2D(
                values=frame.values,
                unit=frame.unit,
                quantity=frame.quantity,
                timestamp=frame.timestamp,
                mask=frame.mask,
                calibration=frame.calibration,
                metadata=meta_extra,
            )
            # Written only after the verdict, so a clean poll can leave no session.
            pending = {
                "derived": derived_cap,
                "roles": {"thermal": bench.instrument_for_role("thermal")},
                "instruments": {"thermal": inst.identify()},
                "tags": _thermal_alert_tags(fid, args, cfg),
                "notes": f"unit={frame.unit} fixture_id={fid}",
            }
        finally:
            inst.disconnect()

    baseline = None
    sigma = None
    baseline_unit = None
    bpath = Path(args["baseline_file"]) if args.get("baseline_file") else (baseline_path(fid) if fid else None)
    if bpath is not None and bpath.is_file():
        baseline, sigma, bmeta = load_baseline(bpath)
        baseline_unit = str(bmeta.get("unit") or "") or None
    elif policy.require_baseline:
        raise FileNotFoundError(f"no thermal baseline at {bpath}; run lab thermal baseline first")

    alert = evaluate_alert(
        frame,
        baseline=baseline,
        sigma=sigma,
        policy=policy,
        baseline_unit=baseline_unit,
        geometry=geometry,
    )
    mapped, resolution = _map_alert_regions(args, alert.regions)
    extra: dict[str, float] = {
        "alert_severity_code": _severity_code(alert.severity),
        "alert_region_count": float(len(alert.regions)),
        "t_delta_peak": float(alert.regions[0].peak_delta) if alert.regions else 0.0,
        "t_ref": float(alert.t_ref),
        "alert_baseline_used": 1.0 if alert.baseline_used else 0.0,
    }
    component_ref = None
    if mapped:
        hits = mapped[0].get("kicad_hits") or []
        inside = [h for h in hits if h.get("inside") and h.get("reference")]
        if inside:
            component_ref = str(inside[0]["reference"])
        with_ref = [h for h in hits if h.get("reference") and h.get("distance_mm") is not None]
        if with_ref:
            extra["alert_top_ref_distance_mm"] = float(with_ref[0]["distance_mm"])
    artifact = alert_result_to_dict(alert)
    artifact["regions"] = mapped
    artifact["px_per_mm"] = resolution.get("px_per_mm")
    artifact["coarse_warning"] = resolution.get("coarse_warning")
    if pending is None:
        _persist_session_alert(store, session_id, extra, artifact, component_ref=component_ref)
    elif args.get("skip_clear_sessions") and alert.severity == "none":
        session_id = None
    else:
        derived = dict(pending["derived"])
        derived.update(extra)
        meta = store.write_session(
            design=str(p.design),
            component_ref=component_ref,
            frames={"thermal": frame},
            derived=derived,
            roles=pending["roles"],
            instruments=pending["instruments"],
            tags=pending["tags"],
            notes=pending["notes"],
        )
        session_id = meta.session_id
        if meta.path is not None:
            (meta.path / "thermal_alert.json").write_text(
                json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8"
            )
    return {
        "session_id": session_id,
        "severity": alert.severity,
        "unit": alert.unit,
        "t_ref": alert.t_ref,
        "threshold_warn": alert.threshold_warn,
        "threshold_fail": alert.threshold_fail,
        "policy_source": alert.policy_source,
        "baseline_used": alert.baseline_used,
        "regions": mapped,
        "px_per_mm": resolution.get("px_per_mm"),
        "coarse_warning": resolution.get("coarse_warning"),
        "fixture_id": fid,
    }


def _thermal_alert_tags(fid: str, args: dict[str, Any], cfg: dict[str, Any]) -> list[str]:
    tags = [f"fixture:{fid}", "thermal-alert"]
    extra = args.get("session_tag") or cfg.get("session_tag")
    if extra and str(extra) not in tags:
        tags.append(str(extra))
    return tags


def _lab_thermal_watch(args: dict[str, Any]) -> dict[str, Any]:
    import time

    from benchgate.lab.thermal import apply_thermal_defaults

    cfg = _thermal_cfg(args)
    args = apply_thermal_defaults(args, cfg)
    if args.get("session_tag") is None:
        args["session_tag"] = str(cfg.get("session_tag") or "thermal-gate")
    if args.get("skip_clear_sessions") is None:
        args["skip_clear_sessions"] = True
    interval = args.get("interval_s")
    if interval is None:
        interval = cfg.get("watch_interval_s", 30)
    interval_s = float(interval)
    max_iter = int(args.get("max_iterations") or 0)
    quiet = bool(args.get("quiet"))
    runs: list[dict[str, Any]] = []
    i = 0
    while True:
        i += 1
        one = dict(args)
        one.pop("session_id", None)
        one.pop("session", None)
        try:
            result = _lab_thermal_alert(one)
            row = {
                "ok": True,
                "session_id": result.get("session_id"),
                "severity": result.get("severity"),
            }
            runs.append(row)
            if not quiet:
                print(json.dumps(result, ensure_ascii=False), flush=True)
        except Exception as exc:  # noqa: BLE001 — keep polling
            row = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            runs.append(row)
            if not quiet:
                print(json.dumps(row, ensure_ascii=False), flush=True)
        if max_iter and i >= max_iter:
            break
        time.sleep(max(interval_s, 0.0))
    return {"iterations": i, "runs": runs}
