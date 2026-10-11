"""Make benchgate run without anyone remembering to call it.

Two triggers, both read the record ledger (``models/index.jsonl``):

* **Cursor ``stop`` hook** (``benchgate hook stop``): when an agent turn ends and a
  design with a manifest has inputs that differ from its last gate, run
  mapping → sim → gate (no bench capture, no tolerance study). Every design also
  has the Sessions added during the turn checked for empty data and missing
  purpose. Findings go back to the agent as a follow-up.
* **Git pre-commit** (``benchgate index check --staged``): refuse a commit that
  changes design inputs while the gate is missing, stale or failing, and point
  out ledger files left out of the commit.

Which checks run is the repository's choice, made with ``hooks install`` options
and stored in the hook commands themselves (``--no-sim``, ``--no-gate``).

``benchgate hooks install`` wires both into one repository.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

from benchgate import ledger

STOP_HOOK_PURPOSE = "设计输入变化后自动复核（Cursor 回合结束）"
PRECOMMIT_MARKER = "# benchgate-pre-commit"
STOP_TIMEOUT_S = 900
STOP_LOOP_LIMIT = 2


# --- stop hook --------------------------------------------------------------


def _review_state_path(design: Path) -> Path:
    from benchgate.paths import benchgate_paths

    return benchgate_paths(design).state.with_suffix(".review.json")


def _load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _update_state(path: Path, **values: Any) -> None:
    state = _load_state(path)
    state.update(values)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def _save_state(path: Path, digest: str, files: dict[str, str]) -> None:
    _update_state(path, inputs=digest, input_files=files)


def review_profile(records: list[dict], override: str | None = None) -> str:
    """Profile for an automatic review: explicit override, else the one last used on this design."""
    if override:
        return override
    for kind in ("sim_run", "gate"):
        rec = ledger.latest(records, kind)
        if rec and rec.get("profile"):
            return str(rec["profile"])
    return "default"


def review_design(design: Path, *, root: Path | None = None, profile: str | None = None) -> dict[str, Any]:
    """Run sim + gate for ``design`` if its inputs moved since the last gate.

    The first time a design is seen with no gate on record, only a baseline is
    stored: opening a chat in an old repository must not start ngspice.
    """
    from benchgate.paths import benchgate_paths
    from benchgate.watch.trigger import watch_once

    design = Path(design).resolve()
    label = _label(design, root)
    state_path = _review_state_path(design)
    digest, files = ledger.current_inputs(design)
    records = ledger.load_records(design)
    gate = ledger.latest(records, "gate", with_inputs=True)
    if gate is not None:
        ref_digest, ref_files = gate["inputs"], gate.get("input_files")
    else:
        state = _load_state(state_path)
        ref_digest, ref_files = state.get("inputs"), state.get("input_files")
    if ref_digest is None:
        _save_state(state_path, digest, files)
        return {"design": label, "skipped": "baseline"}
    if ref_digest == digest:
        return {"design": label, "skipped": "unchanged"}

    changed = ledger.changed_inputs(ref_files, files)
    p = benchgate_paths(design)
    profile = review_profile(records, profile)
    try:
        with ledger.record_context(via="hook", purpose=STOP_HOOK_PURPOSE):
            watch_once(
                p.design,
                manifest_path=p.manifest,
                models_dir=p.models,
                reports_dir=p.reports,
                state_path=p.state,
                sim_profile_path=p.sim_profile if p.sim_profile.is_file() else None,
                profile=profile,
                subckt_dir=p.subckt,
                global_models_dir=p.global_models,
                blocks_yaml=p.blocks_yaml,
                tmp_dir=p.tmp_root / "pipeline",
                run_pipeline=any(c.startswith("models/blocks") for c in changed),
                run_auto_capture=False,
                run_tolerance=False,
                force=True,
            )
    except Exception as exc:  # noqa: BLE001 — report, never crash the hook
        _save_state(state_path, digest, files)
        return {
            "design": label,
            "changed": changed,
            "message": f"benchgate 自动复核 {label} 时出错：{exc}。设计输入改过（{_short(changed)}），但没有得到新的仿真/gate 结论。",
        }

    digest, files = ledger.current_inputs(design)
    _save_state(state_path, digest, files)
    problems = ledger.verification_problems(design)
    result: dict[str, Any] = {"design": label, "changed": changed, "problems": problems, "profile": profile}
    if problems:
        lines = [f"benchgate 自动复核 {label}（profile {profile}）：设计输入改过（{_short(changed)}），复核未通过："]
        lines += [f"- {p}" for p in problems]
        lines.append(
            f"请先处理这些问题，或向用户说明为什么可以接受；不要直接宣称任务完成。记录见 {label}/models/INDEX.md。"
        )
        result["message"] = "\n".join(lines)
    return result


def _label(design: Path, root: Path | None) -> str:
    if root is not None:
        try:
            rel = design.relative_to(Path(root).resolve()).as_posix()
            return rel or "."
        except ValueError:
            pass
    return str(design)


def _short(changed: list[str], n: int = 4) -> str:
    if not changed:
        return "未知文件"
    text = ", ".join(changed[:n])
    return text + (f" 等 {len(changed)} 个文件" if len(changed) > n else "")


def _ledger_git_state(design: Path) -> str | None:
    """``untracked`` when models/index.jsonl exists but is not in git, ``ignored`` when gitignored."""
    path = design / "models" / ledger.INDEX_FILE
    if not path.is_file():
        return None
    try:
        tracked = subprocess.run(
            ["git", "-C", str(design), "ls-files", "--error-unmatch", str(path)],
            capture_output=True,
            text=True,
        )
        if tracked.returncode == 0:
            return None
        ignored = subprocess.run(
            ["git", "-C", str(design), "check-ignore", "-q", str(path)],
            capture_output=True,
            text=True,
        )
        inside = subprocess.run(
            ["git", "-C", str(design), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if inside.returncode != 0:
        return None
    return "ignored" if ignored.returncode == 0 else "untracked"


def review_records(design: Path, *, root: Path | None = None) -> dict[str, Any]:
    """Look at the Sessions added since the last turn: empty data, suspect data, no purpose.

    The first look at a design only remembers how many records exist, so old
    history is not reported. Each record is reported once.
    """
    design = Path(design).resolve()
    label = _label(design, root)
    state_path = _review_state_path(design)
    state = _load_state(state_path)
    records = ledger.load_records(design)
    seen = state.get("records_seen")
    new = records[seen:] if isinstance(seen, int) and seen <= len(records) else []
    updates: dict[str, Any] = {"records_seen": len(records)}

    sessions = [r for r in new if r.get("kind") == "session" and not r.get("backfilled")]
    bad = [r for r in sessions if r.get("status") in ("empty", "suspect")]
    unexplained = [r for r in sessions if not r.get("purpose")]
    lines: list[str] = []
    if bad:
        lines.append(f"本轮新增的实验记录里有 {len(bad)} 条数据可能有问题：")
        for rec in bad:
            text = "有空通道" if rec.get("status") == "empty" else "采样率异常"
            lines.append(f"- {rec['id']}：{text}（{ledger.summary_line(rec)}）")
    if unexplained:
        ids = ", ".join(r["id"] for r in unexplained[:6]) + (" 等" if len(unexplained) > 6 else "")
        lines.append(f"这些记录没有写目的：{ids}。之后采集或导入时请带上 purpose（CLI 用 --purpose）。")
    if not state.get("ledger_git_noted"):
        git_state = _ledger_git_state(design)
        if git_state:
            updates["ledger_git_noted"] = True
            where = f"{label}/models/{ledger.INDEX_FILE}"
            if git_state == "ignored":
                lines.append(f"{where} 被 .gitignore 忽略，实验记录不会随仓库保存。")
            else:
                lines.append(f"{where} 还没有加入 git。")
    _update_state(state_path, **updates)
    result: dict[str, Any] = {"design": label, "new_sessions": len(sessions)}
    if lines:
        lines.insert(0, f"benchgate 实验记录检查 {label}：")
        lines.append(
            "请把这些情况告诉用户。需要重新测量时先征得用户同意，不要自行操作仪器；"
            f"不要替用户提交。记录见 {label}/models/INDEX.md。"
        )
        result["message"] = "\n".join(lines)
    return result


def has_manifest(design: Path) -> bool:
    return (Path(design) / "models" / "manifest.yaml").is_file()


def run_stop_hook(payload: dict[str, Any], *, profile: str | None = None, sim: bool = True) -> dict[str, Any]:
    if payload.get("status") not in (None, "completed"):
        return {}
    roots = [Path(r) for r in (payload.get("workspace_roots") or [])] or [Path.cwd()]
    messages = []
    for root in roots:
        for design in ledger.discover_designs(root):
            if sim and has_manifest(design):
                res = review_design(design, root=root, profile=profile)
                if res.get("message"):
                    messages.append(res["message"])
            rec = review_records(design, root=root)
            if rec.get("message"):
                messages.append(rec["message"])
    return {"followup_message": "\n\n".join(messages)} if messages else {}


# --- pre-commit check -------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout


def repo_root(path: Path) -> Path:
    return Path(_git(path, "rev-parse", "--show-toplevel").strip()).resolve()


def staged_designs(repo: Path) -> list[Path]:
    """Designs with a manifest whose sim/gate inputs are touched by the staged change."""
    repo = repo_root(repo)
    staged = [repo / line for line in _git(repo, "diff", "--cached", "--name-only", "--diff-filter=ACMRD").splitlines() if line]
    affected = []
    for design in ledger.discover_designs(repo):
        if not has_manifest(design):
            continue
        inputs = {p.resolve() for p in ledger.input_paths(design).values()}
        for path in staged:
            try:
                rel = path.resolve().relative_to(design).as_posix()
            except ValueError:
                continue
            if path.resolve() in inputs or rel.endswith(".kicad_sch") or rel in ("models/manifest.yaml", "models/blocks.yaml"):
                affected.append(design)
                break
    return affected


def ledger_left_out(repo: Path) -> list[str]:
    """Ledger files that changed but are not part of the commit (untracked or unstaged)."""
    repo = repo_root(repo)
    paths = []
    for design in ledger.discover_designs(repo):
        for name in (ledger.INDEX_FILE, ledger.INDEX_MD):
            path = design / "models" / name
            if path.is_file():
                paths.append(path.relative_to(repo).as_posix())
    if not paths:
        return []
    out = []
    for line in _git(repo, "status", "--porcelain", "--", *paths).splitlines():
        code, rel = line[:2], line[3:]
        if code == "??":
            out.append(f"{rel}（未加入 git）")
        elif code[1] == "M":
            out.append(f"{rel}（有改动未暂存）")
    return out


def check_designs(designs: list[Path], *, root: Path | None = None) -> tuple[bool, str]:
    lines: list[str] = []
    ok = True
    for design in designs:
        label = _label(design, root)
        if not has_manifest(design):
            lines.append(f"{label}：没有 models/manifest.yaml，不做 gate 检查。")
            continue
        problems = ledger.verification_problems(design)
        if problems:
            ok = False
            records = ledger.load_records(design)
            cmd = f"benchgate watch once --design {label} --profile {review_profile(records)} --no-auto-capture --force"
            lines.append(f"{label}：")
            lines += [f"  - {p}" for p in problems]
            if problems[0].startswith(("gate 已过期", "没有")):
                lines.append(f"  先运行：{cmd}")
            else:
                lines.append(f"  修复后重新运行：{cmd}")
        else:
            lines.append(f"{label}：gate 是当前设计的通过结论。")
    return ok, "\n".join(lines)


# --- install ----------------------------------------------------------------


def _benchgate_command() -> str:
    exe = shutil.which("benchgate")
    if exe:
        return str(Path(exe).resolve())
    return f"{sys.executable} -m benchgate.cli"


def _is_ours(entry: dict) -> bool:
    return "hook stop" in str(entry.get("command", "")) and "benchgate" in str(entry.get("command", ""))


def install(
    repo: Path,
    *,
    cursor: bool = True,
    git: bool = True,
    profile: str | None = None,
    sim: bool = True,
) -> dict[str, Any]:
    """Install both hooks. ``sim=False`` writes ``--no-sim`` / ``--no-gate`` into the hook commands."""
    repo = repo_root(repo)
    command = _benchgate_command()
    designs = ledger.discover_designs(repo)
    out: dict[str, Any] = {
        "repo": str(repo),
        "sim": sim,
        "designs": [_label(d, repo) for d in designs],
    }
    if not designs:
        out["note"] = "目前没有设计（models/ 下没有 manifest、台账或采集）；第一次采集或导入后，hook 会自动纳入。"

    if cursor:
        hooks_path = repo / ".cursor" / "hooks.json"
        config = json.loads(hooks_path.read_text(encoding="utf-8")) if hooks_path.is_file() else {}
        config.setdefault("version", 1)
        hooks = config.setdefault("hooks", {})
        stop = [e for e in hooks.get("stop", []) if not _is_ours(e)]
        stop_cmd = f"{command} hook stop" + (f" --profile {profile}" if profile else "") + ("" if sim else " --no-sim")
        stop.append({"command": stop_cmd, "timeout": STOP_TIMEOUT_S, "loop_limit": STOP_LOOP_LIMIT})
        hooks["stop"] = stop
        hooks_path.parent.mkdir(parents=True, exist_ok=True)
        hooks_path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        out["cursor_hook"] = str(hooks_path)

    if git:
        hooks_dir = Path(_git(repo, "rev-parse", "--git-path", "hooks").strip())
        if not hooks_dir.is_absolute():
            hooks_dir = repo / hooks_dir
        hooks_dir.mkdir(parents=True, exist_ok=True)
        pre = hooks_dir / "pre-commit"
        prev = hooks_dir / "pre-commit.benchgate-prev"
        if pre.is_file() and PRECOMMIT_MARKER not in pre.read_text(encoding="utf-8", errors="replace"):
            pre.replace(prev)
        pre.write_text(
            "#!/bin/sh\n"
            f"{PRECOMMIT_MARKER}\n"
            'prev="$(dirname "$0")/pre-commit.benchgate-prev"\n'
            'if [ -x "$prev" ]; then "$prev" "$@" || exit $?; fi\n'
            f"exec {command} index check --staged{'' if sim else ' --no-gate'}\n",
            encoding="utf-8",
        )
        pre.chmod(pre.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        out["git_hook"] = str(pre)
    return out


def uninstall(repo: Path) -> dict[str, Any]:
    repo = repo_root(repo)
    out: dict[str, Any] = {"repo": str(repo)}
    hooks_path = repo / ".cursor" / "hooks.json"
    if hooks_path.is_file():
        config = json.loads(hooks_path.read_text(encoding="utf-8"))
        stop = [e for e in config.get("hooks", {}).get("stop", []) if not _is_ours(e)]
        if stop:
            config["hooks"]["stop"] = stop
        else:
            config.get("hooks", {}).pop("stop", None)
        hooks_path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        out["cursor_hook"] = "removed"
    hooks_dir = Path(_git(repo, "rev-parse", "--git-path", "hooks").strip())
    if not hooks_dir.is_absolute():
        hooks_dir = repo / hooks_dir
    pre, prev = hooks_dir / "pre-commit", hooks_dir / "pre-commit.benchgate-prev"
    if pre.is_file() and PRECOMMIT_MARKER in pre.read_text(encoding="utf-8", errors="replace"):
        pre.unlink()
        if prev.is_file():
            prev.replace(pre)
        out["git_hook"] = "removed"
    return out


def read_hook_payload() -> dict[str, Any]:
    if sys.stdin is None or sys.stdin.isatty():
        return {}
    text = sys.stdin.read()
    try:
        return json.loads(text) if text.strip() else {}
    except json.JSONDecodeError:
        return {}


def env_disabled() -> bool:
    return os.environ.get("BENCHGATE_NO_HOOKS", "").strip().lower() in {"1", "true", "yes"}
