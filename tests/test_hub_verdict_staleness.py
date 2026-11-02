# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a real hub stales a verdict when an input it read is corrected
"""The public scenario end to end: correct a raw record after its abstract was approved."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.core.approvals import APPROVAL_NOTE_KIND, format_approval_note
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.universal_receipts import universal_receipts_from_events
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

RAW = "1" * 64
RAW_FIXED = "2" * 64
ABSTRACT = "3" * 64
WRITER = "LAB/writer"
REVIEWER = "LAB/reviewer"
APPROVER = "LAB/approver"
STRANGER = "OTHER/stranger"


def _verdict() -> str:
    return format_verdict(VerdictRecord("LAB", ABSTRACT, "accepted", (RAW,), (), WRITER, 1))


def _approve() -> str:
    return format_approval_note(subject=ABSTRACT, state="approved", reason="")


async def _posted(seat: Any, text: str) -> dict[str, Any]:
    message: dict[str, Any] = await seat.recorder.wait_for(
        lambda m: m.get("type") == "ledger_progress_posted" and m["note"]["text"] == text
    )
    return message


async def _error(seat: Any, fragment: str) -> dict[str, Any]:
    message: dict[str, Any] = await seat.recorder.wait_for(
        lambda m: m.get("type") == "error" and fragment in str(m.get("payload"))
    )
    return message


async def test_correction_stales_the_review_and_blocks_the_approval(tmp_path: Path) -> None:
    """Evidence, verdict, approval, correction, notice, refusal and replay on a real hub."""
    db = tmp_path / "hub.db"
    store = EventStore(db)
    async with running_hub(SynapseHub(journal=store)) as (_hub, uri):
        writer = await connect_agent(WRITER, uri)
        reviewer = await connect_agent(REVIEWER, uri)
        approver = await connect_agent(APPROVER, uri)
        stranger = await connect_agent(STRANGER, uri)
        try:
            for digest in (RAW, ABSTRACT):
                body = format_evidence(EvidenceRecord(digest, "LAB"))
                await writer.agent.post_progress("STUDY", body, kind=EVIDENCE_NOTE_KIND)
                await _posted(writer, body)
            await reviewer.agent.post_progress("STUDY", _verdict(), kind=VERDICT_NOTE_KIND)
            await _posted(reviewer, _verdict())
            await approver.agent.post_progress(ABSTRACT, _approve(), kind=APPROVAL_NOTE_KIND)
            await _posted(approver, _approve())

            foreign = format_correction(CorrectionRecord("LAB", RAW, RAW_FIXED, "not mine to fix"))
            await stranger.agent.post_progress("STUDY", foreign, kind=CORRECTION_NOTE_KIND)
            await _error(stranger, "correction-not-authorised")

            # Declaring the same hash in its own namespace and correcting it there is
            # allowed, and touches nothing in LAB: the approval below still passes.
            own = format_evidence(EvidenceRecord(RAW, "OTHER"))
            await stranger.agent.post_progress("STUDY", own, kind=EVIDENCE_NOTE_KIND)
            await _posted(stranger, own)
            elsewhere = format_correction(CorrectionRecord("OTHER", RAW, RAW_FIXED, "own copy"))
            await stranger.agent.post_progress("STUDY", elsewhere, kind=CORRECTION_NOTE_KIND)
            await _posted(stranger, elsewhere)
            again = format_approval_note(subject=ABSTRACT, state="approved", reason="still fresh")
            await approver.agent.post_progress(ABSTRACT, again, kind=APPROVAL_NOTE_KIND)
            await _posted(approver, again)

            fix = format_correction(
                CorrectionRecord("LAB", RAW, RAW_FIXED, "raw record re-exported")
            )
            await writer.agent.post_progress("STUDY", fix, kind=CORRECTION_NOTE_KIND)
            await _posted(writer, fix)
            notice = await reviewer.recorder.wait_for(lambda m: "verdict_stale" in m)
            assert notice["verdict_stale"]["subject_sha256"] == ABSTRACT
            assert notice["verdict_stale"]["corrected_by"] == WRITER

            await approver.agent.post_progress(ABSTRACT, _approve(), kind=APPROVAL_NOTE_KIND)
            refusal = await _error(approver, "approval gate")
            assert "stale input" in refusal["payload"]
        finally:
            await close_agents(writer, reviewer, approver, stranger)
    kinds = [event.kind for event in store.iter_events()]
    assert kinds.count(EventKind.VERDICT_STALE) == 1
    assert kinds.count(EventKind.VERDICT_RECORD_REFUSED) == 2
    refused = store.read_since(0, kinds=(EventKind.VERDICT_RECORD_REFUSED,))
    assert {row.payload["kind"] for row in refused} == {CORRECTION_NOTE_KIND, APPROVAL_NOTE_KIND}
    assert all("text" not in row.payload for row in refused)
    receipts = universal_receipts_from_events(store.iter_events())
    by_kind = {receipt.kind: receipt for receipt in receipts}
    assert by_kind["verdict-stale"].subject == ABSTRACT
    assert by_kind["verdict-stale"].actor == REVIEWER
    assert by_kind["verdict-record-refused"].status == "refused"
    store.close()

    # After a restart the gate still refuses: staleness comes from the journal.
    reopened = EventStore(db)
    async with running_hub(SynapseHub(journal=reopened)) as (_hub, uri):
        approver = await connect_agent(APPROVER, uri)
        try:
            await approver.agent.post_progress(ABSTRACT, _approve(), kind=APPROVAL_NOTE_KIND)
            await _error(approver, "approval gate")
        finally:
            await close_agents(approver)
    reopened.close()


async def test_switched_off_gate_and_hub_without_journal(tmp_path: Path) -> None:
    """The recorded off switch lets approvals pass; a journal-less hub refuses records."""
    store = EventStore(tmp_path / "hub.db")
    async with running_hub(SynapseHub(journal=store, verdict_gate_enabled=False)) as (_hub, uri):
        approver = await connect_agent(APPROVER, uri)
        try:
            await approver.agent.post_progress(ABSTRACT, _approve(), kind=APPROVAL_NOTE_KIND)
            await _posted(approver, _approve())
            plain = format_approval_note(subject="release-1.0", state="approved", reason="")
            await approver.agent.post_progress("release-1.0", plain, kind=APPROVAL_NOTE_KIND)
            await _posted(approver, plain)
        finally:
            await close_agents(approver)
    bypass = store.read_since(0, kinds=(EventKind.VERDICT_GATE_BYPASS,))
    assert [row.payload["subject"] for row in bypass] == [ABSTRACT]
    assert bypass[0].payload["gate_would_refuse"] is True
    assert bypass[0].payload["reasons"] == ["no verdict recorded"]
    bypass_receipt = universal_receipts_from_events(bypass)[0]
    assert (bypass_receipt.kind, bypass_receipt.status) == ("verdict-gate-bypass", "bypassed")
    store.close()
    async with running_hub() as (_hub, uri):
        writer = await connect_agent(WRITER, uri)
        try:
            body = format_evidence(EvidenceRecord(RAW, "LAB"))
            await writer.agent.post_progress("STUDY", body, kind=EVIDENCE_NOTE_KIND)
            await _error(writer, "need a durable journal")
            await writer.agent.post_progress(ABSTRACT, _approve(), kind=APPROVAL_NOTE_KIND)
            await _posted(writer, _approve())
        finally:
            await close_agents(writer)
