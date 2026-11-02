# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — journal-backed admission of verdict records and the approval gate
"""Read records from a real journal, admit or refuse new ones, and gate approvals."""

from __future__ import annotations

from pathlib import Path

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.core.approvals import APPROVAL_NOTE_KIND, format_approval_note
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.ledger import ProgressNote
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.verdict_hub import (
    VerdictHubPolicy,
    admission_refusal,
    approval_refusal,
    recorded_notes,
    subject_digest,
)
from synapse_channel.core.verdict_records import (
    CORRECTION_NOTE_KIND,
    EVIDENCE_NOTE_KIND,
    CorrectionRecord,
    EvidenceRecord,
    format_correction,
    format_evidence,
)

RAW = "1" * 64
RAW_FIXED = "2" * 64


def _append(store: EventStore, author: str, kind: str, text: str) -> None:
    note = ProgressNote(task_id="T", author=author, kind=kind, text=text, posted_at=1.0)
    store.append(EventKind.LEDGER_PROGRESS, note.as_dict())


def test_journal_records_drive_admission_and_authority(tmp_path: Path) -> None:
    """Only verdict-family notes are read; allow-listed seats may declare and correct."""
    store = EventStore(tmp_path / "hub.db")
    _append(store, "LAB/writer", "note", "plain progress is ignored")
    _append(store, "OTHER/seat", EVIDENCE_NOTE_KIND, format_evidence(EvidenceRecord(RAW, "LAB")))
    notes = recorded_notes(store)
    assert [note.kind for note in notes] == [EVIDENCE_NOTE_KIND]
    policy = VerdictHubPolicy(verdict_authority={"LAB": frozenset({"OTHER/auditor"})})
    fix = format_correction(CorrectionRecord("LAB", RAW, RAW_FIXED, "re-exported"))
    # The earlier foreign declaration was refused, so nobody declared RAW yet.
    assert (
        admission_refusal(notes, policy, author="LAB/writer", kind=CORRECTION_NOTE_KIND, text=fix)
        == "correction-not-authorised"
    )
    declared = format_evidence(EvidenceRecord(RAW, "LAB"))
    assert (
        admission_refusal(
            notes, policy, author="OTHER/auditor", kind=EVIDENCE_NOTE_KIND, text=declared
        )
        is None
    )
    malformed = admission_refusal(
        notes, policy, author="LAB/writer", kind=EVIDENCE_NOTE_KIND, text="{}"
    )
    assert malformed is not None and malformed.startswith("malformed evidence record")
    store.close()


def test_subject_digest_and_gate_skip_other_subjects() -> None:
    """Only SHA-256 subjects are gated; a plain release subject is never refused here."""
    assert subject_digest("sha256:" + RAW) == RAW
    assert subject_digest(RAW) == RAW
    assert subject_digest("release-0.99.43") is None
    approver = "LAB/approver"
    assert approval_refusal([], VerdictHubPolicy(), "release-0.99.43", approver=approver) is None
    refused = approval_refusal([], VerdictHubPolicy(), RAW, approver=approver)
    assert refused is not None and refused.reasons == ("no verdict recorded",)


async def test_approval_request_is_not_gated(tmp_path: Path) -> None:
    """Requesting an approval of an unreviewed object is allowed; only approving is gated."""
    store = EventStore(tmp_path / "hub.db")
    async with running_hub(SynapseHub(journal=store)) as (_hub, uri):
        seat = await connect_agent("LAB/approver", uri)
        try:
            request = format_approval_note(subject=RAW, state="requested", reason="")
            await seat.agent.post_progress(RAW, request, kind=APPROVAL_NOTE_KIND)
            await seat.recorder.wait_for(
                lambda m: m.get("type") == "ledger_progress_posted" and m["note"]["text"] == request
            )
        finally:
            await close_agents(seat)
    store.close()
