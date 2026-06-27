# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — replayable postmortem regressions

from __future__ import annotations

from pathlib import Path
from typing import cast

from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.postmortem import (
    build_task_postmortem,
    postmortem_to_json,
    render_markdown,
    run_task_postmortem,
)
from synapse_channel.core.state import TaskClaim


def _claim(**overrides: object) -> TaskClaim:
    base: dict[str, object] = {
        "task_id": "T1",
        "owner": "alpha",
        "note": "initial claim",
        "claimed_at": 10.0,
        "lease_expires_at": 120.0,
        "status": "claimed",
        "data_ref": "",
        "worktree": "repo",
        "paths": ("src/auth.py",),
        "epoch": 1,
        "checkpoint": "",
    }
    base.update(overrides)
    return TaskClaim(**base)  # type: ignore[arg-type]


def _seed_postmortem_store(path: Path) -> None:
    store = EventStore(path)
    store.append(EventKind.CLAIM, _claim().as_dict(), ts=10.0, durable=True)
    store.append(
        EventKind.CLAIM,
        _claim(
            task_id="T2",
            owner="beta",
            note="overlapping claim",
            paths=("src",),
            epoch=2,
            claimed_at=12.0,
        ).as_dict(),
        ts=12.0,
        durable=True,
    )
    store.append(
        EventKind.CHAT,
        {
            "from": "lead",
            "target": "alpha",
            "payload": "T1 status?",
            "msg_id": 1,
        },
        ts=13.0,
    )
    store.append(
        EventKind.TASK_UPDATE,
        _claim(status="in_progress", data_ref="mem://draft", epoch=3).as_dict(),
        ts=20.0,
        durable=True,
    )
    store.append(
        EventKind.LEDGER_PROGRESS,
        {
            "task_id": "T1",
            "author": "alpha",
            "kind": "assessment",
            "text": "release receipt: evidence=pytest tests/test_postmortem.py -q",
            "posted_at": 29.0,
        },
        ts=29.0,
    )
    store.append(EventKind.RELEASE, {"task_id": "T1"}, ts=30.0, durable=True)
    store.close()


def test_task_postmortem_reconstructs_timeline_conflicts_evidence_and_messages(
    tmp_path: Path,
) -> None:
    db = tmp_path / "events.db"
    _seed_postmortem_store(db)

    store = EventStore(db)
    try:
        report = build_task_postmortem("T1", store.read_all())
    finally:
        store.close()

    assert report.task_id == "T1"
    assert report.generated_from_seq == 6
    assert report.owners == ("alpha",)
    assert [entry.kind for entry in report.timeline] == [
        "claim",
        "chat",
        "task_update",
        "ledger_progress",
        "release",
    ]
    assert report.releases[0].seq == 6
    assert report.evidence_notes[0].text == (
        "release receipt: evidence=pytest tests/test_postmortem.py -q"
    )
    assert report.conflicts == (
        {
            "seq": 2,
            "ts": 12.0,
            "left_task": "T1",
            "left_owner": "alpha",
            "right_task": "T2",
            "right_owner": "beta",
            "worktree": "repo",
            "paths": ["src/auth.py", "src"],
        },
    )
    assert report.unanswered_messages[0].sender == "lead"
    assert report.unanswered_messages[0].target == "alpha"
    assert report.unanswered_messages[0].payload == "T1 status?"


def test_postmortem_json_and_markdown_are_stable(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    _seed_postmortem_store(db)

    report = run_task_postmortem(db, "T1")
    payload = postmortem_to_json(report)
    markdown = render_markdown(report)

    assert payload["task_id"] == "T1"
    assert payload["owners"] == ["alpha"]
    timeline = cast(list[dict[str, object]], payload["timeline"])
    conflicts = cast(list[dict[str, object]], payload["conflicts"])
    evidence_notes = cast(list[dict[str, object]], payload["evidence_notes"])
    unanswered_messages = cast(list[dict[str, object]], payload["unanswered_messages"])
    assert timeline[0]["kind"] == "claim"
    assert conflicts[0]["right_task"] == "T2"
    assert evidence_notes[0]["author"] == "alpha"
    assert unanswered_messages[0]["sender"] == "lead"
    assert "# Postmortem: T1" in markdown
    assert "## Timeline" in markdown
    assert "seq=6" in markdown
    assert "beta" in markdown
    assert "release receipt: evidence=pytest tests/test_postmortem.py -q" in markdown


def test_missing_or_empty_task_postmortem_is_explicit(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    store = EventStore(db)
    store.close()

    report = run_task_postmortem(db, "MISSING")

    assert report.task_id == "MISSING"
    assert report.timeline == ()
    assert render_markdown(report) == "# Postmortem: MISSING\n\nNo task events found."


def test_postmortem_distinguishes_non_conflicts_and_answered_messages(
    tmp_path: Path,
) -> None:
    db = tmp_path / "events.db"
    store = EventStore(db)
    store.append(
        EventKind.CLAIM,
        _claim(task_id="OTHER", owner="beta", paths=("docs",), epoch=1).as_dict(),
        ts=1.0,
        durable=True,
    )
    store.append(
        EventKind.CLAIM,
        _claim(task_id="T1", owner="alpha", paths=("src/api.py",), epoch=2).as_dict(),
        ts=2.0,
        durable=True,
    )
    store.append(
        EventKind.CLAIM,
        _claim(task_id="T3", owner="gamma", paths=(), epoch=3).as_dict(),
        ts=3.0,
        durable=True,
    )
    store.append(
        EventKind.CLAIM,
        _claim(task_id="T4", owner="alpha", paths=("src/api.py",), epoch=4).as_dict(),
        ts=4.0,
        durable=True,
    )
    store.append(EventKind.CHAT, {"from": "lead", "target": "all", "payload": "T1 FYI"}, ts=5.0)
    store.append(
        EventKind.CHAT,
        {"from": "lead", "target": "alpha", "payload": "T1 status?"},
        ts=6.0,
    )
    store.append(
        EventKind.CHAT,
        {"from": "alpha", "target": "lead", "payload": "T1 done"},
        ts=7.0,
    )
    store.close()

    report = run_task_postmortem(db, "T1")
    markdown = render_markdown(report)

    assert report.conflicts == (
        {
            "seq": 3,
            "ts": 3.0,
            "left_task": "T1",
            "left_owner": "alpha",
            "right_task": "T3",
            "right_owner": "gamma",
            "worktree": "repo",
            "paths": ["src/api.py"],
        },
    )
    assert report.evidence_notes == ()
    assert report.unanswered_messages == ()
    assert "## Evidence\n- none" in markdown
    assert "## Candidate Unanswered Messages\n- none" in markdown
