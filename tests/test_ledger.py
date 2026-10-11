"""Record ledger (models/index.jsonl), staleness, backfill, and the automatic triggers."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from benchgate import hooks, ledger
from benchgate.gate.report import write_gate_report
from benchgate.instruments.types import Waveform
from benchgate.io.manifest import save_manifest
from benchgate.lab.store import LabDataStore
from benchgate.schemas import MappingManifest


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("BENCHGATE_HOME", str(tmp_path / "bg_home"))
    monkeypatch.delenv("BENCHGATE_NO_INDEX", raising=False)
    monkeypatch.delenv("BENCHGATE_NO_HOOKS", raising=False)


def _design(root: Path, name: str = "pcb") -> Path:
    design = root / name
    (design / "models").mkdir(parents=True)
    (design / "board.kicad_pro").write_text("{}", encoding="utf-8")
    (design / "board.kicad_sch").write_text("(kicad_sch rev1)", encoding="utf-8")
    save_manifest(MappingManifest(), design / "models" / "manifest.yaml")
    return design


def _wave(n: int = 50) -> Waveform:
    t = np.linspace(0, 1e-3, n)
    return Waveform(t, np.sin(t), 1, datetime.now(timezone.utc))


def _gate_payload(*, spec_failures: int = 0, checked: bool = True) -> dict:
    entries = [{"spec_status": "fail" if spec_failures else ("pass" if checked else "n/a")}]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {"spec_failures": spec_failures, "with_sim": 1, "with_bench": 0, "compared": 0, "rules": None},
        "entries": entries,
    }


def _record_gate(design: Path, **kw) -> dict:
    return ledger.record_report(design, "gate", _gate_payload(**kw), design / "reports" / "gate_report.json")


def test_session_indexed_only_under_design_models(tmp_path):
    design = _design(tmp_path)
    LabDataStore(design / "models" / "captured").write_session(
        component_ref="C1", waveforms={"scope_ch1": _wave(), "scope_ch2": _wave(0)}, notes="ringing check"
    )
    LabDataStore(tmp_path / "captured").write_session(waveforms={"scope_ch1": _wave()})

    records = ledger.load_records(design)
    assert len(records) == 1
    rec = records[0]
    assert rec["kind"] == "session"
    assert rec["status"] == "empty"
    assert rec["summary"]["empty_channels"] == ["scope_ch2"]
    assert rec["purpose"] == "ringing check"
    assert not (tmp_path / "index.jsonl").exists()
    assert (design / "models" / "INDEX.md").is_file()


def test_gate_goes_stale_when_schematic_changes(tmp_path):
    design = _design(tmp_path)
    _record_gate(design)
    assert ledger.verification_problems(design) == []

    (design / "board.kicad_sch").write_text("(kicad_sch rev2)", encoding="utf-8")
    problems = ledger.verification_problems(design)
    assert len(problems) == 1
    assert "已过期" in problems[0] and "board.kicad_sch" in problems[0]


def test_gate_verdicts():
    assert ledger.summarize_gate(_gate_payload(spec_failures=2))[0] == "fail"
    assert ledger.summarize_gate(_gate_payload())[0] == "pass"
    assert ledger.summarize_gate(_gate_payload(checked=False))[0] == "unchecked"
    rules_fail = _gate_payload()
    rules_fail["summary"]["rules"] = {"passed": False, "failures": 1, "results": [{}]}
    assert ledger.summarize_gate(rules_fail)[0] == "fail"


def test_failed_gate_and_sim_are_problems(tmp_path):
    design = _design(tmp_path)
    ledger.record_report(
        design,
        "sim_run",
        {"success": False, "checks": {"checks": [{"signal": "v(a)", "metric": "avg", "passed": False}]}},
        design / "reports" / "sim" / "sim_report.json",
    )
    _record_gate(design, spec_failures=1)
    problems = ledger.verification_problems(design)
    assert any(p.startswith("gate 失败") for p in problems)
    assert any(p.startswith("仿真失败") for p in problems)


def test_write_gate_report_records_scope_and_parents(tmp_path):
    design = _design(tmp_path)
    sim_report = design / "reports" / "sim" / "sim_report.json"
    sim_report.parent.mkdir(parents=True)
    sim_report.write_text(json.dumps({"success": True}), encoding="utf-8")
    sim_rec = ledger.record_report(design, "sim_run", {"success": True}, sim_report)

    write_gate_report(
        design / "models" / "manifest.yaml",
        design / "reports" / "gate_report.json",
        captured_dir=design / "models" / "captured",
        sim_report_path=sim_report,
        design_dir=design,
    )
    scratch = design / "reports" / "scratch" / "manifest.yaml"
    save_manifest(MappingManifest(), scratch)
    write_gate_report(scratch, scratch.parent / "gate_report.json", captured_dir=design / "models" / "captured", design_dir=design)

    gates = [r for r in ledger.load_records(design) if r["kind"] == "gate"]
    assert gates[0]["parents"] == [sim_rec["id"]]
    assert "scope" not in gates[0]
    assert gates[1]["scope"] == "alt"
    assert ledger.latest(ledger.load_records(design), "gate")["id"] == gates[0]["id"]


def test_record_context_attaches_purpose(tmp_path):
    design = _design(tmp_path)
    with ledger.record_context(purpose="recheck after R51 change", via="mcp"):
        rec = _record_gate(design)
    assert rec["purpose"] == "recheck after R51 change"
    assert rec["via"] == "mcp"


def test_dispatch_strips_record_context_args(tmp_path):
    from benchgate.agent.dispatch import dispatch

    design = _design(tmp_path)
    _record_gate(design)
    out = dispatch("index_query", {"design_dir": str(design), "kind": "gate", "purpose": "look", "chat_id": "c1"})
    assert len(out["records"]) == 1
    assert "input_files" not in out["records"][0]
    status = dispatch("index_status", {"design_dir": str(design)})
    assert status["problems"] == []


def test_rebuild_backfills_once(tmp_path, monkeypatch):
    design = _design(tmp_path)
    monkeypatch.setenv("BENCHGATE_NO_INDEX", "1")
    LabDataStore(design / "models" / "captured").write_session(waveforms={"scope_ch1": _wave()})
    monkeypatch.delenv("BENCHGATE_NO_INDEX")
    gate = design / "reports" / "gate_report.json"
    gate.parent.mkdir(parents=True, exist_ok=True)
    gate.write_text(json.dumps(_gate_payload()), encoding="utf-8")
    pt = design / "reports" / "stress_sweep" / "pt000" / "stress_sweep_report.json"
    pt.parent.mkdir(parents=True)
    pt.write_text(json.dumps({"worst": {"passed": True}}), encoding="utf-8")

    first = ledger.rebuild(design)
    assert first["by_kind"] == {"session": 1, "gate": 1}
    assert ledger.rebuild(design)["added"] == 0
    assert all(r["backfilled"] for r in ledger.load_records(design))
    assert "没有带输入记录的 gate" in ledger.verification_problems(design)[0]
    text = (design / "models" / "INDEX.md").read_text(encoding="utf-8")
    assert "未知（回填）" in text and "## 当前状态" in text


def test_review_design_baseline_then_runs_on_change(tmp_path, monkeypatch):
    design = _design(tmp_path)
    calls = []

    def fake_watch_once(design_dir, **kw):
        calls.append(kw)
        with ledger.record_context(via="hook"):
            _record_gate(Path(design_dir), spec_failures=1)
        return {}

    monkeypatch.setattr("benchgate.watch.trigger.watch_once", fake_watch_once)

    assert hooks.review_design(design)["skipped"] == "baseline"
    assert hooks.review_design(design)["skipped"] == "unchanged"
    assert not calls

    (design / "board.kicad_sch").write_text("(kicad_sch rev2)", encoding="utf-8")
    res = hooks.review_design(design, root=tmp_path)
    assert calls and calls[0]["run_auto_capture"] is False and calls[0]["run_tolerance"] is False
    assert res["changed"] == ["board.kicad_sch"]
    assert "gate 失败" in res["message"]
    assert hooks.review_design(design)["skipped"] == "unchanged"

    out = hooks.run_stop_hook({"status": "completed", "workspace_roots": [str(tmp_path)]})
    assert out == {}
    assert hooks.run_stop_hook({"status": "aborted", "workspace_roots": [str(tmp_path)]}) == {}
    assert calls[0]["profile"] == "default"


def test_review_reuses_last_profile(tmp_path):
    design = _design(tmp_path)
    ledger.record_report(
        design, "sim_run", {"success": True}, design / "reports" / "sim" / "sim_report.json", extra={"profile": "charge_pump"}
    )
    records = ledger.load_records(design)
    assert hooks.review_profile(records) == "charge_pump"
    assert hooks.review_profile(records, "hbridge_pwm") == "hbridge_pwm"
    assert hooks.review_profile([]) == "default"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_precommit_and_install(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    design = _design(repo)
    (repo / "README.md").write_text("x", encoding="utf-8")

    _git(repo, "add", "README.md")
    assert hooks.staged_designs(repo) == []

    _git(repo, "add", "pcb/board.kicad_sch")
    assert hooks.staged_designs(repo) == [design]
    ok, text = hooks.check_designs([design], root=repo)
    assert not ok and "watch once --design pcb" in text

    _record_gate(design)
    assert hooks.check_designs([design], root=repo)[0]

    pre = repo / ".git" / "hooks" / "pre-commit"
    pre.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    pre.chmod(0o755)
    result = hooks.install(repo)
    assert result["designs"] == ["pcb"] and result["sim"] is True
    config = json.loads((repo / ".cursor" / "hooks.json").read_text(encoding="utf-8"))
    assert config["hooks"]["stop"][0]["command"].endswith("hook stop")
    assert hooks.PRECOMMIT_MARKER in pre.read_text(encoding="utf-8")
    assert (pre.parent / "pre-commit.benchgate-prev").is_file()

    hooks.install(repo)
    config = json.loads((repo / ".cursor" / "hooks.json").read_text(encoding="utf-8"))
    assert len(config["hooks"]["stop"]) == 1

    hooks.uninstall(repo)
    assert pre.read_text(encoding="utf-8") == "#!/bin/sh\nexit 0\n"
    config = json.loads((repo / ".cursor" / "hooks.json").read_text(encoding="utf-8"))
    assert "stop" not in config["hooks"]


def test_ledger_write_failure_never_raises(tmp_path, monkeypatch):
    design = _design(tmp_path)
    monkeypatch.setattr(ledger, "current_inputs", lambda d: (_ for _ in ()).throw(OSError("disk")))
    assert _record_gate(design) is None
