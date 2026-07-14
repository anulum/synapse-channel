# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dedicated tests for the dashboard postmortem projection

from __future__ import annotations

from typing import Any

import pytest

from synapse_channel import dashboard_postmortem_feed as feed


class _Report:
    def __init__(self, timeline: list[dict[str, Any]]) -> None:
        self.timeline = timeline


def _patch(
    monkeypatch: pytest.MonkeyPatch, report: _Report, document: dict[str, object]
) -> dict[str, Any]:
    """Replace the postmortem collaborators and return the recorded call."""
    recorded: dict[str, Any] = {}

    def _run(db_path: Any, task_id: str, *, key_file: Any) -> _Report:
        recorded.update(db_path=db_path, task_id=task_id, key_file=key_file)
        return report

    monkeypatch.setattr(feed, "run_task_postmortem", _run)
    monkeypatch.setattr(feed, "postmortem_to_json", lambda report: dict(document))
    return recorded


def test_present_true_for_a_non_empty_timeline(monkeypatch: pytest.MonkeyPatch) -> None:
    report = _Report([{"event": "claimed"}])
    recorded = _patch(monkeypatch, report, {"task_id": "t1", "events": 1})
    document = feed.build_postmortem_feed("hub.sqlite", "t1", key_file="key")
    assert document["present"] is True
    assert document["events"] == 1
    assert "replayable task evidence" in str(document["note"])
    assert recorded == {"db_path": "hub.sqlite", "task_id": "t1", "key_file": "key"}


def test_present_false_for_an_empty_timeline(monkeypatch: pytest.MonkeyPatch) -> None:
    report = _Report([])
    _patch(monkeypatch, report, {"task_id": "t1"})
    document = feed.build_postmortem_feed("hub.sqlite", "t1")
    assert document["present"] is False
    assert "no matching task event was recorded" in str(document["note"])


def test_key_file_defaults_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    report = _Report([{"event": "done"}])
    recorded = _patch(monkeypatch, report, {})
    feed.build_postmortem_feed("hub.sqlite", "t1")
    assert recorded["key_file"] is None


def test_endpoint_constants_are_exposed() -> None:
    assert feed.POSTMORTEM_PATH == "/postmortem.json"
    assert feed.MAX_POSTMORTEM_TASK_ID_LENGTH == 512
