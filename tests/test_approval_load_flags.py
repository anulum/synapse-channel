# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — read-side audit flags replayed from journalled approval rows
"""Fast decisions and oversized batches are flagged from the journal alone, and decide nothing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from synapse_channel.core.approval_load_flags import (
    FLAG_BATCH_OVERSIZED,
    FLAG_FAST_DECISION,
    ApprovalLoadFlags,
)
from synapse_channel.core.approvals import (
    APPROVAL_NOTE_KIND,
    approval_text_sha256,
    format_approval_note,
)
from synapse_channel.core.attention import project_hub_attention
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore, StoredEvent

BATCH = "a" * 64


class Journal:
    """A real event store written with chosen timestamps."""

    def __init__(self, path: Path) -> None:
        self.store = EventStore(path)

    def note(self, ts: float, text: str, *, kind: str = APPROVAL_NOTE_KIND) -> None:
        payload = {"task_id": "T", "author": "P/req", "kind": kind, "text": text}
        self.store.append(EventKind.LEDGER_PROGRESS, payload, ts=ts, durable=True)

    def request(self, ts: float, subject: str, *, batch: str = "") -> str:
        text = format_approval_note(
            subject=subject, state="requested", approver="P/alice", batch=batch
        )
        self.note(ts, text)
        return text

    def admitted(self, ts: float, subject: str, text: str, **fields: Any) -> None:
        payload = {
            "subject": subject,
            "text_sha256": approval_text_sha256(text),
            "min_review_seconds": 30.0,
            "max_batch": 2,
            **fields,
        }
        self.store.append(EventKind.APPROVAL_LOAD_ADMITTED, payload, ts=ts, durable=True)

    def decide(self, ts: float, subject: str) -> None:
        self.note(ts, format_approval_note(subject=subject, state="approved"))

    def events(self) -> tuple[StoredEvent, ...]:
        return tuple(self.store.iter_events())


def _flags(events: tuple[StoredEvent, ...]) -> list[tuple[str, str, int]]:
    folder = ApprovalLoadFlags()
    folder.observe_all(events)
    return [(flag.kind, flag.subject, flag.seq) for flag in folder.flags()]


def test_a_decision_faster_than_the_admitted_review_time_is_flagged(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "hub.db")
    try:
        fast = journal.request(100.0, "S-fast")
        journal.admitted(100.1, "S-fast", fast)
        slow = journal.request(100.0, "S-slow")
        journal.admitted(100.1, "S-slow", slow)
        journal.decide(110.0, "S-fast")
        journal.decide(200.0, "S-slow")
        events = journal.events()
        assert _flags(events) == [(FLAG_FAST_DECISION, "S-fast", 5)]
        folder = ApprovalLoadFlags()
        folder.observe_all(events)
        assert "decided 10.000 s after the request at seq 1" in folder.flags()[0].detail
    finally:
        journal.store.close()


def test_a_request_without_its_admission_row_is_never_flagged(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "hub.db")
    try:
        journal.request(100.0, "S-1")
        journal.decide(100.5, "S-1")
        journal.decide(100.5, "S-unknown")
        journal.note(100.6, "approval subject= state=approved")
        journal.note(100.7, "not an approval", kind="note")
        assert _flags(journal.events()) == []
    finally:
        journal.store.close()


def test_an_admission_row_binds_only_its_own_request(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "hub.db")
    try:
        first = journal.request(100.0, "S-1")
        journal.admitted(100.1, "S-other", first)
        journal.admitted(100.1, "S-1", "a different text")
        journal.admitted(100.1, "S-1", first, min_review_seconds="30")
        journal.admitted(100.1, "S-1", first, max_batch=True)
        journal.decide(101.0, "S-1")
        assert _flags(journal.events()) == []
        again = journal.request(200.0, "S-1")
        journal.admitted(200.1, "S-1", again, min_review_seconds=5)
        journal.admitted(200.2, "S-1", again, min_review_seconds=999)
        journal.decide(210.0, "S-1")
        assert _flags(journal.events()) == []
    finally:
        journal.store.close()


def test_every_request_of_an_oversized_batch_is_flagged(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "hub.db")
    try:
        for index in range(3):
            subject = f"S-{index}"
            text = journal.request(100.0 + index, subject, batch=BATCH)
            journal.admitted(100.5 + index, subject, text, batch=BATCH)
        small = journal.request(200.0, "S-small", batch="c" * 64)
        journal.admitted(200.5, "S-small", small, batch="c" * 64)
        flags = _flags(journal.events())
        assert [(kind, subject) for kind, subject, _ in flags] == [
            (FLAG_BATCH_OVERSIZED, "S-0"),
            (FLAG_BATCH_OVERSIZED, "S-1"),
            (FLAG_BATCH_OVERSIZED, "S-2"),
        ]
    finally:
        journal.store.close()


def test_the_flags_become_open_warnings_in_the_attention_projection(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "hub.db")
    try:
        text = journal.request(100.0, "S-1", batch=BATCH)
        journal.admitted(100.1, "S-1", text, max_batch=0, batch=BATCH)
        journal.decide(101.0, "S-1")
        hostile = journal.request(100.0, "\ud800", batch=BATCH)
        journal.admitted(100.1, "\ud800", hostile, max_batch=0, batch=BATCH)
        items = {
            item.kind: item
            for item in project_hub_attention(journal.events(), now=500.0)
            if item.kind in {FLAG_FAST_DECISION, FLAG_BATCH_OVERSIZED}
        }
        assert set(items) == {FLAG_FAST_DECISION, FLAG_BATCH_OVERSIZED}
        for item in items.values():
            assert (item.subject, item.severity, item.state) == ("S-1", "warning", "open")
            assert item.action.startswith("Select for audit; this flag decides nothing (")
            assert item.expires_at is None
        assert items[FLAG_FAST_DECISION].key == "approval-fast-decision:S-1"
        assert items[FLAG_BATCH_OVERSIZED].key == "approval-batch-oversized:S-1"
        assert items[FLAG_FAST_DECISION].source_revision == "3"
    finally:
        journal.store.close()
