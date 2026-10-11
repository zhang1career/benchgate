"""Repos without simulation (hooks --no-sim) and importing outside measurements."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from benchgate import hooks, ledger
from benchgate.cli import main
from benchgate.instruments.types import QuantityKind, Waveform
from benchgate.lab.importer import LabImportError, import_session, parse_value, quantity_for_unit, read_waveform_csv
from benchgate.lab.store import LabDataStore


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("BENCHGATE_HOME", str(tmp_path / "bg_home"))
    monkeypatch.delenv("BENCHGATE_NO_INDEX", raising=False)
    monkeypatch.delenv("BENCHGATE_NO_HOOKS", raising=False)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def _kicad_only(root: Path, name: str = "pcb") -> Path:
    design = root / name
    design.mkdir(parents=True)
    (design / "board.kicad_pro").write_text("{}", encoding="utf-8")
    (design / "board.kicad_sch").write_text("(kicad_sch rev1)", encoding="utf-8")
    return design


def _wave(n: int) -> Waveform:
    t = np.linspace(0, 1e-3, n)
    return Waveform(t, np.sin(t), 1, datetime.now(timezone.utc), sample_rate_hz=1e6 if n else None)


def test_install_no_sim_writes_policy_into_hook_commands(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _kicad_only(repo)
    result = hooks.install(repo, sim=False)
    assert result["designs"] == [] and "note" in result
    config = json.loads((repo / ".cursor" / "hooks.json").read_text(encoding="utf-8"))
    assert config["hooks"]["stop"][0]["command"].endswith("hook stop --no-sim")
    assert (repo / ".git" / "hooks" / "pre-commit").read_text(encoding="utf-8").rstrip().endswith(
        "index check --staged --no-gate"
    )
    assert not list(repo.rglob("benchgate.yaml"))


def test_designs_without_manifest_are_found_by_records_and_never_gated(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    design = _kicad_only(repo)
    bench = repo / "bench"
    with ledger.record_context(purpose="x"):
        LabDataStore(design / "models" / "captured").write_session(waveforms={"scope_ch1": _wave(10)})
        LabDataStore(bench / "models" / "captured").write_session(waveforms={"scope_ch1": _wave(10)})
    assert ledger.discover_designs(repo) == [bench.resolve(), design.resolve()]

    _git(repo, "add", "pcb/board.kicad_sch")
    assert hooks.staged_designs(repo) == []
    ok, text = hooks.check_designs([design], root=repo)
    assert ok and "不做 gate 检查" in text
    index = (design / "models" / "INDEX.md").read_text(encoding="utf-8")
    assert "没有仿真或 gate 记录" in index
    assert "尚未验证" not in index


def test_stop_hook_no_sim_checks_new_sessions_once(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    design = _kicad_only(repo)
    from benchgate.io.manifest import save_manifest
    from benchgate.schemas import MappingManifest

    save_manifest(MappingManifest(), design / "models" / "manifest.yaml")

    def no_sim(*_a, **_k):
        raise AssertionError("--no-sim must not simulate")

    monkeypatch.setattr("benchgate.watch.trigger.watch_once", no_sim)
    payload = {"status": "completed", "workspace_roots": [str(repo)]}
    assert hooks.run_stop_hook(payload, sim=False) == {}

    store = LabDataStore(design / "models" / "captured")
    store.write_session(waveforms={"scope_ch1": _wave(0)})
    with ledger.record_context(purpose="测输出纹波"):
        store.write_session(waveforms={"scope_ch1": _wave(50)})
    (design / "board.kicad_sch").write_text("(kicad_sch rev2)", encoding="utf-8")

    out = hooks.run_stop_hook(payload, sim=False)
    message = out["followup_message"]
    assert "有空通道" in message
    assert "没有写目的" in message
    assert "还没有加入 git" in message
    assert "不要自行操作仪器" in message
    assert hooks.run_stop_hook(payload, sim=False) == {}


def test_precommit_points_out_ledger_left_out(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    design = _kicad_only(repo)
    with ledger.record_context(purpose="x"):
        LabDataStore(design / "models" / "captured").write_session(waveforms={"scope_ch1": _wave(10)})
    left = hooks.ledger_left_out(repo)
    assert any("index.jsonl（未加入 git）" in item for item in left)
    _git(repo, "add", "pcb/models/index.jsonl", "pcb/models/INDEX.md")
    assert hooks.ledger_left_out(repo) == []


def test_cli_index_check_no_gate_never_blocks(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    design = _kicad_only(repo)
    from benchgate.io.manifest import save_manifest
    from benchgate.schemas import MappingManifest

    save_manifest(MappingManifest(), design / "models" / "manifest.yaml")
    _git(repo, "add", "pcb/board.kicad_sch")
    monkeypatch.chdir(repo)
    assert main(["index", "check", "--staged"]) == 1
    assert main(["index", "check", "--staged", "--no-gate"]) == 0


def test_parse_value_and_units():
    v = parse_value("iin=85mA")
    assert (v.name, v.value, v.unit) == ("iin", 85.0, "mA")
    assert parse_value("gain = -3.5e1").unit == ""
    assert quantity_for_unit("mV") is QuantityKind.VOLTAGE
    assert quantity_for_unit("kHz") is QuantityKind.FREQUENCY
    assert quantity_for_unit("dB") is QuantityKind.UNKNOWN
    with pytest.raises(LabImportError):
        parse_value("vout 23.6V")


def test_read_csv_with_vendor_header(tmp_path):
    csv_path = tmp_path / "scope.csv"
    csv_path.write_text(
        "Model,DS1054Z\nTime (s),CH1 (V),CH2 (V)\n0,0.1,1.0\n1e-6,0.2,1.1\n2e-6,0.3,1.2\n,,\n",
        encoding="utf-8",
    )
    names, t, values = read_waveform_csv(csv_path)
    assert names == ["CH1 (V)", "CH2 (V)"]
    assert t.tolist() == [0.0, 1e-6, 2e-6]
    assert values.shape == (3, 2)

    names, t, _ = read_waveform_csv(csv_path, sample_interval_s=5e-7)
    assert t.tolist() == [0.0, 5e-7, 1e-6]


def test_import_session_writes_ledger(tmp_path):
    design = _kicad_only(tmp_path)
    csv_path = tmp_path / "ripple.csv"
    csv_path.write_text("t,v\n0,1\n0.001,2\n0.002,3\n", encoding="utf-8")
    shot = tmp_path / "screen.png"
    shot.write_bytes(b"\x89PNG")

    with ledger.record_context(purpose="12V 输入时的输出"):
        result = import_session(
            design / "models" / "captured",
            design=str(design),
            waveforms=[csv_path],
            values=["vout=23.6V", "iin=85mA"],
            files=[shot],
            instrument="DS1054Z U 盘导出",
            captured_at="2026-10-01T09:30:00",
        )
    assert result["channels"] == ["ripple_v", "vout", "iin"]
    assert result["derived"] == {"vout": 23.6, "iin": 85.0}
    assert result["attachments"] == ["attachments/ripple.csv", "attachments/screen.png"]
    assert (Path(result["path"]) / "attachments" / "screen.png").is_file()

    rec = ledger.load_records(design)[-1]
    assert rec["kind"] == "session" and rec["status"] == "ok"
    assert rec["purpose"] == "12V 输入时的输出"
    assert "import" in rec["tags"]
    assert rec["summary"]["instruments"] == ["import"]
    assert rec["at"].startswith("2026-10-01")
    assert "附件 2" in ledger.summary_line(rec)


def test_import_needs_something(tmp_path):
    with pytest.raises(LabImportError):
        import_session(tmp_path / "models" / "captured")


def test_cli_import(tmp_path, capsys):
    design = _kicad_only(tmp_path)
    code = main(
        [
            "lab", "import", "--design", str(design),
            "--value", "vout=5.02V", "--purpose", "空载输出",
        ]
    )
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["derived"] == {"vout": 5.02}
    assert ledger.load_records(design)[-1]["purpose"] == "空载输出"

    assert main(["lab", "import", "--design", str(design), "--value", "bad"]) == 2
