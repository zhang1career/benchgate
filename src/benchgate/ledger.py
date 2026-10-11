"""Per-design record ledger: one JSON line per benchgate result.

Every Session, simulation, sweep, tolerance study, gate report, model build and
co-simulation that benchgate writes for a design also appends one line to
``<design>/models/index.jsonl``. The line carries the verdict inline, so the
history survives even though the report files themselves are overwritten on the
next run and usually ignored by Git. ``models/INDEX.md`` is the same ledger
rendered for people.

Each record stores a digest of the design inputs (schematic sheets, manifest,
blocks, sim profile) at the time it was written. Comparing that digest with the
current one is how a ``pass`` is recognised as stale.

Ledger writes never break the operation that triggered them: failures are
reported on stderr and swallowed. ``BENCHGATE_NO_INDEX=1`` disables the ledger.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sys
from collections import Counter
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

INDEX_FILE = "index.jsonl"
INDEX_MD = "INDEX.md"

KIND_LABELS = {
    "gate": "gate",
    "sim_run": "仿真",
    "stress_sweep": "应力扫描",
    "tolerance": "容差",
    "sim_sweep": "扫参",
    "block_sweep": "模块扫参",
    "cosim": "协同仿真",
    "model_build": "建模",
    "session": "采集",
}
STATUS_ORDER = tuple(KIND_LABELS)

# Report file name -> record kind, used when backfilling from existing reports.
REPORT_FILES = {
    "sim_report.json": "sim_run",
    "gate_report.json": "gate",
    "sweep_report.json": "sim_sweep",
    "block_sweep_report.json": "block_sweep",
    "stress_sweep_report.json": "stress_sweep",
    "mc_tolerance.json": "tolerance",
    "cosim_report.json": "cosim",
}
# Default output of each kind; a record written elsewhere (a scratch manifest, an
# alternate out dir) is scope "alt" and never counts as the design's verdict.
CANONICAL_ARTIFACTS = {
    "sim_run": "reports/sim/sim_report.json",
    "gate": "reports/gate_report.json",
}

_BLOCK_SUFFIXES = (".net", ".cir", ".asc")
_SKIP_DIRS = {"node_modules", "reports", "__pycache__", "venv", ".venv", "Project Outputs"}
_RENDER_LIMIT = 300

_context: ContextVar[dict[str, Any]] = ContextVar("benchgate_record_context", default={})


def disabled() -> bool:
    return os.environ.get("BENCHGATE_NO_INDEX", "").strip().lower() in {"1", "true", "yes"}


@contextmanager
def record_context(
    *,
    purpose: str | None = None,
    chat_id: str | None = None,
    via: str | None = None,
    tags: Iterable[str] | None = None,
) -> Iterator[None]:
    """Attach why/who to every record written inside the block. Inner values win."""
    merged = dict(_context.get())
    if purpose:
        merged["purpose"] = str(purpose).strip()
    if chat_id:
        merged["chat_id"] = str(chat_id).strip()
    if via:
        merged["via"] = via
    if tags:
        merged["tags"] = sorted(set(merged.get("tags", [])) | {str(t) for t in tags})
    token = _context.set(merged)
    try:
        yield
    finally:
        _context.reset(token)


def _warn(message: str) -> None:
    print(f"benchgate: index: {message}", file=sys.stderr)


# --- design discovery -------------------------------------------------------


def is_design_dir(path: Path) -> bool:
    return (path / "models").is_dir() and any(path.glob("*.kicad_pro"))


def find_design_dir(start: Path) -> Path | None:
    """Nearest ancestor (inclusive) that looks like a benchgate design."""
    path = Path(start).resolve()
    if path.is_file():
        path = path.parent
    for candidate in (path, *path.parents):
        if is_design_dir(candidate):
            return candidate
    return None


def design_from_manifest(manifest_path: Path) -> Path | None:
    manifest_path = Path(manifest_path).resolve()
    if manifest_path.parent.name == "models" and is_design_dir(manifest_path.parent.parent):
        return manifest_path.parent.parent
    return None


def discover_designs(root: Path, *, max_depth: int = 4) -> list[Path]:
    """Designs under ``root`` that benchgate has something for.

    A KiCad project with ``models/manifest.yaml`` (can be simulated), or any
    directory whose ``models/`` already holds a record ledger or lab captures.
    """
    def managed(path: Path) -> bool:
        models = path / "models"
        return (
            (models / INDEX_FILE).is_file()
            or (models / "captured").is_dir()
            or ((models / "manifest.yaml").is_file() and any(path.glob("*.kicad_pro")))
        )

    return _walk_dirs(root, managed, max_depth)


def kicad_project_dirs(root: Path, *, max_depth: int = 4) -> list[Path]:
    """Directories under ``root`` holding a ``*.kicad_pro``."""
    return _walk_dirs(root, lambda path: any(path.glob("*.kicad_pro")), max_depth)


def _walk_dirs(root: Path, match: Any, max_depth: int) -> list[Path]:
    found: list[Path] = []

    def walk(path: Path, depth: int) -> None:
        if match(path):
            found.append(path)
        if depth >= max_depth:
            return
        try:
            children = sorted(p for p in path.iterdir() if p.is_dir())
        except OSError:
            return
        for child in children:
            if child.name.startswith(".") or child.name in _SKIP_DIRS or child.name == "models":
                continue
            walk(child, depth + 1)

    walk(Path(root).resolve(), 0)
    return found


# --- input digest -----------------------------------------------------------


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _schematics(design: Path) -> list[Path]:
    nested: dict[Path, bool] = {}

    def inside_nested_design(path: Path) -> bool:
        for parent in path.parents:
            if parent == design:
                return False
            if parent not in nested:
                nested[parent] = any(parent.glob("*.kicad_pro"))
            if nested[parent]:
                return True
        return False

    out = []
    for path in design.rglob("*.kicad_sch"):
        rel_parts = path.relative_to(design).parts
        if any(part.startswith(".") or part in _SKIP_DIRS for part in rel_parts):
            continue
        if path.name.startswith("_autosave-") or inside_nested_design(path):
            continue
        out.append(path)
    return sorted(out)


def input_paths(design: Path) -> dict[str, Path]:
    """Files whose content decides the sim/gate verdict, keyed by a stable label."""
    from benchgate.paths import benchgate_paths

    design = Path(design).resolve()
    files: dict[str, Path] = {}
    for path in _schematics(design):
        files[path.relative_to(design).as_posix()] = path
    models = design / "models"
    for name in ("manifest.yaml", "blocks.yaml"):
        if (models / name).is_file():
            files[f"models/{name}"] = models / name
    blocks = models / "blocks"
    if blocks.is_dir():
        for path in sorted(blocks.rglob("*")):
            if path.is_file() and (path.suffix.lower() in _BLOCK_SUFFIXES or path.name.endswith(".metrics.json")):
                files[path.relative_to(design).as_posix()] = path
    label = sim_profiles_label(design)
    if label:
        files[label] = benchgate_paths(design).sim_profile
    return files


def sim_profiles_label(design: Path) -> str | None:
    """The sim_profiles.yaml this design uses: ``models/sim_profiles.yaml`` or the shared one."""
    from benchgate.paths import benchgate_paths

    design = Path(design).resolve()
    profile = benchgate_paths(design).sim_profile
    if not profile.is_file():
        return None
    try:
        return profile.relative_to(design).as_posix()
    except ValueError:
        return "$BENCHGATE_HOME/config/" + profile.name


def current_inputs(design: Path) -> tuple[str, dict[str, str]]:
    hashes = {key: _hash_file(path) for key, path in input_paths(design).items()}
    digest = hashlib.sha256(json.dumps(sorted(hashes.items())).encode()).hexdigest()[:16]
    return f"sha256:{digest}", hashes


def changed_inputs(before: dict[str, str] | None, after: dict[str, str]) -> list[str]:
    before = before or {}
    keys = set(before) | set(after)
    return sorted(k for k in keys if before.get(k) != after.get(k))


# --- verdict summarizers ----------------------------------------------------


def summarize_sim(report: dict) -> tuple[str, dict]:
    items = ((report.get("checks") or {}).get("checks")) or []
    passed = sum(1 for c in items if c.get("passed"))
    failed = [f"{c.get('signal')}:{c.get('metric')}" for c in items if not c.get("passed")]
    issues = ((report.get("preflight") or {}).get("issues")) or []
    dropped = sorted({i.get("reference") for i in issues if i.get("code") == "unmodeled_placeholder" and i.get("reference")})
    stress = report.get("stress") or {}
    summary = {
        "ngspice_ok": bool(report.get("ngspice_ok", True)),
        "checks_passed": passed,
        "checks_total": len(items),
        "failed_checks": failed[:8],
        "dropped_models": dropped,
    }
    if stress:
        summary["stress_passed"] = bool(stress.get("passed"))
    return ("pass" if report.get("success") else "fail"), summary


def summarize_gate(report: dict) -> tuple[str, dict]:
    s = report.get("summary") or {}
    entries = report.get("entries") or []
    rules = s.get("rules") or {}
    stress = s.get("stress_summary") or {}
    specs_checked = sum(1 for e in entries if e.get("spec_status") not in (None, "n/a"))
    rule_results = len(rules.get("results") or [])
    summary = {
        "spec_failures": int(s.get("spec_failures") or 0),
        "specs_checked": specs_checked,
        "rules_checked": rule_results,
        "rules_failures": int(rules.get("failures") or 0),
        "waveform_failures": int(s.get("waveform_failures") or 0),
        "erc_errors": int(s.get("erc_errors") or 0),
        "with_sim": int(s.get("with_sim") or 0),
        "with_bench": int(s.get("with_bench") or 0),
        "compared": int(s.get("compared") or 0),
    }
    if stress:
        summary["stress_passed"] = bool(stress.get("passed"))
    failed = (
        summary["spec_failures"] > 0
        or summary["waveform_failures"] > 0
        or summary["erc_errors"] > 0
        or (rules and rules.get("passed") is False)
        or (stress and stress.get("passed") is False)
    )
    checked = specs_checked or rule_results or summary["compared"] or bool(stress)
    status = "fail" if failed else ("pass" if checked else "unchecked")
    return status, summary


def summarize_sweep(report: dict) -> tuple[str, dict]:
    points = report.get("points") or []
    failed = sum(1 for p in points if p.get("passed") is False)
    judged = sum(1 for p in points if p.get("passed") is not None)
    ngspice_failures = sum(1 for p in points if p.get("ngspice_ok") is False)
    summary = {
        "metric": report.get("metric"),
        "points": len(points),
        "failed": failed,
        "ngspice_failures": ngspice_failures,
    }
    if failed or ngspice_failures:
        return "fail", summary
    return ("pass" if judged else "done"), summary


def summarize_stress(report: dict) -> tuple[str, dict]:
    worst = report.get("worst") or {}
    results = worst.get("results") or []
    summary = {
        "points": len(report.get("points") or []),
        "failures": sum(1 for r in results if r.get("passed") is False),
        "warnings": len(worst.get("warnings") or []),
    }
    return ("pass" if worst.get("passed") else "fail"), summary


def summarize_tolerance(report: dict) -> tuple[str, dict]:
    summary = {
        "yield_pct": report.get("yield_pct"),
        "yield_ci_low_pct": report.get("yield_ci_low_pct"),
        "yield_ci_high_pct": report.get("yield_ci_high_pct"),
        "n_samples": report.get("n_samples"),
        "strategy": report.get("strategy"),
    }
    return "done", {k: v for k, v in summary.items() if v is not None}


def summarize_cosim(report: dict) -> tuple[str, dict]:
    summary = {
        "steps": report.get("steps"),
        "final_vout_v": report.get("final_vout_v"),
        "final_duty": report.get("final_duty"),
    }
    return ("pass" if report.get("success") else "fail"), {k: v for k, v in summary.items() if v is not None}


def _instrument_drivers(instruments: Any) -> list[str]:
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, val in node.items():
                if isinstance(val, (dict, list)):
                    walk(val)
                elif str(key).endswith("driver") and val:
                    found.add(str(val))

    walk(instruments)
    return sorted(found)


def summarize_session(meta: dict) -> tuple[str, dict]:
    channels = [c for c in (meta.get("channels") or []) if isinstance(c, dict)]
    empty = [c.get("name") for c in channels if (c.get("extra") or {}).get("n") == 0]
    suspect = [
        c.get("name")
        for c in channels
        if c.get("kind") == "waveform"
        and c.get("name") not in empty
        and isinstance((c.get("extra") or {}).get("sample_rate_hz"), (int, float))
        and c["extra"]["sample_rate_hz"] < 1.0
    ]
    summary = {
        "channels": [c.get("name") for c in channels],
        "empty_channels": empty,
        "suspect_channels": suspect,
        "instruments": _instrument_drivers(meta.get("instruments")),
        "roles": {k: v for k, v in (meta.get("roles") or {}).items() if v},
        "component_ref": meta.get("component_ref"),
        "attachments": list(meta.get("attachments") or []),
    }
    summary = {k: v for k, v in summary.items() if v not in (None, [], {})}
    status = "empty" if empty else ("suspect" if suspect else "ok")
    return status, summary


SUMMARIZERS = {
    "sim_run": summarize_sim,
    "gate": summarize_gate,
    "sim_sweep": summarize_sweep,
    "block_sweep": summarize_sweep,
    "stress_sweep": summarize_stress,
    "tolerance": summarize_tolerance,
    "cosim": summarize_cosim,
}


def summary_line(rec: dict) -> str:
    kind = rec.get("kind")
    s = rec.get("summary") or {}
    if kind == "gate":
        parts = []
        if s.get("specs_checked"):
            parts.append(f"spec 失败 {s.get('spec_failures', 0)}/{s['specs_checked']}")
        if s.get("rules_checked"):
            parts.append(f"规则失败 {s.get('rules_failures', 0)}/{s['rules_checked']}")
        if s.get("waveform_failures"):
            parts.append(f"波形失败 {s['waveform_failures']}")
        if s.get("erc_errors"):
            parts.append(f"ERC 错误 {s['erc_errors']}")
        if "stress_passed" in s:
            parts.append("应力 " + ("通过" if s["stress_passed"] else "失败"))
        parts.append(f"bench 对比 {s.get('compared', 0)}")
        if not s.get("specs_checked") and not s.get("rules_checked"):
            parts.append("没有可判定的 spec 或规则")
        return " · ".join(parts)
    if kind == "sim_run":
        parts = []
        if not s.get("ngspice_ok", True):
            parts.append("ngspice 失败")
        if s.get("checks_total"):
            parts.append(f"检查 {s.get('checks_passed', 0)}/{s['checks_total']} 通过")
            if s.get("failed_checks"):
                parts.append("失败：" + ", ".join(s["failed_checks"][:3]))
        else:
            parts.append("没有检查项")
        if s.get("dropped_models"):
            parts.append(f"丢弃 {len(s['dropped_models'])} 个未建模器件")
        return " · ".join(parts)
    if kind in ("sim_sweep", "block_sweep"):
        line = f"{s.get('points', 0)} 点"
        if s.get("failed"):
            line += f" · 失败 {s['failed']}"
        if s.get("ngspice_failures"):
            line += f" · ngspice 失败 {s['ngspice_failures']}"
        if s.get("metric"):
            line += f" · {s['metric']}"
        return line
    if kind == "stress_sweep":
        return f"{s.get('points', 0)} 点 · 超限 {s.get('failures', 0)} · 警告 {s.get('warnings', 0)}"
    if kind == "tolerance":
        y = s.get("yield_pct")
        line = f"良率 {y:.0f}%" if isinstance(y, (int, float)) else "良率未知"
        lo, hi = s.get("yield_ci_low_pct"), s.get("yield_ci_high_pct")
        if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
            line += f"（CI {lo:.0f}–{hi:.0f}%）"
        if s.get("n_samples"):
            line += f" · {s['n_samples']} 样本"
        return line
    if kind == "cosim":
        if isinstance(s.get("final_vout_v"), (int, float)):
            return f"Vout {s['final_vout_v']:.3g} V · {s.get('steps', '?')} 步"
        return ""
    if kind == "model_build":
        return f"{s.get('reference') or s.get('kicad_key', '')} ← {s.get('subckt', '')} ({s.get('provider', '')})"
    if kind == "session":
        chans = s.get("channels") or []
        line = ", ".join(chans[:4]) + (f" 等 {len(chans)} 路" if len(chans) > 4 else "")
        if s.get("attachments"):
            line += (" · " if line else "") + f"附件 {len(s['attachments'])}"
        if s.get("empty_channels"):
            line += f" · 空通道 {len(s['empty_channels'])}"
        if s.get("suspect_channels"):
            line += f" · 采样率异常 {len(s['suspect_channels'])}"
        if s.get("component_ref"):
            line += f" · {s['component_ref']}"
        return line
    return ""


# --- write ------------------------------------------------------------------


def _ts(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def new_record_id(kind: str, at: datetime | None = None) -> str:
    return f"{kind}-{_ts(at or datetime.now(timezone.utc))}_{secrets.token_hex(2)}"


def _rel(design: Path, path: Path | str) -> str:
    p = Path(path)
    try:
        return p.resolve().relative_to(design).as_posix()
    except (ValueError, OSError):
        return str(p)


def _clean(rec: dict) -> dict:
    return {k: v for k, v in rec.items() if v not in (None, "", [], {})}


def append_record(
    design_dir: Path | str | None,
    kind: str,
    *,
    status: str,
    summary: dict | None = None,
    artifacts: Iterable[Path | str] = (),
    parents: Iterable[str] = (),
    tags: Iterable[str] = (),
    purpose: str | None = None,
    record_id: str | None = None,
    at: datetime | None = None,
    with_inputs: bool = True,
    extra: dict | None = None,
) -> dict | None:
    if design_dir is None or disabled():
        return None
    try:
        design = Path(design_dir).resolve()
        if not (design / "models").is_dir():
            return None
        ctx = _context.get()
        at = at or datetime.now(timezone.utc)
        rec: dict[str, Any] = {
            "id": record_id or new_record_id(kind, at),
            "kind": kind,
            "at": at.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "benchgate": _version(),
            "status": status,
            "summary": summary or {},
            "artifacts": [_rel(design, a) for a in artifacts],
            "parents": list(parents),
            "tags": sorted(set(tags) | set(ctx.get("tags", []))),
            "purpose": ctx.get("purpose") or purpose,
            "chat_id": ctx.get("chat_id"),
            "via": ctx.get("via"),
        }
        canonical = CANONICAL_ARTIFACTS.get(kind)
        if canonical and canonical not in rec["artifacts"]:
            rec["scope"] = "alt"
        if extra:
            rec.update(extra)
        if with_inputs:
            rec["inputs"], rec["input_files"] = current_inputs(design)
        rec = _clean(rec)
        with (design / "models" / INDEX_FILE).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        render_index(design)
        return rec
    except Exception as exc:  # noqa: BLE001 — the ledger must never break the run it records
        _warn(f"record skipped ({kind}): {exc}")
        return None


def _version() -> str:
    from benchgate import __version__

    return __version__


def record_report(design_dir: Path | str | None, kind: str, report: dict, report_path: Path | str, **kw: Any) -> dict | None:
    """Summarize a report dict with the kind's summarizer and append it."""
    try:
        status, summary = SUMMARIZERS[kind](report)
    except Exception as exc:  # noqa: BLE001
        _warn(f"could not summarize {kind}: {exc}")
        return None
    return append_record(design_dir, kind, status=status, summary=summary, artifacts=[report_path], **kw)


def record_session(captured_root: Path, meta: dict) -> dict | None:
    """Sessions are indexed only when the store lives at ``<design>/models/captured``."""
    root = Path(captured_root).resolve()
    if root.name != "captured" or root.parent.name != "models":
        return None
    design = root.parent.parent
    status, summary = summarize_session(meta)
    sid = meta["session_id"]
    return append_record(
        design,
        "session",
        status=status,
        summary=summary,
        artifacts=[root / "sessions" / sid / "session.yaml"],
        tags=meta.get("tags") or [],
        purpose=(meta.get("notes") or "").strip()[:200] or None,
        record_id=sid,
        at=_parse_iso(meta.get("captured_at")),
        extra={"session_id": sid},
    )


def collect_session_ids(obj: Any) -> list[str]:
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, val in node.items():
                if key == "session_id" and isinstance(val, str):
                    found.add(val)
                else:
                    walk(val)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(obj)
    return sorted(found)


def parent_for_artifact(design: Path | str | None, artifact: Path | str | None) -> list[str]:
    if design is None or artifact is None:
        return []
    design = Path(design).resolve()
    rel = _rel(design, artifact)
    for rec in reversed(load_records(design)):
        if rel in (rec.get("artifacts") or []):
            return [rec["id"]]
    return []


# --- read -------------------------------------------------------------------


def _parse_iso(text: str | datetime | None) -> datetime | None:
    if text is None:
        return None
    if isinstance(text, datetime):
        dt = text
    else:
        try:
            dt = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def load_records(design_dir: Path | str) -> list[dict]:
    path = Path(design_dir) / "models" / INDEX_FILE
    if not path.is_file():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def query(
    design_dir: Path | str,
    *,
    kind: str | None = None,
    status: str | None = None,
    since: str | datetime | None = None,
    until: str | datetime | None = None,
    tag: str | None = None,
    text: str | None = None,
    limit: int | None = 50,
) -> list[dict]:
    lo, hi = _parse_iso(since), _parse_iso(until)
    rows = []
    for rec in load_records(design_dir):
        if kind and rec.get("kind") != kind:
            continue
        if status and rec.get("status") != status:
            continue
        at = _parse_iso(rec.get("at"))
        if lo and (at is None or at < lo):
            continue
        if hi and (at is None or at > hi):
            continue
        if tag and tag not in (rec.get("tags") or []):
            continue
        if text and text.lower() not in json.dumps(rec, ensure_ascii=False).lower():
            continue
        rows.append(rec)
    rows.sort(key=lambda r: r.get("at") or "", reverse=True)
    return rows[:limit] if limit else rows


def latest(records: list[dict], kind: str, *, canonical: bool = True, with_inputs: bool = False) -> dict | None:
    best = None
    for rec in records:
        if rec.get("kind") != kind:
            continue
        if canonical and rec.get("scope") == "alt":
            continue
        if with_inputs and not rec.get("inputs"):
            continue
        if best is None or (rec.get("at") or "") >= (best.get("at") or ""):
            best = rec
    return best


def freshness(rec: dict, digest: str, files: dict[str, str]) -> tuple[bool | None, list[str]]:
    """(stale?, changed inputs). ``None`` when the record predates input tracking."""
    if not rec.get("inputs"):
        return None, []
    if rec["inputs"] == digest:
        return False, []
    return True, changed_inputs(rec.get("input_files"), files)


def design_status(design_dir: Path | str) -> dict:
    design = Path(design_dir).resolve()
    records = load_records(design)
    digest, files = current_inputs(design)
    kinds: dict[str, dict] = {}
    for kind in STATUS_ORDER:
        rec = latest(records, kind)
        if rec is None:
            continue
        stale, changed = freshness(rec, digest, files) if kind != "session" else (None, [])
        kinds[kind] = {
            "id": rec["id"],
            "at": rec.get("at"),
            "status": rec.get("status"),
            "summary": summary_line(rec),
            "stale": stale,
            "changed_inputs": changed,
            "backfilled": bool(rec.get("backfilled")),
        }
    return {
        "design": str(design),
        "sim_profiles": sim_profiles_label(design),
        "inputs": digest,
        "records": len(records),
        "kinds": kinds,
        "problems": verification_problems(design, records=records, digest=digest, files=files),
    }


def verification_problems(
    design_dir: Path | str,
    *,
    records: list[dict] | None = None,
    digest: str | None = None,
    files: dict[str, str] | None = None,
) -> list[str]:
    """Why the design's current state is not verified; empty means it is."""
    design = Path(design_dir).resolve()
    records = load_records(design) if records is None else records
    if digest is None or files is None:
        digest, files = current_inputs(design)
    problems: list[str] = []
    gate = latest(records, "gate", with_inputs=True)
    if gate is None:
        return ["没有带输入记录的 gate 结论（从未运行，或只有回填记录）"]
    stale, changed = freshness(gate, digest, files)
    if stale:
        shown = ", ".join(changed[:4]) + (f" 等 {len(changed)} 个文件" if len(changed) > 4 else "")
        problems.append(f"gate 已过期：之后改过 {shown}")
        return problems
    if gate.get("status") == "fail":
        problems.append(f"gate 失败：{summary_line(gate)}")
    sim = latest(records, "sim_run", with_inputs=True)
    if sim and sim.get("inputs") == digest and sim.get("status") == "fail":
        problems.append(f"仿真失败：{summary_line(sim)}")
    return problems


# --- backfill ---------------------------------------------------------------

_PT_DIR = re.compile(r"^pt\d+$")


def rebuild(design_dir: Path | str, *, dry_run: bool = False) -> dict:
    """Index Sessions and the latest report files that the ledger has not seen yet.

    Sessions are fully recoverable. Reports are overwritten on every run, so only
    the copy currently on disk can be indexed; earlier runs are gone. Backfilled
    records carry no input digest, so they never count as a current verdict.
    """
    import yaml

    design = Path(design_dir).resolve()
    records = load_records(design)
    known_ids = {r.get("id") for r in records}
    known_art = {(r.get("kind"), a, r.get("at")) for r in records for a in (r.get("artifacts") or [])}
    new: list[dict] = []

    sessions = design / "models" / "captured" / "sessions"
    if sessions.is_dir():
        for sdir in sorted(p for p in sessions.iterdir() if p.is_dir()):
            meta_path = sdir / "session.yaml"
            if sdir.name in known_ids or not meta_path.is_file():
                continue
            try:
                meta = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
            except Exception:  # noqa: BLE001
                continue
            status, summary = summarize_session(meta)
            at = _parse_iso(meta.get("captured_at")) or datetime.fromtimestamp(meta_path.stat().st_mtime, timezone.utc)
            new.append(
                _clean(
                    {
                        "id": sdir.name,
                        "kind": "session",
                        "at": at.isoformat(timespec="seconds"),
                        "status": status,
                        "summary": summary,
                        "artifacts": [_rel(design, meta_path)],
                        "tags": sorted(meta.get("tags") or []),
                        "purpose": (meta.get("notes") or "").strip()[:200],
                        "session_id": sdir.name,
                        "backfilled": True,
                    }
                )
            )

    reports = design / "reports"
    if reports.is_dir():
        for name, kind in REPORT_FILES.items():
            for path in sorted(reports.rglob(name)):
                if any(_PT_DIR.match(part) for part in path.relative_to(reports).parts):
                    continue
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    status, summary = SUMMARIZERS[kind](data)
                except Exception:  # noqa: BLE001
                    continue
                at = _parse_iso(data.get("ran_at") or data.get("generated_at")) or datetime.fromtimestamp(
                    path.stat().st_mtime, timezone.utc
                )
                rel = _rel(design, path)
                at_s = at.isoformat(timespec="seconds")
                rid = f"{kind}-{_ts(at)}_{hashlib.sha256(rel.encode()).hexdigest()[:4]}"
                if rid in known_ids or (kind, rel, at_s) in known_art:
                    continue
                rec = {
                    "id": rid,
                    "kind": kind,
                    "at": at_s,
                    "status": status,
                    "summary": summary,
                    "artifacts": [rel],
                    "backfilled": True,
                }
                if CANONICAL_ARTIFACTS.get(kind) and CANONICAL_ARTIFACTS[kind] != rel:
                    rec["scope"] = "alt"
                new.append(_clean(rec))

    new.sort(key=lambda r: r["at"])
    if new and not dry_run and not disabled():
        with (design / "models" / INDEX_FILE).open("a", encoding="utf-8") as fh:
            for rec in new:
                fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        render_index(design)
    return {
        "design": str(design),
        "added": len(new),
        "by_kind": dict(Counter(r["kind"] for r in new)),
        "dry_run": dry_run,
    }


# --- render -----------------------------------------------------------------

_STATUS_TEXT = {
    "pass": "通过",
    "fail": "失败",
    "unchecked": "未判定",
    "done": "完成",
    "ok": "正常",
    "empty": "有空通道",
    "suspect": "数据可疑",
    "error": "出错",
}


def _local(at: str | None) -> str:
    dt = _parse_iso(at)
    return dt.astimezone().strftime("%Y-%m-%d %H:%M") if dt else "?"


def _cell(text: Any) -> str:
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ")


def render_index(design_dir: Path | str) -> Path | None:
    design = Path(design_dir).resolve()
    records = load_records(design)
    if not records:
        return None
    status = design_status(design)
    lines = [
        "# benchgate 记录目录",
        "",
        f"> 由 benchgate 自动生成，请勿手改。源数据：`models/{INDEX_FILE}`，共 {len(records)} 条。"
        f"生成于 {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M')}。",
        "",
        "## 当前状态",
        "",
    ]
    if not any(rec.get("kind") in ("sim_run", "gate") for rec in records):
        lines.append("没有仿真或 gate 记录。")
    elif status["problems"]:
        lines.append("**尚未验证：**")
        lines.extend(f"- {p}" for p in status["problems"])
    else:
        lines.append("当前设计输入已有通过的 gate 结论。")
    if (design / "models" / "manifest.yaml").is_file() and status["sim_profiles"]:
        if status["sim_profiles"].startswith("$BENCHGATE_HOME"):
            lines += ["", f"仿真配置：`{status['sim_profiles']}`（本机共用文件，不在这个项目的仓库里）。"]
        else:
            lines += ["", f"仿真配置：`{status['sim_profiles']}`（随项目仓库）。"]
    lines += ["", "| 类型 | 最近一次 | 结论 | 输入 | 摘要 | 记录 |", "|---|---|---|---|---|---|"]
    for kind, info in status["kinds"].items():
        if info["stale"] is None:
            freshness_text = "—" if kind == "session" else "未知（回填）"
        elif info["stale"]:
            freshness_text = "已过期"
        else:
            freshness_text = "当前"
        lines.append(
            f"| {KIND_LABELS[kind]} | {_local(info['at'])} | {_STATUS_TEXT.get(info['status'], info['status'])} "
            f"| {freshness_text} | {_cell(info['summary'])} | `{info['id']}` |"
        )

    lines += ["", f"## 记录（最新在前，最多 {_RENDER_LIMIT} 条）", ""]
    current_day = None
    for rec in sorted(records, key=lambda r: r.get("at") or "", reverse=True)[:_RENDER_LIMIT]:
        day = _local(rec.get("at"))[:10]
        if day != current_day:
            current_day = day
            lines += ["", f"### {day}", "", "| 时间 | 类型 | 结论 | 摘要 | 目的 | 记录 |", "|---|---|---|---|---|---|"]
        marks = []
        if rec.get("backfilled"):
            marks.append("回填")
        if rec.get("scope") == "alt":
            marks.append("非默认输出")
        if rec.get("via") == "hook":
            marks.append("自动")
        kind_text = KIND_LABELS.get(rec.get("kind"), rec.get("kind")) + (f"（{'、'.join(marks)}）" if marks else "")
        lines.append(
            f"| {_local(rec.get('at'))[11:]} | {kind_text} | {_STATUS_TEXT.get(rec.get('status'), rec.get('status'))} "
            f"| {_cell(summary_line(rec))} | {_cell(rec.get('purpose'))} | `{rec.get('id')}` |"
        )
    path = design / "models" / INDEX_MD
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
