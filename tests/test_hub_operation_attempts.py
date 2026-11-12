# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a live hub opens one attempt per fresh approval and journals every answer
"""A real hub answers requests to open an attempt, journals each answer, and opens once."""

from __future__ import annotations

import asyncio
import json
import re
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

import operation_attempt_scenario as scene
from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub
from synapse_channel.core.acl import CLAIM, Target
from synapse_channel.core.execution_outcome import OutcomeState
from synapse_channel.core.handlers import DISPATCH
from synapse_channel.core.handlers import operation_attempts as handler
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.operation_attempts import attempt_id
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.verdict_records import (
    CORRECTION_NOTE_KIND,
    CorrectionRecord,
    format_correction,
)


class _Seats:
    """The four connected seats of one scenario."""

    def __init__(self, handles: dict[str, AgentHandle]) -> None:
        self.worker = handles[scene.WORKER]
        self.reviewer = handles[scene.REVIEWER]
        self.approver = handles[scene.APPROVER]
        self.checker = handles[scene.CHECKER]


@asynccontextmanager
async def _seats(uri: str) -> AsyncIterator[_Seats]:
    names = (scene.WORKER, scene.REVIEWER, scene.APPROVER, scene.CHECKER)
    handles = {name: await connect_agent(name, uri) for name in names}
    try:
        yield _Seats(handles)
    finally:
        await close_agents(*handles.values())


async def _prepared(seats: _Seats, *, approve: bool = True) -> tuple[int, str]:
    """Claim, declare with an intent, review and (by default) approve; return epoch and digest."""
    epoch = await scene.claim(seats.worker)
    digest = await scene.declare(seats.worker, epoch, intent=scene.statement())
    await scene.review(seats.reviewer, digest)
    if approve:
        await scene.approve(seats.approver, digest)
    return epoch, digest


def _answers(store: EventStore) -> list[dict[str, Any]]:
    rows = store.read_since(0, kinds=(EventKind.OPERATION_ATTEMPT_ANSWERED,))
    return [dict(row.payload) for row in rows]


def _declared_deadline(store: EventStore) -> int:
    declared = store.read_since(0, kinds=(EventKind.OPERATION_DECLARED,))[0]
    deadline = declared.payload["intent"]["deadline"]
    assert isinstance(deadline, int)
    return deadline


def _stored(frame: dict[str, Any]) -> str:
    return json.dumps({k: v for k, v in frame.items() if k != "live"}, sort_keys=True)


async def test_a_fresh_approval_opens_one_attempt_and_every_answer_is_journalled(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=scene.policy())) as (_h, uri):
            async with _seats(uri) as seats:
                epoch, digest = await _prepared(seats, approve=False)
                early = await scene.attempt(seats.worker, epoch, digest, "k0")
                assert (early["answer"], early["payload"]) == (
                    "not_approved",
                    "the intent has no current approval",
                )
                assert early["live"] == {"attempt_state": "none", "replayed": False}
                await scene.approve(seats.approver, digest)
                opened = await scene.attempt(seats.worker, epoch, digest, "k1")
                second = await scene.attempt(seats.worker, epoch, digest, "k2")
        rows = _answers(store)
        answer_seqs = [
            row.seq for row in store.read_since(0, kinds=(EventKind.OPERATION_ATTEMPT_ANSWERED,))
        ]
    finally:
        store.close()
    approval_seq = opened["approval_seq"]
    assert isinstance(approval_seq, int)
    assert (opened["answer"], opened["payload"]) == ("approved_fresh", "attempt opened")
    assert opened["attempt_id"] == attempt_id(scene.OPERATION, approval_seq)
    assert opened["live"] == {"attempt_state": "open", "replayed": False}
    assert (second["answer"], second["payload"]) == (
        "attempt_exists",
        "an attempt of the operation is open",
    )
    assert [row["answer"] for row in rows] == ["not_approved", "approved_fresh", "attempt_exists"]
    assert [row["request_key"] for row in rows] == ["k0", "k1", "k2"]
    assert rows[1]["attempt_id"] == opened["attempt_id"]
    assert rows[1]["approval_seq"] == approval_seq
    assert "attempt_id" not in rows[2] and rows[2]["sender"] == scene.WORKER
    assert opened["seq"] == answer_seqs[1]


async def test_a_repeat_returns_the_stored_answer_byte_for_byte_and_the_live_state(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=scene.policy())) as (_h, uri):
            async with _seats(uri) as seats:
                epoch, digest = await _prepared(seats)
                first = await scene.attempt(seats.worker, epoch, digest, "k1")
                ended = await scene.record(
                    seats.worker, **scene.outcome(OutcomeState.CONFIRMED_DONE)
                )
                assert ended["status"] == "accepted"
                again = await scene.attempt(seats.worker, epoch, digest, "k1")
                conflict = await scene.answer(
                    seats.worker,
                    lambda: seats.worker.agent.send_message(
                        MessageType.OPERATION_ATTEMPT,
                        task_id=scene.TASK,
                        operation_id=scene.OPERATION,
                        epoch=epoch,
                        intent_sha256="f" * 64,
                        idem_key="k1",
                    ),
                    lambda frame: frame.get("error_code") == "idempotency_conflict",
                )
        rows = _answers(store)
    finally:
        store.close()
    assert first["live"] == {"attempt_state": "open", "replayed": False}
    assert _stored(again) == _stored(first)
    assert again["live"] == {"attempt_state": "ended", "replayed": True}
    assert conflict["payload"] == "Idempotency key was already used for a different request."
    assert [row["request_key"] for row in rows] == ["k1"]


async def test_only_the_executor_ends_and_only_another_sender_reconciles(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=scene.policy())) as (_h, uri):
            async with _seats(uri) as seats:
                epoch, digest = await _prepared(seats)
                first = await scene.attempt(seats.worker, epoch, digest, "k1")
                assert first["answer"] == "approved_fresh"
                checker_says = await scene.record(
                    seats.checker, **scene.outcome(OutcomeState.CONFIRMED_DONE)
                )
                assert checker_says["status"] == "accepted"
                still = await scene.attempt(seats.worker, epoch, digest, "k1")
                assert still["live"]["attempt_state"] == "open"
                declined = await scene.record(
                    seats.worker, **scene.outcome(OutcomeState.NOT_STARTED)
                )
                assert declined["status"] == "accepted"
                own_look = await scene.record(
                    seats.worker, **scene.verification("absent", "not_verified")
                )
                assert own_look["status"] == "accepted"
                consumed = await scene.attempt(seats.worker, epoch, digest, "k2")
                await scene.approve(seats.approver, digest, reason="second")
                waiting = await scene.attempt(seats.worker, epoch, digest, "k3")
                looked = await scene.record(
                    seats.checker, **scene.verification("absent", "not_verified")
                )
                assert looked["status"] == "accepted"
                reconciled = await scene.attempt(seats.worker, epoch, digest, "k1")
                fresh = await scene.attempt(seats.worker, epoch, digest, "k4")
    finally:
        store.close()
    assert (consumed["answer"], consumed["payload"]) == (
        "attempt_exists",
        "this approval has already admitted an attempt",
    )
    assert (waiting["answer"], waiting["payload"]) == (
        "earlier_attempt_unreconciled",
        "an ended attempt has no independent observation since its end",
    )
    assert reconciled["live"] == {"attempt_state": "reconciled", "replayed": True}
    assert fresh["answer"] == "approved_fresh"
    assert fresh["attempt_id"] != first["attempt_id"]


async def test_an_independently_verified_operation_is_closed(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=scene.policy())) as (_h, uri):
            async with _seats(uri) as seats:
                epoch, digest = await _prepared(seats)
                own = await scene.record(seats.worker, **scene.verification("observed", "verified"))
                assert own["status"] == "accepted"
                opened = await scene.attempt(seats.worker, epoch, digest, "k1")
                seen = await scene.record(
                    seats.checker, **scene.verification("observed", "verified")
                )
                assert seen["status"] == "accepted"
                replay = await scene.attempt(seats.worker, epoch, digest, "k1")
                closed = await scene.attempt(seats.worker, epoch, digest, "k2")
    finally:
        store.close()
    assert opened["answer"] == "approved_fresh"
    assert replay["live"]["attempt_state"] == "closed"
    assert (closed["answer"], closed["payload"]) == (
        "operation_closed",
        "an independent verification has verified the operation",
    )


async def test_requests_from_the_wrong_seat_or_for_the_wrong_intent_are_refused(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=scene.policy())) as (_h, uri):
            async with _seats(uri) as seats:
                epoch, digest = await _prepared(seats)
                plain = await scene.declare(seats.worker, epoch, operation_id="op-plain")
                answers = [
                    await scene.attempt(seats.checker, epoch, digest, "c1"),
                    await scene.attempt(seats.worker, epoch + 7, digest, "w1"),
                    await scene.attempt(seats.worker, epoch, digest, "w2", operation_id="op-9"),
                    await scene.attempt(seats.worker, epoch, digest, "w3", operation_id="op-plain"),
                    await scene.attempt(seats.worker, epoch, "f" * 64, "w4"),
                ]
        rows = _answers(store)
    finally:
        store.close()
    assert plain == ""
    assert [(item["answer"], item["payload"]) for item in answers] == [
        (
            "not_claim_holder",
            "the requester does not hold the task's live claim as the operation's declarer",
        ),
        ("stale_epoch", "the stated epoch is neither the claim's epoch nor its ownership epoch"),
        ("not_approved", "the operation is not declared on this task"),
        ("not_approved", "the operation was declared without an authorisation intent"),
        ("not_approved", "the named digest is not the operation's intent"),
    ]
    assert len(rows) == 5


async def test_the_approval_itself_must_be_fresh_in_time_and_from_another_seat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=scene.policy())) as (_h, uri):
            async with _seats(uri) as seats:
                epoch, digest = await _prepared(seats)
                deadline = _declared_deadline(store)
                monkeypatch.setattr(handler, "wall_clock", lambda: float(deadline))
                expired = await scene.attempt(seats.worker, epoch, digest, "k1")
                monkeypatch.setattr(handler, "wall_clock", lambda: 1.0)
                backwards = await scene.attempt(seats.worker, epoch, digest, "k2")
                monkeypatch.undo()
                await scene.approve(seats.worker, digest)
                own = await scene.attempt(seats.worker, epoch, digest, "k3")
                other_reviewer = await connect_agent("Q/reviewer", uri)
                stranger = await connect_agent("Q/approver", uri)
                try:
                    await scene.review(other_reviewer, digest, project="Q")
                    await scene.approve(stranger, digest)
                finally:
                    await close_agents(other_reviewer, stranger)
                foreign = await scene.attempt(seats.worker, epoch, digest, "k4")
                await scene.approve(seats.approver, digest, reason="again")
                fix = format_correction(
                    CorrectionRecord("P", scene.INPUT, "2" * 64, "input re-exported")
                )
                await scene.note(seats.reviewer, CORRECTION_NOTE_KIND, fix)
                stale = await scene.attempt(seats.worker, epoch, digest, "k5")
        rows = _answers(store)
    finally:
        store.close()
    assert (expired["answer"], expired["payload"]) == (
        "approval_expired",
        "the hub's clock is at or past the intent's deadline",
    )
    assert (backwards["answer"], backwards["payload"]) == (
        "unknown",
        "the hub's clock reads earlier than the declaration",
    )
    assert (own["answer"], own["payload"]) == (
        "approver_unauthorised",
        "the approver is the intent's actor",
    )
    assert (foreign["answer"], foreign["payload"]) == (
        "approver_unauthorised",
        "the approver is not of the actor's project",
    )
    assert stale["answer"] == "approval_stale"
    gate = (
        rf"approval gate: {scene.REVIEWER}: stale input, unacknowledged corrections [0-9a-f]{{64}}"
    )
    assert re.fullmatch(gate, stale["payload"])
    assert [row["answer"] for row in rows] == [
        "approval_expired",
        "unknown",
        "approver_unauthorised",
        "approver_unauthorised",
        "approval_stale",
    ]


async def test_a_gate_that_is_or_was_off_gives_unknown(tmp_path: Path) -> None:
    path = tmp_path / "hub.db"
    store = EventStore(path)
    off = SynapseHub(journal=store, acl_policy=scene.policy(), verdict_gate_enabled=False)
    try:
        async with running_hub(off) as (_h, uri):
            async with _seats(uri) as seats:
                epoch, digest = await _prepared(seats)
                gate_off = await scene.attempt(seats.worker, epoch, digest, "k1")
    finally:
        store.close()
    reopened = EventStore(path)
    try:
        async with running_hub(SynapseHub(journal=reopened, acl_policy=scene.policy())) as (
            _h,
            uri,
        ):
            worker = await connect_agent(scene.WORKER, uri)
            try:
                bypassed = await scene.attempt(worker, epoch, digest, "k2")
            finally:
                await close_agents(worker)
    finally:
        reopened.close()
    assert (gate_off["answer"], gate_off["payload"]) == ("unknown", "the hub's verdict gate is off")
    assert (bypassed["answer"], bypassed["payload"]) == (
        "unknown",
        "the approval came through a bypassed verdict gate",
    )


async def test_a_malformed_request_and_a_hub_without_a_journal_journal_nothing(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=scene.policy())) as (_h, uri):
            worker = await connect_agent(scene.WORKER, uri)
            try:
                malformed = await scene.answer(
                    worker,
                    lambda: worker.agent.send_message(
                        MessageType.OPERATION_ATTEMPT,
                        task_id=scene.TASK,
                        operation_id=scene.OPERATION,
                        epoch=1,
                        intent_sha256="d" * 64,
                    ),
                    lambda frame: frame.get("type") == MessageType.OPERATION_ATTEMPT_RESULT,
                )
            finally:
                await close_agents(worker)
        rows = _answers(store)
    finally:
        store.close()
    async with running_hub(SynapseHub()) as (_h, uri):
        worker = await connect_agent(scene.WORKER, uri)
        try:
            bare = await scene.attempt(worker, 1, "d" * 64, "k1")
        finally:
            await close_agents(worker)
    assert (malformed["answer"], malformed["payload"]) == (
        "invalid_request",
        "idem_key must be text of 1 to 256 characters",
    )
    assert malformed["live"] == {"attempt_state": "none", "replayed": False}
    assert (bare["answer"], bare["payload"]) == ("no_journal", "this hub keeps no journal")
    assert rows == []


async def test_a_verification_never_lands_between_the_reading_and_the_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A verification already past registration waits for the open's lock, never slips in.

    Every frame passes the hub's mutation lock when it registers, so the
    verification is held after that step and before its handler. The attempt
    then reads and holds its commit; the verification's handler runs meanwhile.
    Its row must come after the attempt's answer.
    """
    store = EventStore(tmp_path / "hub.db")
    entered, release = threading.Event(), threading.Event()
    commit = store.commit_operation

    def held_commit(**kwargs: Any) -> Any:
        kinds = [kind for kind, _ in kwargs["events"]]
        assert kinds == [EventKind.OPERATION_ATTEMPT_ANSWERED], kinds
        entered.set()
        release.wait(timeout=scene.WAIT)
        return commit(**kwargs)

    registered, go, running = asyncio.Event(), asyncio.Event(), asyncio.Event()
    serve_record = DISPATCH[MessageType.OPERATION_RECORD]

    async def held_record(hub: Any, sender: str, data: dict[str, Any], websocket: Any) -> None:
        if data.get("record") == "verification":
            registered.set()
            await go.wait()
            running.set()
        await serve_record(hub, sender, data, websocket)

    monkeypatch.setattr(store, "commit_operation", held_commit)
    monkeypatch.setitem(DISPATCH, MessageType.OPERATION_RECORD, held_record)
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=scene.policy())) as (_h, uri):
            async with _seats(uri) as seats:
                epoch, digest = await _prepared(seats)
                looked = asyncio.create_task(
                    scene.record(seats.checker, **scene.verification("observed", "verified"))
                )
                await asyncio.wait_for(registered.wait(), scene.WAIT)
                request = asyncio.create_task(scene.attempt(seats.worker, epoch, digest, "k1"))
                await asyncio.to_thread(entered.wait, scene.WAIT)
                go.set()
                await asyncio.wait_for(running.wait(), scene.WAIT)
                await asyncio.sleep(0.05)
                release.set()
                opened, verified = await request, await looked
    finally:
        release.set()
        store.close()
    assert opened["answer"] == "approved_fresh" and verified["status"] == "accepted"
    assert verified["seq"] > opened["seq"]


async def test_two_requests_at_once_open_one_attempt(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=scene.policy())) as (_h, uri):
            async with _seats(uri) as seats:
                epoch, digest = await _prepared(seats)
                both = await asyncio.gather(
                    scene.attempt(seats.worker, epoch, digest, "k1"),
                    scene.attempt(seats.worker, epoch, digest, "k2"),
                )
        rows = _answers(store)
    finally:
        store.close()
    assert sorted(item["answer"] for item in both) == ["approved_fresh", "attempt_exists"]
    assert sorted(row["answer"] for row in rows) == ["approved_fresh", "attempt_exists"]


async def test_a_restarted_hub_answers_from_its_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Restoring checks no time; the request after the restart is judged on the clock."""
    path = tmp_path / "hub.db"
    store = EventStore(path)
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=scene.policy())) as (_h, uri):
            async with _seats(uri) as seats:
                epoch, digest = await _prepared(seats)
                first = await scene.attempt(seats.worker, epoch, digest, "k1")
        deadline = _declared_deadline(store)
    finally:
        store.close()
    reopened = EventStore(path)
    try:
        async with running_hub(SynapseHub(journal=reopened, acl_policy=scene.policy())) as (
            _h,
            uri,
        ):
            worker = await connect_agent(scene.WORKER, uri)
            try:
                exists = await scene.attempt(worker, epoch, digest, "k2")
                repeated = await scene.attempt(worker, epoch, digest, "k1")
                monkeypatch.setattr(handler, "wall_clock", lambda: float(deadline + 1))
                expired = await scene.attempt(worker, epoch, digest, "k3")
            finally:
                await close_agents(worker)
    finally:
        reopened.close()
    assert first["answer"] == "approved_fresh"
    assert exists["answer"] == "attempt_exists"
    assert _stored(repeated) == _stored(first)
    assert repeated["live"] == {"attempt_state": "open", "replayed": True}
    assert expired["answer"] == "approval_expired"


def test_the_access_mapping_keeps_the_claim_rule_for_the_named_task() -> None:
    """Under an enforced policy the request needs the claim permission on its task."""
    assert handler.operation_attempt_access({"task_id": "T1"}) == [(CLAIM, Target("claim", "T1"))]
    assert handler.operation_attempt_access({}) == [(CLAIM, Target("claim", ""))]
