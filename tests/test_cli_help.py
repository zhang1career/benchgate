"""CLI --help text for out-of-project usage."""

from __future__ import annotations

import subprocess
import sys


def _help(*args: str) -> str:
    proc = subprocess.run(
        [sys.executable, "-m", "benchgate.cli", *args, "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


def test_root_help_documents_design_and_docs():
    text = _help()
    assert "Design–verification loop" in text
    assert "--design /path/to/myboard" in text
    assert "docs/examples/blocks.yaml" in text


def test_watch_once_help_has_prerequisites():
    text = _help("watch", "once")
    assert "blocks.yaml" in text
    assert "benchgate watch once --design" in text


def test_pipeline_sync_help_explains_scope():
    text = _help("pipeline", "sync")
    assert "Does not run mapping sync" in text
    assert "benchgate pipeline sync --design" in text


def test_mapping_sync_design_help():
    text = _help("mapping", "sync")
    assert "absolute path" in text
