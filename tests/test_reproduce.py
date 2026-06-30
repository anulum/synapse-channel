# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — deterministic-reproduction digest regressions

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore, StoredEvent
from synapse_channel.core.reproduce import (
    authoritative_slice,
    canonical_bytes,
    render_markdown,
    reproduce_task,
    reproduction_to_json,
    run_reproduction,
    slice_digest,
    verify_reproduction,
)
from synapse_channel.core.state import TaskClaim


def _claim(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "task_id": "T1",
        "owner": "alice",
        "note": "start",
        "claimed_at": 10.0,
        "lease_expires_at": 100.0,
        "status": "claimed",
        "data_ref": "",
        "worktree": "repo",
        "paths": ("src/auth.py",),
        "epoch": 1,
        "version": 0,
        "checkpoint": "",
    }
    base.update(overrides)
    return TaskClaim(**base).as_dict()  # type: ignore[arg-type]


def _live_events() -> tuple[StoredEvent, ...]:
    """T1 still held (claim -> update), plus an unrelated T2 and a non-authoritative chat."""
    return (
        StoredEvent(seq=1, ts=10.0, kind=EventKind.CLAIM, payload=_claim()),
        StoredEvent(seq=2, ts=11.0, kind=EventKind.CHAT, payload={"task_id": "T1", "from": "x"}),
        StoredEvent(
            seq=3, ts=12.0, kind=EventKind.CLAIM, payload=_claim(task_id="T2", owner="bob")
        ),
        StoredEvent(
            seq=4,
            ts=13.0,
            kind=EventKind.TASK_UPDATE,
            payload=_claim(status="in_progress", checkpoint="step1", version=1),
        ),
    )


def _released_events() -> tuple[StoredEvent, ...]:
    return (
        *_live_events(),
        StoredEvent(seq=5, ts=14.0, kind=EventKind.RELEASE, payload={"task_id": "T1"}),
    )


def _seed(path: Path, events: tuple[StoredEvent, ...]) -> None:
    store = EventStore(path)
    for event in events:
        store.append(event.kind, event.payload, ts=event.ts)
    store.close()


def test_authoritative_slice_drops_unrelated_and_non_authoritative() -> None:
    slice_events = authoritative_slice("  T1  ", _released_events())

    assert [event.seq for event in slice_events] == [1, 4, 5]


def test_reproduce_live_task_reports_final_claim() -> None:
    report = reproduce_task("T1", _live_events())

    assert report.present is True
    assert report.event_count == 2
    assert report.first_seq == 1
    assert report.last_seq == 4
    assert report.final_owner == "alice"
    assert report.final_status == "in_progress"
    assert len(report.digest) == 64


def test_reproduce_released_task_reports_released_status() -> None:
    report = reproduce_task("T1", _released_events())

    assert report.final_status == "released"
    assert report.final_owner == ""
    assert report.last_seq == 5


def test_reproduce_unknown_task_is_absent() -> None:
    report = reproduce_task("ghost", _live_events())

    assert report.present is False
    assert report.event_count == 0
    assert report.first_seq == 0
    assert report.last_seq == 0
    assert report.final_status == "absent"
    assert report.final_owner == ""


def test_digest_is_deterministic_across_runs_and_event_order() -> None:
    forward = reproduce_task("T1", _released_events())
    shuffled = reproduce_task("T1", tuple(reversed(_released_events())))

    assert forward.digest == shuffled.digest


def test_digest_changes_when_history_differs() -> None:
    base = reproduce_task("T1", _live_events())
    mutated_events = (
        *_live_events()[:-1],
        StoredEvent(
            seq=4,
            ts=13.0,
            kind=EventKind.TASK_UPDATE,
            payload=_claim(status="blocked", checkpoint="step1", version=1),
        ),
    )
    mutated = reproduce_task("T1", mutated_events)

    assert base.digest != mutated.digest


def test_canonical_bytes_is_sorted_and_compact() -> None:
    raw = canonical_bytes(authoritative_slice("T1", _live_events()))

    assert b'"kind":' in raw
    assert b", " not in raw  # compact separators


def test_slice_digest_of_empty_slice_is_stable() -> None:
    assert slice_digest(()) == slice_digest(())
    assert len(slice_digest(())) == 64


def test_verify_reproduction_matches_case_insensitively() -> None:
    report = reproduce_task("T1", _live_events())

    assert verify_reproduction(report, f"  {report.digest.upper()}  ") is True
    assert verify_reproduction(report, "deadbeef") is False


def test_reproduction_to_json_exposes_fingerprint() -> None:
    payload = reproduction_to_json(reproduce_task("T1", _live_events()))

    assert payload["task_id"] == "T1"
    assert payload["present"] is True
    assert payload["final_status"] == "in_progress"
    assert len(str(payload["digest"])) == 64


def test_render_markdown_present_lists_digest() -> None:
    text = render_markdown(reproduce_task("T1", _live_events()))

    assert "# Reproduce: T1" in text
    assert "Digest (sha256):" in text
    assert "Final status: in_progress" in text


def test_render_markdown_absent_states_no_events() -> None:
    text = render_markdown(reproduce_task("ghost", _live_events()))

    assert "No authoritative events found." in text


def test_run_reproduction_loads_store(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed(db, _released_events())

    report = run_reproduction(db, "T1")

    assert report.final_status == "released"
    assert report.digest == reproduce_task("T1", _released_events()).digest


def test_run_reproduction_missing_store_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing event store"):
        run_reproduction(tmp_path / "absent.db", "T1")
