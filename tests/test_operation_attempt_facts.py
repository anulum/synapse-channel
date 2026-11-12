# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the current approval of an intent, as the journal holds it
"""The latest decision on a digest decides; the gate is evaluated now; bypasses are seen."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from synapse_channel.core.approvals import APPROVAL_NOTE_KIND, format_approval_note
from synapse_channel.core.journal import EventKind
from synapse_channel.core.operation_attempt_facts import current_approval
from synapse_channel.core.operation_attempts import ApprovalFact
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.verdict_hub import VerdictHubPolicy
from synapse_channel.core.verdict_records import (
    CORRECTION_NOTE_KIND,
    EVIDENCE_NOTE_KIND,
    VERDICT_NOTE_KIND,
    CorrectionRecord,
    EvidenceRecord,
    VerdictRecord,
    format_correction,
    format_evidence,
    format_verdict,
)

DIGEST = "d" * 64
INPUT = "1" * 64
APPROVER = "P/approver"
REVIEWER = "P/reviewer"
POLICY = VerdictHubPolicy()


@pytest.fixture
def store(tmp_path: Path) -> Iterator[EventStore]:
    journal = EventStore(tmp_path / "hub.db")
    yield journal
    journal.close()


def _note(store: EventStore, author: str, kind: str, text: str) -> int:
    payload = {"author": author, "kind": kind, "text": text, "task_id": "T1"}
    return store.append(EventKind.LEDGER_PROGRESS, payload, durable=True)


def _decision(store: EventStore, state: str, *, author: str = APPROVER, subject: str = "") -> int:
    text = format_approval_note(subject=subject or f"sha256:{DIGEST}", state=state)
    return _note(store, author, APPROVAL_NOTE_KIND, text)


def _accepted_review(store: EventStore) -> None:
    for digest in (INPUT, DIGEST):
        _note(store, REVIEWER, EVIDENCE_NOTE_KIND, format_evidence(EvidenceRecord(digest, "P")))
    verdict = VerdictRecord("P", DIGEST, "accepted", (INPUT,), (), REVIEWER, 1)
    _note(store, REVIEWER, VERDICT_NOTE_KIND, format_verdict(verdict))


def _bypass(store: EventStore, author: str, subject: str = DIGEST) -> int:
    payload = {"author": author, "subject": subject, "gate_would_refuse": False, "reasons": []}
    return store.append(EventKind.VERDICT_GATE_BYPASS, payload, durable=True)


def test_no_decision_and_a_latest_request_or_rejection_mean_no_approval(
    store: EventStore,
) -> None:
    assert current_approval(store, POLICY, DIGEST) is None
    _decision(store, "approved")
    _decision(store, "requested")
    assert current_approval(store, POLICY, DIGEST) is None
    _decision(store, "approved")
    _decision(store, "rejected", author="P/other")
    assert current_approval(store, POLICY, DIGEST) is None


def test_an_approval_with_a_fresh_accepted_verdict_passes_the_gate_now(
    store: EventStore,
) -> None:
    _accepted_review(store)
    seq = _decision(store, "approved", subject=DIGEST)
    assert current_approval(store, POLICY, DIGEST) == ApprovalFact(seq, APPROVER, False, ())


def test_the_gate_is_evaluated_now_and_even_when_the_hub_has_it_off(store: EventStore) -> None:
    seq = _decision(store, "approved")
    unreviewed = ApprovalFact(seq, APPROVER, False, ("no verdict recorded",))
    assert current_approval(store, POLICY, DIGEST) == unreviewed
    off = VerdictHubPolicy(gate_enabled=False)
    assert current_approval(store, off, DIGEST) == unreviewed
    _accepted_review(store)
    fix = format_correction(CorrectionRecord("P", INPUT, "2" * 64, "input re-exported"))
    _note(store, REVIEWER, CORRECTION_NOTE_KIND, fix)
    approval = current_approval(store, POLICY, DIGEST)
    assert approval is not None
    assert len(approval.gate_reasons) == 1
    stale = rf"{REVIEWER}: stale input, unacknowledged corrections [0-9a-f]{{64}}"
    assert re.fullmatch(stale, approval.gate_reasons[0])


def test_other_notes_and_other_digests_are_not_decisions(store: EventStore) -> None:
    seq = _decision(store, "approved")
    _decision(store, "rejected", subject="e" * 64)
    _note(store, APPROVER, "status", "approval subject=sha256:" + DIGEST + " state=rejected")
    _note(store, APPROVER, APPROVAL_NOTE_KIND, "not an approval note")
    approval = current_approval(store, POLICY, DIGEST)
    assert approval is not None and approval.seq == seq


def test_a_bypass_by_the_approver_since_its_previous_note_marks_the_approval(
    store: EventStore,
) -> None:
    _bypass(store, APPROVER)
    _decision(store, "approved")
    _bypass(store, "P/other")
    _bypass(store, APPROVER, subject="e" * 64)
    seq = _decision(store, "approved")
    approval = current_approval(store, POLICY, DIGEST)
    assert approval is not None and (approval.seq, approval.bypassed) == (seq, False)
    _bypass(store, APPROVER, subject=f"sha256:{DIGEST}")
    seq = _decision(store, "approved")
    approval = current_approval(store, POLICY, DIGEST)
    assert approval is not None and (approval.seq, approval.bypassed) == (seq, True)
