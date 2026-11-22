# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the approval-load policy on a real hub: refusals, recount, flags, receipts
"""On a real hub with a journal, the approval-load policy refuses, recounts and flags as declared.

The scenarios run end to end: agents post approval notes over the socket, and
the assertions read the hub's own journal back through the same readers an
auditor uses (approvals, attention, universal receipts).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.client import connect

from hub_e2e_helpers import (
    close_agents,
    collect_available,
    connect_agent,
    read_until_type,
    running_hub,
    send_json,
)
from synapse_channel.core.approval_load import ApprovalLoad
from synapse_channel.core.approval_load_flags import FLAG_BATCH_OVERSIZED, FLAG_FAST_DECISION
from synapse_channel.core.approval_load_policy import (
    ApprovalLoadPolicy,
    parse_approval_load_policy,
)
from synapse_channel.core.approvals import (
    APPROVAL_NOTE_KIND,
    approvals_to_json,
    build_approval_report,
    format_approval_note,
)
from synapse_channel.core.attention import project_hub_attention
from synapse_channel.core.handlers.approval_load_admission import NO_JOURNAL_REFUSAL
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.hub_config import HubAuthConfig, HubConfig
from synapse_channel.core.journal import (
    EventKind,
    record_approval_load_policy,
    record_ledger_progress_admitted,
)
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.role_grants import RoleGrants
from synapse_channel.core.universal_receipts import universal_receipts_from_events
from synapse_channel.core.verdict_records import VERDICT_NOTE_KIND

REQUESTER = "P/req"
OUTSIDER = "Q/req"
ALICE = "P/alice"
BOB = "P/bob"
CAROL = "P/carol"
BATCH = "c" * 64
SUMMARY = "5" * 64
GRANTS = RoleGrants({"P/person": frozenset({ALICE, BOB})})


def _policy() -> ApprovalLoadPolicy:
    return parse_approval_load_policy(
        {
            "schema": "synapse-approval-load-policy.v1",
            "roles": {
                "P/person": {
                    "request_rate": 2,
                    "window_seconds": 3600,
                    "min_review_seconds": 60,
                    "max_batch": 5,
                }
            },
            "approvers": {
                CAROL: {
                    "request_rate": 10,
                    "window_seconds": 3600,
                    "min_review_seconds": 0,
                    "max_batch": 2,
                }
            },
        }
    )


def _policy_hub(store: EventStore | None) -> SynapseHub:
    return SynapseHub(journal=store, role_grants=GRANTS, approval_load=ApprovalLoad(_policy()))


def _request(subject: str, **tokens: str) -> str:
    return format_approval_note(subject=subject, state="requested", **tokens)


async def _posted(seat: Any, text: str) -> None:
    await seat.recorder.wait_for(
        lambda m: m.get("type") == "ledger_progress_posted" and m["note"]["text"] == text
    )


async def _refused(seat: Any, fragment: str) -> None:
    await seat.recorder.wait_for(
        lambda m: m.get("type") == "error" and fragment in str(m.get("payload"))
    )


def _rows(store: EventStore, kind: str) -> list[dict[str, Any]]:
    return [dict(event.payload) for event in store.read_since(0, kinds=(kind,))]


async def test_without_a_policy_an_unaddressed_request_is_admitted_unchanged(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store)) as (_hub, uri):
            requester = await connect_agent(REQUESTER, uri)
            try:
                text = _request("S-0")
                await requester.agent.post_progress("T", text, kind=APPROVAL_NOTE_KIND)
                await _posted(requester, text)
            finally:
                await close_agents(requester)
        assert _rows(store, EventKind.APPROVAL_LOAD_POLICY) == []
        assert _rows(store, EventKind.APPROVAL_LOAD_ADMITTED) == []
        assert _rows(store, EventKind.VERDICT_RECORD_REFUSED) == []
    finally:
        store.close()


async def test_the_policy_refuses_counts_flags_and_survives_a_restart(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(_policy_hub(store)) as (_hub, uri):
            requester = await connect_agent(REQUESTER, uri)
            outsider = await connect_agent(OUTSIDER, uri)
            alice = await connect_agent(ALICE, uri)
            try:
                await requester.agent.post_progress("T", _request("S-u"), kind=APPROVAL_NOTE_KIND)
                await _refused(requester, "approval_request_unaddressed")
                malformed = _request("S-m").replace(
                    "state=requested", "state=requested approver=bad"
                )
                await requester.agent.post_progress("T", malformed, kind=APPROVAL_NOTE_KIND)
                await _refused(requester, "approval_request_malformed: approver must be")

                free = _request("S-q")
                await outsider.agent.post_progress("T", free, kind=APPROVAL_NOTE_KIND)
                await _posted(outsider, free)

                first = _request("S-1", approver=ALICE, batch=BATCH, summary=SUMMARY)
                second = _request("S-2", approver=BOB)
                for text in (first, second):
                    await requester.agent.post_progress("T", text, kind=APPROVAL_NOTE_KIND)
                    await _posted(requester, text)
                await requester.agent.post_progress(
                    "T", _request("S-3", approver=ALICE), kind=APPROVAL_NOTE_KIND
                )
                await _refused(
                    requester, "approval_rate_exceeded: role P/person allows 2 requests per 3600 s"
                )

                decision = format_approval_note(subject="S-1", state="approved")
                await alice.agent.post_progress("T", decision, kind=APPROVAL_NOTE_KIND)
                await _posted(alice, decision)

                for index in range(3):
                    text = _request(f"S-c{index}", approver=CAROL, batch=BATCH)
                    await requester.agent.post_progress("T", text, kind=APPROVAL_NOTE_KIND)
                    await _posted(requester, text)
            finally:
                await close_agents(requester, outsider, alice)

        refused = _rows(store, EventKind.VERDICT_RECORD_REFUSED)
        assert [(row["author"], row["kind"], row["reason"].split(":")[0]) for row in refused] == [
            (REQUESTER, APPROVAL_NOTE_KIND, "approval_request_unaddressed"),
            (REQUESTER, APPROVAL_NOTE_KIND, "approval_request_malformed"),
            (REQUESTER, APPROVAL_NOTE_KIND, "approval_rate_exceeded"),
        ]
        assert all("text" not in row for row in refused)
        policies = _rows(store, EventKind.APPROVAL_LOAD_POLICY)
        assert [row["policy_sha256"] for row in policies] == [_policy().sha256]
        admitted = _rows(store, EventKind.APPROVAL_LOAD_ADMITTED)
        assert [row["subject"] for row in admitted] == ["S-1", "S-2", "S-c0", "S-c1", "S-c2"]
        assert admitted[0]["limits"] == [["role", "P/person"]]
        assert admitted[0]["summary"] == SUMMARY
        assert (admitted[0]["min_review_seconds"], admitted[0]["max_batch"]) == (60.0, 5)
        assert admitted[2]["limits"] == [["approver", CAROL]]

        events = tuple(store.iter_events())
        flagged = {
            (item.kind, item.subject)
            for item in project_hub_attention(events, now=max(event.ts for event in events))
            if item.kind in {FLAG_FAST_DECISION, FLAG_BATCH_OVERSIZED}
        }
        assert flagged == {
            (FLAG_FAST_DECISION, "S-1"),
            (FLAG_BATCH_OVERSIZED, "S-c0"),
            (FLAG_BATCH_OVERSIZED, "S-c1"),
            (FLAG_BATCH_OVERSIZED, "S-c2"),
        }
        receipts = [
            receipt.payload
            for receipt in universal_receipts_from_events(events)
            if receipt.kind == "approval" and receipt.subject == "S-1"
        ]
        assert receipts[0]["approver"] == ALICE
        assert receipts[0]["batch"] == BATCH
        statuses = approvals_to_json(build_approval_report(events))["statuses"]
        assert isinstance(statuses, list)
        first_request = next(row for row in statuses if row["subject"] == "S-2")
        assert first_request["history"][0]["approver"] == BOB

        async with running_hub(_policy_hub(store)) as (_hub, uri):
            requester = await connect_agent(REQUESTER, uri)
            try:
                await requester.agent.post_progress(
                    "T", _request("S-4", approver=BOB), kind=APPROVAL_NOTE_KIND
                )
                await _refused(requester, "approval_rate_exceeded: role P/person")
            finally:
                await close_agents(requester)
        assert len(_rows(store, EventKind.APPROVAL_LOAD_POLICY)) == 2
    finally:
        store.close()


async def test_a_policy_without_a_journal_refuses_requests(tmp_path: Path) -> None:
    async with running_hub(_policy_hub(None)) as (_hub, uri):
        requester = await connect_agent(REQUESTER, uri)
        try:
            await requester.agent.post_progress(
                "T", _request("S-1", approver=ALICE), kind=APPROVAL_NOTE_KIND
            )
            await _refused(requester, NO_JOURNAL_REFUSAL)
            await requester.agent.post_progress("T", "verdict body", kind=VERDICT_NOTE_KIND)
            await _refused(requester, "need a durable journal")
            decision = format_approval_note(subject="S-1", state="approved")
            await requester.agent.post_progress("T", decision, kind=APPROVAL_NOTE_KIND)
            await _posted(requester, decision)
        finally:
            await close_agents(requester)


async def test_a_request_lost_to_a_journal_failure_gives_its_place_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = EventStore(tmp_path / "hub.db")
    failures = {"left": 0}

    def failing_record(journal: EventStore, note: Any, admitted: Any) -> None:
        if failures["left"]:
            failures["left"] -= 1
            raise sqlite3.OperationalError("disk I/O error")
        record_ledger_progress_admitted(journal, note, admitted)

    monkeypatch.setattr(
        "synapse_channel.core.handlers.planning.record_ledger_progress_admitted", failing_record
    )
    try:
        async with running_hub(_policy_hub(store)) as (_hub, uri):
            requester = await connect_agent(REQUESTER, uri)
            try:
                first = _request("S-1", approver=ALICE)
                await requester.agent.post_progress("T", first, kind=APPROVAL_NOTE_KIND)
                await _posted(requester, first)
                failures["left"] = 1
                await requester.agent.post_progress(
                    "T", _request("S-lost", approver=ALICE), kind=APPROVAL_NOTE_KIND
                )
                await _refused(requester, "rolled back")
                second = _request("S-2", approver=ALICE)
                await requester.agent.post_progress("T", second, kind=APPROVAL_NOTE_KIND)
                await _posted(requester, second)
                await requester.agent.post_progress(
                    "T", _request("S-3", approver=ALICE), kind=APPROVAL_NOTE_KIND
                )
                await _refused(requester, "approval_rate_exceeded")
            finally:
                await close_agents(requester)
        admitted = [row["subject"] for row in _rows(store, EventKind.APPROVAL_LOAD_ADMITTED)]
        assert admitted == ["S-1", "S-2"]
    finally:
        store.close()


def test_a_hub_refuses_an_approval_load_value_that_is_not_one() -> None:
    """The record holds the field as ``object``; normalisation enforces its type, fail-closed."""
    not_a_load = "a policy path is not an approval load"
    with pytest.raises(TypeError, match="approval_load must be an ApprovalLoad or None"):
        SynapseHub(HubConfig(auth=HubAuthConfig(approval_load=not_a_load)))
    assert SynapseHub(HubConfig()).approval_load is None
    load = ApprovalLoad(_policy())
    assert SynapseHub(HubConfig(auth=HubAuthConfig(approval_load=load))).approval_load is load


def _note_and_row_pairs(store: EventStore) -> list[tuple[str, int, int]]:
    """Return (subject, note seq, admitted-row seq) for each admitted request, in order."""
    notes = {
        event.seq: str(event.payload.get("text", ""))
        for event in store.read_since(0, kinds=(EventKind.LEDGER_PROGRESS,))
    }
    pairs = []
    for row in store.read_since(0, kinds=(EventKind.APPROVAL_LOAD_ADMITTED,)):
        note_seq = row.seq - 1
        assert f"subject={row.payload['subject']} " in notes[note_seq]
        pairs.append((str(row.payload["subject"]), note_seq, row.seq))
    return pairs


async def test_the_admitted_row_is_written_with_its_note_on_both_paths(tmp_path: Path) -> None:
    """Keyed or not, the note and its admitted row are one transaction; a replay counts nothing."""
    store = EventStore(tmp_path / "hub.db")
    keyed_sender = "P/keyed"
    try:
        async with running_hub(_policy_hub(store)) as (_hub, uri):
            requester = await connect_agent(REQUESTER, uri)
            try:
                plain = _request("S-plain", approver=ALICE)
                await requester.agent.post_progress("T", plain, kind=APPROVAL_NOTE_KIND)
                await _posted(requester, plain)
                frame = {
                    "type": "ledger_progress",
                    "sender": keyed_sender,
                    "target": "System",
                    "task_id": "T",
                    "kind": APPROVAL_NOTE_KIND,
                    "text": _request("S-keyed", approver=ALICE),
                    "idem_key": "approval-keyed-1",
                }
                async with connect(uri) as websocket:
                    await send_json(
                        websocket, type="heartbeat", sender=keyed_sender, target="System"
                    )
                    await collect_available(websocket, 0.3)
                    await send_json(websocket, **frame)
                    first = await read_until_type(websocket, "ledger_progress_posted")
                    await send_json(websocket, **frame)
                    replay = await read_until_type(websocket, "ledger_progress_posted")
                assert replay["note"] == first["note"]
                await requester.agent.post_progress(
                    "T", _request("S-full", approver=ALICE), kind=APPROVAL_NOTE_KIND
                )
                await _refused(requester, "approval_rate_exceeded")
            finally:
                await close_agents(requester)
        assert [subject for subject, _, _ in _note_and_row_pairs(store)] == ["S-plain", "S-keyed"]
    finally:
        store.close()


async def test_a_policy_row_lost_to_a_journal_failure_counts_nothing_and_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first policy row fails: the sender is answered, and the next request starts over."""
    store = EventStore(tmp_path / "hub.db")
    failures = {"left": 1}

    def failing_policy(journal: EventStore, record: Any) -> int:
        if failures["left"]:
            failures["left"] -= 1
            raise sqlite3.OperationalError("disk I/O error")
        return record_approval_load_policy(journal, record)

    monkeypatch.setattr(
        "synapse_channel.core.approval_load.record_approval_load_policy", failing_policy
    )
    try:
        async with running_hub(_policy_hub(store)) as (_hub, uri):
            requester = await connect_agent(REQUESTER, uri)
            try:
                await requester.agent.post_progress(
                    "T", _request("S-lost", approver=ALICE), kind=APPROVAL_NOTE_KIND
                )
                await _refused(requester, "Approval request was not journalled")
                kept = _request("S-kept", approver=ALICE)
                await requester.agent.post_progress("T", kept, kind=APPROVAL_NOTE_KIND)
                await _posted(requester, kept)
            finally:
                await close_agents(requester)
        assert len(_rows(store, EventKind.APPROVAL_LOAD_POLICY)) == 1
        assert [subject for subject, _, _ in _note_and_row_pairs(store)] == ["S-kept"]
    finally:
        store.close()
