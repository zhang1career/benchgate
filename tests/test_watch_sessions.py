"""Tests for tagged session watch triggers."""

from __future__ import annotations

import yaml

from benchgate.watch.trigger import detect_tagged_session_changes


def test_detect_tagged_session_changes(tmp_path):
    design = tmp_path / "design"
    sessions = design / "models" / "captured" / "sessions" / "sess1"
    sessions.mkdir(parents=True)
    meta = {
        "session_id": "sess1",
        "captured_at": "2026-01-01T00:00:00+00:00",
        "tags": ["anomaly"],
    }
    (sessions / "session.yaml").write_text(yaml.safe_dump(meta), encoding="utf-8")
    state = design / "models" / ".watch_state.json"

    first = detect_tagged_session_changes(design, state)
    assert first == ["sess1"]

    second = detect_tagged_session_changes(design, state)
    assert second == []
