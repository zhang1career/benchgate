"""sim profile name list / copy / rename / remove."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchgate.cli import main
from benchgate.sim.profile_catalog import (
    ProfileCatalogError,
    add_profile,
    list_profiles,
    remove_profile,
    rename_profile,
    render_profiles,
    resolve_catalog,
    shared_catalog_path,
    show_profile,
    split_profiles,
    update_hook_profile,
)

SAMPLE = """\
# shared

default:
  description: Background transient
  directives:
    - ".control"
    - "tran 1u 10m"
    - ".endc"

# pump note
charge_pump:
  description: NE555 charge pump
  checks:
    - signal: v(vout)
      gte: 8
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("BENCHGATE_HOME", str(tmp_path / "bg_home"))
    path = shared_catalog_path()
    path.parent.mkdir(parents=True)
    path.write_text(SAMPLE, encoding="utf-8")
    return path


def _design(tmp_path: Path) -> Path:
    design = tmp_path / "pcb"
    (design / "models").mkdir(parents=True)
    return design


def test_split_roundtrip_keeps_comments():
    preamble, blocks = split_profiles(SAMPLE)
    assert [name for name, _body in blocks] == ["default", "charge_pump"]
    assert render_profiles(preamble, blocks) == SAMPLE


def test_rename_changes_only_the_key(home: Path):
    rename_profile(home, "charge_pump", "pump")
    text = home.read_text(encoding="utf-8")
    assert text == SAMPLE.replace("charge_pump:", "pump:", 1)
    assert show_profile(home, "pump").startswith("pump:\n")


def test_remove_leaves_the_other_block(home: Path):
    remove_profile(home, "charge_pump")
    text = home.read_text(encoding="utf-8")
    assert "charge_pump" not in text
    assert "# shared" in text
    assert "# pump note" in text
    assert [row["name"] for row in list_profiles(home)] == ["default"]


def test_add_copies_block_inside_the_same_file(home: Path):
    add_profile(home, "charge_pump_b", "charge_pump")
    shown = show_profile(home, "charge_pump_b")
    assert shown.startswith("charge_pump_b:\n")
    assert "NE555 charge pump" in shown
    assert "charge_pump:" in home.read_text(encoding="utf-8")


def test_add_local_file_copies_from_shared_and_hides_other_names(home: Path, tmp_path: Path):
    design = _design(tmp_path)
    path, scope = resolve_catalog(design, use_local=True, writing=True)
    assert scope == "local"
    add_profile(path, "board", "charge_pump", source_path=home)
    assert [row["name"] for row in list_profiles(path)] == ["board"]
    assert home.read_text(encoding="utf-8") == SAMPLE
    read_path, read_scope = resolve_catalog(design)
    assert read_scope == "local"
    assert read_path == path


def test_local_add_moves_shared_profile_under_same_name(home: Path, tmp_path: Path):
    design = _design(tmp_path)
    path, _scope = resolve_catalog(design, use_local=True, writing=True)
    add_profile(path, "default", "default", source_path=home)
    add_profile(path, "charge_pump", "charge_pump", source_path=home)
    assert [row["name"] for row in list_profiles(path)] == ["default", "charge_pump"]
    assert "NE555 charge pump" in show_profile(path, "charge_pump")
    with pytest.raises(ProfileCatalogError, match="已经有"):
        add_profile(path, "charge_pump", "charge_pump", source_path=home)
    with pytest.raises(ProfileCatalogError, match="相同"):
        add_profile(path, "charge_pump", "charge_pump")


def test_shared_profiles_hint(home: Path, tmp_path: Path, capsys):
    import argparse

    from benchgate.cli import _note_shared_sim_profiles
    from benchgate.io.manifest import save_manifest
    from benchgate.schemas import MappingManifest

    design = _design(tmp_path)
    save_manifest(MappingManifest(), design / "models" / "manifest.yaml")
    _note_shared_sim_profiles(argparse.Namespace(design=str(design), profile="charge_pump"))
    assert "本机共用文件" in capsys.readouterr().err
    (design / "models" / "sim_profiles.yaml").write_text("default: {}\n", encoding="utf-8")
    _note_shared_sim_profiles(argparse.Namespace(design=str(design), profile="default"))
    assert capsys.readouterr().err == ""


def test_write_without_local_file_requires_an_explicit_target(tmp_path: Path, home: Path):
    design = _design(tmp_path)
    with pytest.raises(ProfileCatalogError, match="--global"):
        resolve_catalog(design, writing=True)
    path, scope = resolve_catalog(design)
    assert scope == "shared"
    assert path == home


def test_default_needs_force(home: Path):
    with pytest.raises(ProfileCatalogError, match="default"):
        remove_profile(home, "default")
    with pytest.raises(ProfileCatalogError, match="default"):
        rename_profile(home, "default", "generic")
    remove_profile(home, "default", force=True)
    assert "default:" not in home.read_text(encoding="utf-8")


def test_rename_updates_stop_hook_only(tmp_path: Path, home: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    hooks = repo / ".cursor" / "hooks.json"
    hooks.parent.mkdir()
    hooks.write_text(
        json.dumps(
            {
                "version": 1,
                "hooks": {
                    "stop": [
                        {"command": "/bin/benchgate hook stop --profile charge_pump", "timeout": 900},
                        {"command": "echo --profile charge_pump"},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    rename_profile(home, "charge_pump", "pump")
    assert update_hook_profile(repo, "charge_pump", "pump") is True
    text = hooks.read_text(encoding="utf-8")
    assert "hook stop --profile pump" in text
    assert "echo --profile charge_pump" in text


def test_remove_drops_profile_flag_from_hook(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    hooks = repo / ".cursor" / "hooks.json"
    hooks.parent.mkdir()
    hooks.write_text(
        json.dumps({"version": 1, "hooks": {"stop": [{"command": "benchgate hook stop --profile pump"}]}}),
        encoding="utf-8",
    )
    assert update_hook_profile(repo, "pump", None) is True
    assert json.loads(hooks.read_text(encoding="utf-8"))["hooks"]["stop"][0]["command"] == "benchgate hook stop"


def test_cli_list_add_and_refuse(home: Path, tmp_path: Path, capsys):
    assert main(["sim", "profile", "list"]) == 0
    out = capsys.readouterr().out
    assert "charge_pump" in out
    assert str(home) in out

    design = _design(tmp_path)
    assert main(["sim", "profile", "add", "board", "--from", "charge_pump", "--design", str(design)]) == 2
    err = capsys.readouterr().err
    assert "--local" in err

    assert main(
        ["sim", "profile", "add", "board", "--from", "charge_pump", "--design", str(design), "--local"]
    ) == 0
    message = capsys.readouterr().out
    assert "已添加 board" in message
    assert "不再使用本机共用文件" in message
