# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — human-in-the-loop approval gate regressions

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from synapse_channel.core.approvals import (
    APPROVAL_NOTE_KIND,
    AWAITING,
    approvals_to_json,
    build_approval_report,
    format_approval_note,
    parse_approval_note,
    render_human,
    run_approval_report,
)
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore, StoredEvent


def _event(
    *,
    seq: int,
    author: str,
    subject: str,
    state: str,
    reason: str = "",
    ts: float = 1.0,
) -> StoredEvent:
    return StoredEvent(
        seq=seq,
        ts=ts,
        kind=EventKind.LEDGER_PROGRESS,
        payload={
            "author": author,
            "kind": APPROVAL_NOTE_KIND,
            "task_id": subject,
            "text": format_approval_note(subject=subject, state=state, reason=reason),
        },
    )


# ---------- format / parse ----------


def test_format_approval_note_variants() -> None:
    assert format_approval_note(subject="  T1  ", state="requested") == (
        "approval subject=T1 state=requested"
    )
    full = format_approval_note(subject="T1", state="approved", reason="looks  good\nship")
    assert full == "approval subject=T1 state=approved :: looks good ship"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"subject": "", "state": "requested"},
        {"subject": "two words", "state": "requested"},
        {"subject": "T1", "state": "bogus"},
    ],
)
def test_format_approval_note_rejects_bad_input(kwargs: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        format_approval_note(**kwargs)


def test_parse_approval_note_roundtrip_with_reason() -> None:
    parsed = parse_approval_note("approval subject=T1 state=rejected :: not ready :: retry")
    assert parsed == {"subject": "T1", "state": "rejected", "reason": "not ready :: retry"}


def test_parse_approval_note_without_reason() -> None:
    assert parse_approval_note("approval subject=T1 state=approved") == {
        "subject": "T1",
        "state": "approved",
        "reason": "",
    }


@pytest.mark.parametrize(
    "text",
    [
        "note subject=T1 state=requested",
        "approval state=requested",
        "approval subject=T1 state=bogus",
        "approval subject= state=requested",
        "",
    ],
)
def test_parse_approval_note_rejects_invalid(text: str) -> None:
    assert parse_approval_note(text) is None


def test_parse_approval_note_ignores_malformed_pairs() -> None:
    parsed = parse_approval_note("approval stray subject=T1 state=requested")
    assert parsed == {"subject": "T1", "state": "requested", "reason": ""}


# ---------- replay ----------


def test_replay_request_only_is_awaiting() -> None:
    report = build_approval_report([_event(seq=1, author="dev", subject="T1", state="requested")])
    status = report.by_subject["T1"]
    assert status.current_state == AWAITING
    assert status.is_pending is True
    assert status.requested_by == "dev"
    assert status.decided_by == ""
    assert report.pending == (status,)


def test_replay_request_then_approve() -> None:
    report = build_approval_report(
        [
            _event(seq=1, author="dev", subject="T1", state="requested", ts=1.0),
            _event(seq=2, author="ceo", subject="T1", state="approved", reason="ok", ts=2.0),
        ]
    )
    status = report.by_subject["T1"]
    assert status.current_state == "approved"
    assert status.is_pending is False
    assert status.decided_by == "ceo"
    assert status.decided_at == 2.0
    assert status.decision_reason == "ok"


def test_replay_rerequest_after_decision_reopens() -> None:
    report = build_approval_report(
        [
            _event(seq=1, author="dev", subject="T1", state="requested", ts=1.0),
            _event(seq=2, author="ceo", subject="T1", state="rejected", reason="no", ts=2.0),
            _event(seq=3, author="dev", subject="T1", state="requested", ts=3.0),
        ]
    )
    status = report.by_subject["T1"]
    assert status.current_state == AWAITING
    assert status.decided_by == ""  # decision cleared on re-open
    assert status.decided_at == 0.0
    assert status.decision_reason == ""
    assert len(status.history) == 3


def test_build_report_sorts_subjects_and_ignores_noise() -> None:
    report = build_approval_report(
        [
            _event(seq=1, author="dev", subject="ZZ", state="requested"),
            _event(seq=2, author="dev", subject="AA", state="approved"),
            StoredEvent(3, 5.0, EventKind.CHAT, {"text": "hi"}),
            StoredEvent(4, 5.0, EventKind.LEDGER_PROGRESS, {"kind": "note", "text": "x"}),
            StoredEvent(
                5,
                5.0,
                EventKind.LEDGER_PROGRESS,
                {"kind": APPROVAL_NOTE_KIND, "text": "approval x"},
            ),
        ]
    )
    assert [status.subject for status in report.statuses] == ["AA", "ZZ"]
    assert report.generated_from_seq == 5


# ---------- run / json / human ----------


def test_run_report_missing_store_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing event store"):
        run_approval_report(tmp_path / "nope.db")


def test_run_report_reads_real_store(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    store = EventStore(db)
    store.append(
        EventKind.LEDGER_PROGRESS,
        {
            "author": "dev",
            "kind": APPROVAL_NOTE_KIND,
            "task_id": "T1",
            "text": format_approval_note(subject="T1", state="requested"),
        },
        durable=True,
    )
    store.close()
    report = run_approval_report(db)
    assert report.by_subject["T1"].is_pending is True


def test_approvals_to_json_shape() -> None:
    report = build_approval_report(
        [
            _event(seq=1, author="dev", subject="T1", state="requested"),
            _event(seq=2, author="ceo", subject="T1", state="approved", reason="ok"),
        ]
    )
    payload = approvals_to_json(report)
    assert cast(str, payload["note"]).startswith("advisory approval evidence")
    statuses = cast("list[dict[str, object]]", payload["statuses"])
    assert statuses[0]["subject"] == "T1"
    assert statuses[0]["current_state"] == "approved"
    history = cast("list[dict[str, object]]", statuses[0]["history"])
    assert len(history) == 2


def test_render_human_empty_and_populated() -> None:
    assert "No approval activity" in render_human(build_approval_report([]))
    report = build_approval_report(
        [
            _event(seq=1, author="dev", subject="T1", state="requested"),
            _event(seq=2, author="dev", subject="T2", state="requested"),
            _event(seq=3, author="ceo", subject="T2", state="rejected", reason="later"),
            _event(seq=4, author="dev", subject="T3", state="requested"),
            _event(seq=5, author="ceo", subject="T3", state="approved"),  # decided, no reason
        ]
    )
    text = render_human(report)
    assert "pending=1 of 3 subjects" in text
    assert "T1: awaiting_approval" in text
    assert "T2: rejected" in text
    assert "later" in text
    assert "T3: approved — approved by ceo" in text
