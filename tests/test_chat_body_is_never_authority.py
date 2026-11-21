# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a chat body is data, never an approval, a verdict or a declaration
"""A chat whose text is a canonical approval, verdict or evidence body records nothing.

Approvals, verdicts, evidence and corrections are read only from ``LEDGER_PROGRESS``
notes of their declared kind, authored by the bound sender and admitted through
their gate. A chat carries text; every reader of authority skips it by event kind,
whatever its body says and whatever extra fields its frame claims.

The scenario runs on a real hub with a durable journal:

* one seat sends the exact canonical bodies as a room chat and as a direct chat, and
  another sends them as raw chat frames that also claim ``kind``, ``text``,
  ``task_id`` and ``author`` fields;
* afterwards no reader of the journal sees an approval, a verdict, a declared object,
  an approver-kind row, an attention item or a receipt derived from those chats;
* the same bodies sent as typed progress notes by the proper seats are then seen by
  every one of those readers, so the empty answers above are not vacuous.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from websockets.asyncio.client import connect

from hub_e2e_helpers import close_agents, collect_available, connect_agent, running_hub, send_json
from synapse_channel.core.approvals import (
    APPROVAL_NOTE_KIND,
    build_approval_report,
    format_approval_note,
)
from synapse_channel.core.attention import project_hub_attention
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.operation_attempt_facts import current_approval
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.universal_receipts import universal_receipts_from_events
from synapse_channel.core.verdict_hub import VerdictHubPolicy, recorded_notes
from synapse_channel.core.verdict_records import (
    EVIDENCE_NOTE_KIND,
    VERDICT_NOTE_KIND,
    EvidenceRecord,
    VerdictRecord,
    format_evidence,
    format_verdict,
)
from synapse_channel.core.verdict_report import build_verdict_report

RAW = "1" * 64
ABSTRACT = "3" * 64
WRITER = "LAB/writer"
REVIEWER = "LAB/reviewer"
APPROVER = "LAB/approver"
MALLORY = "LAB/mallory"
FORGER = "LAB/forger"
POLICY = VerdictHubPolicy()

REQUEST = format_approval_note(subject=ABSTRACT, state="requested", reason="please sign")
APPROVAL = format_approval_note(subject=ABSTRACT, state="approved", reason="signed off")
EVIDENCE_RAW = format_evidence(EvidenceRecord(RAW, "LAB"))
EVIDENCE_ABSTRACT = format_evidence(EvidenceRecord(ABSTRACT, "LAB"))
VERDICT = format_verdict(VerdictRecord("LAB", ABSTRACT, "accepted", (RAW,), (), WRITER, 1))

# Every canonical authority body, paired with the note kind it would need.
BODIES = (
    (APPROVAL_NOTE_KIND, REQUEST),
    (APPROVAL_NOTE_KIND, APPROVAL),
    (EVIDENCE_NOTE_KIND, EVIDENCE_RAW),
    (EVIDENCE_NOTE_KIND, EVIDENCE_ABSTRACT),
    (VERDICT_NOTE_KIND, VERDICT),
)


async def _posted(seat: Any, text: str) -> None:
    await seat.recorder.wait_for(
        lambda m: m.get("type") == "ledger_progress_posted" and m["note"]["text"] == text
    )


async def _direct_chat_arrived(seat: Any, body: str) -> None:
    def arrived(message: dict[str, Any]) -> bool:
        return (
            message.get("type") == "chat"
            and message.get("target") == APPROVER
            and message.get("payload") == body
        )

    await seat.recorder.wait_for(arrived)


async def _send_forged_chats(uri: str) -> None:
    """Send each body as a raw chat frame that also claims the note fields."""
    async with connect(uri) as websocket:
        await send_json(websocket, type="heartbeat", sender=FORGER, target="System")
        await collect_available(websocket, 0.3)
        for kind, body in BODIES:
            await send_json(
                websocket,
                type=MessageType.CHAT,
                sender=FORGER,
                target=APPROVER,
                payload=body,
                kind=kind,
                text=body,
                task_id=ABSTRACT,
                author=APPROVER,
            )
        await collect_available(websocket, 0.5)


def _readings(store: EventStore) -> dict[str, Any]:
    """Return what every authority reader of the journal answers now."""
    events = tuple(store.iter_events())
    return {
        "approvals": build_approval_report(events).by_subject,
        "current": current_approval(store, POLICY, ABSTRACT),
        "notes": recorded_notes(store),
        "verdicts": build_verdict_report(events, POLICY)["verdicts"],
        "approver_kinds": [e for e in events if e.kind == EventKind.APPROVAL_KIND],
        "attention": [
            item
            for item in project_hub_attention(events, now=max(e.ts for e in events))
            if item.kind == "approval"
        ],
        "receipts": {r.seq: r.kind for r in universal_receipts_from_events(events)},
        "chats": [e for e in events if e.kind == EventKind.CHAT],
    }


async def test_chat_bodies_shaped_as_authority_notes_record_no_authority(tmp_path: Path) -> None:
    """Room, direct and forged-field chats record nothing; typed notes of the same text do."""
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store)) as (_hub, uri):
            mallory = await connect_agent(MALLORY, uri)
            approver = await connect_agent(APPROVER, uri)
            try:
                for kind, body in BODIES:
                    await mallory.agent.chat(body, metadata={"kind": kind, "text": body})
                    await mallory.agent.chat(body, target=APPROVER)
                    await _direct_chat_arrived(approver, body)
            finally:
                await close_agents(mallory)
            await _send_forged_chats(uri)

            before = _readings(store)

            writer = await connect_agent(WRITER, uri)
            reviewer = await connect_agent(REVIEWER, uri)
            try:
                for body in (EVIDENCE_RAW, EVIDENCE_ABSTRACT):
                    await writer.agent.post_progress("STUDY", body, kind=EVIDENCE_NOTE_KIND)
                    await _posted(writer, body)
                await reviewer.agent.post_progress("STUDY", VERDICT, kind=VERDICT_NOTE_KIND)
                await _posted(reviewer, VERDICT)
                for body in (REQUEST, APPROVAL):
                    await approver.agent.post_progress(ABSTRACT, body, kind=APPROVAL_NOTE_KIND)
                    await _posted(approver, body)
            finally:
                await close_agents(writer, reviewer, approver)
        after = _readings(store)
    finally:
        store.close()

    # The chats were journalled, body and all, as chats: data, kept verbatim.
    chat_bodies = [str(event.payload.get("payload")) for event in before["chats"]]
    assert len(before["chats"]) == 3 * len(BODIES)
    assert sorted(chat_bodies) == sorted(body for _kind, body in BODIES for _copy in range(3))
    chat_seqs = {event.seq for event in before["chats"]}
    # The forged frames' claimed note fields are journalled too, so the readers below
    # skip them by event kind, not because the claims are missing. If the hub ever
    # drops undeclared chat fields this line changes; the readers' answers must not.
    forged = [event.payload for event in before["chats"] if event.payload["sender"] == FORGER]
    assert [(row["kind"], row["text"], row["author"]) for row in forged] == [
        (kind, body, APPROVER) for kind, body in BODIES
    ]

    # Not one authority reader saw any of them.
    assert before["approvals"] == {}
    assert before["current"] is None
    assert before["notes"] == []
    assert before["verdicts"] == []
    assert before["approver_kinds"] == []
    assert before["attention"] == []
    assert set(before["receipts"]) & chat_seqs == set()

    # The same bodies as typed notes reach every reader, so the answers above are real.
    status = after["approvals"][ABSTRACT]
    assert (status.current_state, status.requested_by, status.decided_by) == (
        "approved",
        APPROVER,
        APPROVER,
    )
    current = after["current"]
    assert current is not None
    assert (current.approver, current.gate_reasons) == (APPROVER, ())
    assert [(note.author, note.kind) for note in after["notes"]] == [
        (WRITER, EVIDENCE_NOTE_KIND),
        (WRITER, EVIDENCE_NOTE_KIND),
        (REVIEWER, VERDICT_NOTE_KIND),
    ]
    assert [(row["reviewer"], row["outcome"]) for row in after["verdicts"]] == [
        (REVIEWER, "accepted")
    ]
    assert [row.payload["author"] for row in after["approver_kinds"]] == [APPROVER]
    assert [(item.subject, item.state) for item in after["attention"]] == [(ABSTRACT, "resolved")]
    assert set(after["receipts"]) & chat_seqs == set()
    assert "approval" in after["receipts"].values()
