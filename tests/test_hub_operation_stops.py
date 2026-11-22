# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a live hub stops and resumes scopes and journals every answer
"""A real hub stops a scope, lists its open attempts, answers ``stopped``, and resumes it."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

import operation_attempt_scenario as scene
from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub
from synapse_channel.core.acl import (
    OPERATION_RESUME,
    OPERATION_STOP,
    AclPolicy,
    AclRule,
)
from synapse_channel.core.approvals import APPROVAL_NOTE_KIND, format_approval_note
from synapse_channel.core.authorisation_intent import compose_intent
from synapse_channel.core.compaction import RetentionPolicy, compact
from synapse_channel.core.handlers import DISPATCH
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.journal_export import export_store
from synapse_channel.core.operation_attempt_ledger import AttemptLedger, OpenAttempt
from synapse_channel.core.operation_attempts import OperationFact
from synapse_channel.core.operation_records import decode_operation_record
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType

OPERATOR = "H/operator"
"""A person principal: namespace ``H`` holds ``operation-resume`` on every scope."""

STOP_KINDS = (
    EventKind.OPERATION_STOPPED,
    EventKind.OPERATION_RESUMED,
    EventKind.OPERATION_STOP_ANSWERED,
)


def _policy(*extra: AclRule) -> AclPolicy:
    rules = [*scene.policy().rules, AclRule(OPERATION_RESUME, "stop-scope", "*", "H", "persons")]
    return AclPolicy([*rules, *extra])


class _Seats:
    """The connected seats of one scenario: the four of the attempt scenario and a person."""

    def __init__(self, handles: dict[str, AgentHandle]) -> None:
        self.worker = handles[scene.WORKER]
        self.reviewer = handles[scene.REVIEWER]
        self.approver = handles[scene.APPROVER]
        self.checker = handles[scene.CHECKER]
        self.operator = handles[OPERATOR]


@asynccontextmanager
async def _seats(uri: str) -> AsyncIterator[_Seats]:
    names = (scene.WORKER, scene.REVIEWER, scene.APPROVER, scene.CHECKER, OPERATOR)
    handles = {name: await connect_agent(name, uri) for name in names}
    try:
        yield _Seats(handles)
    finally:
        await close_agents(*handles.values())


async def _prepared(seats: _Seats, *, approve: bool = True) -> tuple[int, str]:
    """Claim, declare a file operation with an intent, review and approve it."""
    epoch = await scene.claim(seats.worker)
    digest = await scene.declare(seats.worker, epoch, intent=scene.statement())
    await scene.review(seats.reviewer, digest)
    if approve:
        await scene.approve(seats.approver, digest)
    return epoch, digest


async def _ask(handle: AgentHandle, message_type: str, key: str, **fields: Any) -> dict[str, Any]:
    return await scene.answer(
        handle,
        lambda: handle.agent.send_message(message_type, idem_key=key, **fields),
        lambda frame: (
            frame.get("type") == MessageType.OPERATION_STOP_RESULT
            and frame.get("request_key") == key
        ),
    )


async def _stop(
    handle: AgentHandle, scope: str, key: str, reason: str = "stop it"
) -> dict[str, Any]:
    return await _ask(handle, MessageType.OPERATION_STOP, key, scope=scope, reason=reason)


async def _resume(
    handle: AgentHandle, scope: str, key: str, case: str = "another_person"
) -> dict[str, Any]:
    return await _ask(
        handle, MessageType.OPERATION_RESUME, key, scope=scope, reason="all clear", case=case
    )


async def _state(handle: AgentHandle) -> dict[str, Any]:
    return await scene.answer(
        handle,
        lambda: handle.agent.send_message(
            MessageType.OPERATION_STATE,
            task_id=scene.TASK,
            operation_id=scene.OPERATION,
            request_id="r1",
        ),
        lambda frame: frame.get("type") == MessageType.OPERATION_STATE_RESULT,
    )


async def _approval_refused(handle: AgentHandle, digest: str, reason: str) -> str:
    """Post an approved note that the hub must refuse, and return the refusal's text."""
    text = format_approval_note(subject=f"sha256:{digest}", state="approved", reason=reason)
    refusal = await scene.answer(
        handle,
        lambda: handle.agent.post_progress(scene.TASK, text, kind=APPROVAL_NOTE_KIND),
        lambda frame: frame.get("type") == MessageType.ERROR,
    )
    return str(refusal["payload"])


def _rows(store: EventStore, *kinds: str) -> list[tuple[int, str, dict[str, Any]]]:
    return [
        (row.seq, row.kind, dict(row.payload))
        for row in store.read_since(0, kinds=kinds or STOP_KINDS)
    ]


async def test_a_stop_lists_the_open_attempt_and_every_later_request_is_stopped(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=_policy())) as (_h, uri):
            async with _seats(uri) as seats:
                epoch, digest = await _prepared(seats)
                opened = await scene.attempt(seats.worker, epoch, digest, "k1")
                stop = await _stop(seats.operator, "project:P", "s1")
                stopped = await scene.attempt(seats.worker, epoch, digest, "k2")
                stranger = await scene.attempt(seats.checker, epoch, digest, "k3")
                state = await _state(seats.worker)
        rows = _rows(store)
        answers = _rows(store, EventKind.OPERATION_ATTEMPT_ANSWERED)
    finally:
        store.close()
    assert opened["answer"] == "approved_fresh"
    listed = [
        {
            "operation_id": scene.OPERATION,
            "attempt_id": opened["attempt_id"],
            "task_id": scene.TASK,
            "executor": scene.WORKER,
        }
    ]
    assert (stop["answer"], stop["epoch"], stop["listed"]) == ("stop_recorded", 1, listed)
    assert stop["payload"] == "the scope project:P is stopped; 1 open attempt(s) listed"
    assert "warning" not in stop and stop["live"] == {"replayed": False}
    seq = stop["seq"]
    sentence = f"the scope project:P is stopped (journal seq {seq})"
    assert (stopped["answer"], stopped["payload"]) == ("stopped", sentence)
    assert (stranger["answer"], stranger["payload"]) == ("stopped", sentence)
    by = state["stopped_by"]
    assert (by["scope"], by["seq"], by["state"], by["profile"]) == (
        "project:P",
        seq,
        "stop requested",
        "cooperative",
    )
    assert by["blocking"] == [
        {
            "operation_id": scene.OPERATION,
            "attempt_id": opened["attempt_id"],
            "reason": "unknown: no marker read back",
        }
    ]
    assert by["counts"] == {
        "listed": 1,
        "sealed": 0,
        "applied": 0,
        "unsealable": 0,
        "unread": 0,
        "reconciled": 0,
    }
    assert rows == [
        (
            seq,
            EventKind.OPERATION_STOPPED,
            {
                "scope": "project:P",
                "reason": "stop it",
                "hub_time": stop["hub_time"],
                "request_key": "s1",
                "stopper": OPERATOR,
                "epoch": 1,
                "listed": listed,
            },
        )
    ]
    assert [row[2]["answer"] for row in answers] == ["approved_fresh", "stopped", "stopped"]


async def test_an_approval_from_before_the_stop_is_stale_after_the_resume_and_a_new_one_opens(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=_policy())) as (_h, uri):
            async with _seats(uri) as seats:
                epoch, digest = await _prepared(seats)
                await _stop(seats.operator, "project:P", "s1")
                resumed = await _resume(seats.operator, "project:P", "r1")
                stale = await scene.attempt(seats.worker, epoch, digest, "k1")
                await scene.approve(seats.approver, digest, reason="after the resume")
                fresh = await scene.attempt(seats.worker, epoch, digest, "k2")
        resume_rows = _rows(store, EventKind.OPERATION_RESUMED)
    finally:
        store.close()
    assert resumed["answer"] == "resumed"
    assert (resumed["resumed_from"], resumed["case"]) == ("at rest", "another_person")
    line = resumed["seq"]
    assert (stale["answer"], stale["payload"]) == (
        "approval_stale",
        f"the approval is older than the latest stop or resume of scope project:P "
        f"(journal seq {line})",
    )
    assert fresh["answer"] == "approved_fresh" and fresh["approval_seq"] > line
    assert [(seq, row["lifts_seq"], row["resumer"]) for seq, _kind, row in resume_rows] == [
        (line, resumed["lifts_seq"], OPERATOR)
    ]


@pytest.mark.parametrize("gate_on", [True, False])
async def test_no_approval_is_admitted_while_the_scope_is_stopped_but_a_rejection_is(
    tmp_path: Path, gate_on: bool
) -> None:
    store = EventStore(tmp_path / "hub.db")
    hub = SynapseHub(journal=store, acl_policy=_policy(), verdict_gate_enabled=gate_on)
    try:
        async with running_hub(hub) as (_h, uri):
            async with _seats(uri) as seats:
                _epoch, digest = await _prepared(seats, approve=False)
                stop = await _stop(seats.operator, f"operation:{scene.OPERATION}", "s1")
                refusal = await _approval_refused(seats.approver, digest, "during the stop")
                rejection = format_approval_note(subject=f"sha256:{digest}", state="rejected")
                await scene.note(seats.approver, APPROVAL_NOTE_KIND, rejection)
        refused = _rows(store, EventKind.VERDICT_RECORD_REFUSED)
    finally:
        store.close()
    reason = (
        f"approval gate: scope operation:{scene.OPERATION} is stopped (journal seq {stop['seq']})"
    )
    assert refusal == reason
    assert [row[2] for row in refused] == [
        {
            "author": scene.APPROVER,
            "kind": APPROVAL_NOTE_KIND,
            "task_id": scene.TASK,
            "reason": reason,
        }
    ]


def _digest_before_declaring(operation_id: str, statement: dict[str, Any]) -> str:
    """Return the digest the hub will keep for a file declaration by the worker."""
    request = decode_operation_record(
        {
            "record": "declare",
            "task_id": scene.TASK,
            "operation_id": operation_id,
            "epoch": 1,
            "effect": "file",
            "effect_target": {"path": "out/result.txt", "sha256": scene.TARGET_SHA},
            "intent": statement,
        }
    )
    assert request.intent is not None and request.effect is not None
    return compose_intent(
        request.intent,
        task_id=scene.TASK,
        operation_id=operation_id,
        effect=request.effect,
        target=request.target,
        actor=scene.WORKER,
    ).sha256


async def test_an_approval_of_an_undeclared_digest_is_admitted_and_stale_after_the_resume(
    tmp_path: Path,
) -> None:
    """The gate cannot place an undeclared digest in a scope; the line still refuses it."""
    statement = scene.statement()
    digest = _digest_before_declaring("op-2", statement)
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=_policy())) as (_h, uri):
            async with _seats(uri) as seats:
                epoch = await scene.claim(seats.worker)
                await _stop(seats.operator, "project:P", "s1")
                await scene.review(seats.reviewer, digest)
                await scene.approve(seats.approver, digest)
                resumed = await _resume(seats.operator, "project:P", "r1")
                kept = await scene.declare(
                    seats.worker, epoch, operation_id="op-2", intent=statement
                )
                stale = await scene.attempt(seats.worker, epoch, digest, "k1", operation_id="op-2")
    finally:
        store.close()
    assert kept == digest
    assert stale["answer"] == "approval_stale"
    assert stale["payload"].endswith(f"scope project:P (journal seq {resumed['seq']})")


async def test_a_file_operation_is_in_effect_file_and_not_in_effect_git_publication(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=_policy())) as (_h, uri):
            async with _seats(uri) as seats:
                epoch, digest = await _prepared(seats)
                await _stop(seats.operator, "effect:git_publication", "s1")
                opened = await scene.attempt(seats.worker, epoch, digest, "k1")
                stop = await _stop(seats.operator, "effect:file", "s2")
                stopped = await scene.attempt(seats.worker, epoch, digest, "k2")
    finally:
        store.close()
    assert opened["answer"] == "approved_fresh"
    assert [item["attempt_id"] for item in stop["listed"]] == [opened["attempt_id"]]
    assert stopped["payload"] == f"the scope effect:file is stopped (journal seq {stop['seq']})"


async def test_installation_covers_an_undeclared_id_and_an_operation_resume_leaves_its_project(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=_policy())) as (_h, uri):
            async with _seats(uri) as seats:
                epoch, digest = await _prepared(seats)
                await _stop(seats.operator, "project:Q", "s0")
                undeclared_before = await scene.attempt(
                    seats.worker, epoch, digest, "k0", operation_id="op-none"
                )
                everything = await _stop(seats.operator, "installation", "s1")
                undeclared = await scene.attempt(
                    seats.worker, epoch, digest, "k1", operation_id="op-none"
                )
                broad = await scene.attempt(seats.worker, epoch, digest, "k2")
                project = await _stop(seats.operator, "project:P", "s2")
                await _stop(seats.operator, f"operation:{scene.OPERATION}", "s3")
                narrow = await scene.attempt(seats.worker, epoch, digest, "k3")
                await _resume(seats.operator, f"operation:{scene.OPERATION}", "r3")
                after = await scene.attempt(seats.worker, epoch, digest, "k4")
    finally:
        store.close()
    assert undeclared_before["answer"] == "not_approved"
    for answer, stop in ((undeclared, everything), (broad, everything), (after, project)):
        assert answer["answer"] == "stopped"
        assert answer["payload"].endswith(f"is stopped (journal seq {stop['seq']})")
    assert broad["payload"].startswith("the scope installation is stopped")
    assert narrow["payload"].startswith(f"the scope operation:{scene.OPERATION} is stopped")
    assert after["payload"].startswith("the scope project:P is stopped")


async def test_every_answer_is_a_journalled_row_and_a_repeat_returns_the_stored_answer(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "hub.db")
    resumer = AclRule(OPERATION_RESUME, "stop-scope", "project:*", "P", "the project resumes")
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=_policy(resumer))) as (_h, uri):
            async with _seats(uri) as seats:
                epoch, digest = await _prepared(seats)
                await scene.attempt(seats.worker, epoch, digest, "k1")
                answers = [
                    await _resume(seats.operator, "project:P", "a1"),
                    await _stop(seats.worker, "project:P", "a2"),
                    await _stop(seats.operator, "project:P", "a3"),
                    await _resume(seats.checker, "project:P", "a4"),
                    await _resume(seats.worker, "project:P", "a5"),
                    await _resume(seats.approver, "project:P", "a6", case="only_person"),
                ]
                repeat = await _resume(seats.approver, "project:P", "a6", case="only_person")
                changed = await scene.answer(
                    seats.approver,
                    lambda: seats.approver.agent.send_message(
                        MessageType.OPERATION_RESUME,
                        idem_key="a6",
                        scope="project:P",
                        reason="another reason",
                        case="only_person",
                    ),
                    lambda frame: frame.get("error_code") == "idempotency_conflict",
                )
        rows = _rows(store)
    finally:
        store.close()
    assert [item["answer"] for item in answers] == [
        "not_stopped",
        "stop_recorded",
        "already_stopped",
        "not_permitted",
        "actor_of_scope",
        "resumed",
    ]
    assert [(seq, kind, row["request_key"]) for seq, kind, row in rows] == [
        (item["seq"], kind, item["request_key"])
        for item, kind in zip(
            answers,
            (
                EventKind.OPERATION_STOP_ANSWERED,
                EventKind.OPERATION_STOPPED,
                EventKind.OPERATION_STOP_ANSWERED,
                EventKind.OPERATION_STOP_ANSWERED,
                EventKind.OPERATION_STOP_ANSWERED,
                EventKind.OPERATION_RESUMED,
            ),
            strict=True,
        )
    ]
    stopped_seq = answers[1]["seq"]
    assert answers[2]["standing_seq"] == stopped_seq and answers[5]["lifts_seq"] == stopped_seq
    assert answers[3]["payload"] == "Q/checker holds no operation-resume on project:P"
    assert answers[4]["payload"] == "P/worker declared an operation the stop of project:P covers"
    assert rows[-1][2]["case"] == "only_person"
    assert {key: value for key, value in repeat.items() if key not in ("live", "timestamp")} == {
        key: value for key, value in answers[5].items() if key not in ("live", "timestamp")
    }
    assert repeat["live"] == {"replayed": True}
    assert changed["error_code"] == "idempotency_conflict"


async def test_a_malformed_request_and_a_hub_without_a_journal_journal_nothing(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=_policy())) as (_h, uri):
            async with _seats(uri) as seats:
                malformed = await _stop(seats.operator, "project:a/b", "m1")
                no_reason = await _ask(
                    seats.operator, MessageType.OPERATION_RESUME, "m2", scope="installation"
                )
        rows = _rows(store)
    finally:
        store.close()
    async with running_hub(SynapseHub(acl_policy=_policy())) as (_h, uri):
        operator = await connect_agent(OPERATOR, uri)
        try:
            no_journal = await _stop(operator, "installation", "n1")
        finally:
            await close_agents(operator)
    assert (malformed["answer"], malformed["scope"]) == ("invalid_request", "")
    assert malformed["payload"].startswith("scope must be installation")
    assert no_reason["answer"] == "invalid_request"
    assert (no_journal["answer"], no_journal["scope"]) == ("no_journal", "installation")
    assert rows == []


async def test_an_open_hub_lets_anyone_stop_and_only_a_restart_with_an_access_file_resumes(
    tmp_path: Path,
) -> None:
    """With no access file nobody holds operation-resume; the stop answer says so."""
    path = tmp_path / "hub.db"
    store = EventStore(path)
    try:
        async with running_hub(SynapseHub(journal=store)) as (_h, uri):
            worker = await connect_agent(scene.WORKER, uri)
            operator = await connect_agent(OPERATOR, uri)
            try:
                stop = await _stop(worker, "installation", "s1")
                refused = await _resume(operator, "installation", "r1")
            finally:
                await close_agents(worker, operator)
    finally:
        store.close()
    store = EventStore(path)
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=_policy())) as (_h, uri):
            operator = await connect_agent(OPERATOR, uri)
            try:
                resumed = await _resume(operator, "installation", "r2", case="only_person")
            finally:
                await close_agents(operator)
    finally:
        store.close()
    assert stop["answer"] == "stop_recorded"
    assert stop["warning"] == (
        "this hub has no access file: lifting the stop needs a restart with an access file "
        "that grants operation-resume on the scope"
    )
    assert refused["answer"] == "not_permitted"
    assert (resumed["answer"], resumed["lifts_seq"]) == ("resumed", stop["seq"])


async def _denied(handle: AgentHandle, message_type: str, **fields: Any) -> str:
    """Send a request the access gate must refuse, and return the refusal's text."""
    denial = await scene.answer(
        handle,
        lambda: handle.agent.send_message(message_type, **fields),
        lambda frame: frame.get("type") == MessageType.ERROR and "acl_reason" in frame,
    )
    return str(denial["payload"])


async def test_an_enforcing_hub_refuses_a_stop_or_resume_without_the_grant_at_its_gate(
    tmp_path: Path,
) -> None:
    """The gate refuses before the verb and journals nothing; the grant admits the stop."""
    store = EventStore(tmp_path / "hub.db")
    stop_rule = AclRule(OPERATION_STOP, "stop-scope", "project:*", "H", "persons stop projects")
    try:
        async with running_hub(
            SynapseHub(journal=store, acl_policy=_policy(stop_rule), require_acl=True)
        ) as (_h, uri):
            worker = await connect_agent(scene.WORKER, uri)
            operator = await connect_agent(OPERATOR, uri)
            try:
                stop_fields = {"scope": "project:P", "reason": "r", "idem_key": "s1"}
                refused = await _denied(worker, MessageType.OPERATION_STOP, **stop_fields)
                outside = await _denied(
                    operator,
                    MessageType.OPERATION_STOP,
                    scope="installation",
                    reason="r",
                    idem_key="s2",
                )
                stop = await _stop(operator, "project:P", "s3")
                resume_fields = {**stop_fields, "case": "only_person", "idem_key": "r1"}
                lifted = await _denied(worker, MessageType.OPERATION_RESUME, **resume_fields)
            finally:
                await close_agents(worker, operator)
        rows = _rows(store)
    finally:
        store.close()
    assert refused == "access denied: operation-stop on stop-scope:project:P"
    assert outside == "access denied: operation-stop on stop-scope:installation"
    assert lifted == "access denied: operation-resume on stop-scope:project:P"
    assert stop["answer"] == "stop_recorded" and "warning" not in stop
    assert [kind for _seq, kind, _row in rows] == [EventKind.OPERATION_STOPPED]


async def test_a_stop_survives_compaction_a_restart_and_the_export(tmp_path: Path) -> None:
    """Retention removes checkpoints, findings and corrupt rows only; the stop stays standing."""
    path = tmp_path / "hub.db"
    store = EventStore(path)
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=_policy())) as (_h, uri):
            async with _seats(uri) as seats:
                epoch, digest = await _prepared(seats)
                first = await _stop(seats.operator, "project:P", "s1")
                resumed = await _resume(seats.operator, "project:P", "r1")
                second = await _stop(seats.operator, "project:P", "s2")
        policy = RetentionPolicy(
            max_checkpoints_per_task=1, finding_grace_seconds=0.0, drop_corrupt_rows=True
        )
        compact(store, policy, floor_seq=store.max_seq())
    finally:
        store.close()
    store = EventStore(path)
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=_policy())) as (_h, uri):
            async with _seats(uri) as seats:
                stopped = await scene.attempt(seats.worker, epoch, digest, "k1")
                again = await _stop(seats.operator, "project:P", "s3")
                lifted = await _resume(seats.operator, "project:P", "r2")
                stale = await scene.attempt(seats.worker, epoch, digest, "k2")
                third = await _stop(seats.operator, "project:P", "s4")
    finally:
        store.close()
    export_store(path, tmp_path / "export")
    exported = [
        json.loads(line)["kind"]
        for line in (tmp_path / "export" / "events.jsonl").read_text().splitlines()
    ]
    assert (first["epoch"], resumed["lifts_seq"], second["epoch"]) == (1, first["seq"], 2)
    assert stopped["payload"] == f"the scope project:P is stopped (journal seq {second['seq']})"
    assert (again["answer"], again["standing_seq"]) == ("already_stopped", second["seq"])
    assert lifted["lifts_seq"] == second["seq"]
    assert stale["payload"].endswith(f"(journal seq {lifted['seq']})")
    assert third["epoch"] == 3
    assert exported.count(EventKind.OPERATION_STOPPED) == 3
    assert exported.count(EventKind.OPERATION_RESUMED) == 2
    assert exported.count(EventKind.OPERATION_STOP_ANSWERED) == 1


def _held_commit(store: EventStore, kind: str) -> tuple[threading.Event, threading.Event, Any]:
    """Return events that hold the first commit led by a ``kind`` row until released."""
    entered, release = threading.Event(), threading.Event()
    commit = store.commit_operation

    def held(**kwargs: Any) -> Any:
        if kwargs["events"][0][0] == kind and not entered.is_set():
            entered.set()
            release.wait(timeout=scene.WAIT)
        return commit(**kwargs)

    return entered, release, held


@pytest.mark.parametrize("first", ["attempt", "stop"])
async def test_the_lock_orders_a_stop_and_an_attempt_and_the_list_is_taken_under_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, first: str
) -> None:
    """The first request holds its commit, and so the lock; the second waits and sees it.

    Every frame passes the mutation lock when it registers, so the second
    request is held after that step and before its handler. The first then
    holds its commit; the second's handler runs meanwhile and waits for the lock.
    """
    store = EventStore(tmp_path / "hub.db")
    held_kind = (
        EventKind.OPERATION_ATTEMPT_ANSWERED if first == "attempt" else EventKind.OPERATION_STOPPED
    )
    entered, release, held = _held_commit(store, held_kind)
    registered, go, running = asyncio.Event(), asyncio.Event(), asyncio.Event()
    second_type = (
        MessageType.OPERATION_STOP if first == "attempt" else MessageType.OPERATION_ATTEMPT
    )
    serve = DISPATCH[second_type]

    async def held_second(hub: Any, sender: str, data: dict[str, Any], websocket: Any) -> None:
        registered.set()
        await go.wait()
        running.set()
        await serve(hub, sender, data, websocket)

    monkeypatch.setattr(store, "commit_operation", held)
    monkeypatch.setitem(DISPATCH, second_type, held_second)
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=_policy())) as (_h, uri):
            async with _seats(uri) as seats:
                epoch, digest = await _prepared(seats)
                attempt = scene.attempt(seats.worker, epoch, digest, "k1")
                stop = _stop(seats.operator, "project:P", "s1")
                tail = asyncio.create_task(stop if first == "attempt" else attempt)
                await asyncio.wait_for(registered.wait(), scene.WAIT)
                head = asyncio.create_task(attempt if first == "attempt" else stop)
                await asyncio.to_thread(entered.wait, scene.WAIT)
                go.set()
                await asyncio.wait_for(running.wait(), scene.WAIT)
                await asyncio.sleep(0.05)
                release.set()
                head_answer, tail_answer = await head, await tail
    finally:
        release.set()
        store.close()
    opened, stopped = (
        (head_answer, tail_answer) if first == "attempt" else (tail_answer, head_answer)
    )
    if first == "attempt":
        assert opened["answer"] == "approved_fresh"
        assert [item["attempt_id"] for item in stopped["listed"]] == [opened["attempt_id"]]
    else:
        assert stopped["listed"] == []
        assert opened["answer"] == "stopped"
    assert stopped["seq"] != opened["seq"]


RACE_RUNS = 200
"""Fresh hubs the race runs on, each with one attempt and one stop sent together."""

_DELAYS = (-0.02, -0.002, 0.0, 0.0005, 0.001, 0.002, 0.004, 0.008, 0.02, 0.05)
"""Seconds the stop is sent after the attempt; a negative delay sends the stop first."""


async def _one_race(path: Path, delay: float) -> tuple[str, bool]:
    """Race one attempt against one stop of its project; return the answer and if listed."""
    store = EventStore(path)
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=_policy())) as (_h, uri):
            async with _seats(uri) as seats:
                epoch, digest = await _prepared(seats)

                async def attempt() -> dict[str, Any]:
                    if delay < 0:
                        await asyncio.sleep(-delay)
                    return await scene.attempt(seats.worker, epoch, digest, "k1")

                async def stop() -> dict[str, Any]:
                    if delay > 0:
                        await asyncio.sleep(delay)
                    return await _stop(seats.operator, "project:P", "s1")

                opened, stopped = await asyncio.gather(attempt(), stop())
        rows = _rows(store, EventKind.OPERATION_STOPPED)
    finally:
        store.close()
    listed = [item["attempt_id"] for item in stopped["listed"]]
    assert [row[2]["listed"] for row in rows] == [stopped["listed"]]
    if opened["answer"] == "approved_fresh":
        assert listed == [opened["attempt_id"]], (delay, opened, stopped)
        return opened["answer"], True
    assert opened["answer"] == "stopped" and listed == [], (delay, opened, stopped)
    return opened["answer"], False


@pytest.mark.real_hub
async def test_an_attempt_opened_between_the_list_and_its_record_cannot_exist(
    tmp_path: Path,
) -> None:
    """Each run ends with the attempt listed or answered ``stopped``; never opened and unlisted."""
    seen: dict[tuple[str, bool], int] = {}
    for run in range(RACE_RUNS):
        outcome = await _one_race(tmp_path / f"race-{run}.db", _DELAYS[run % len(_DELAYS)])
        seen[outcome] = seen.get(outcome, 0) + 1
    assert set(seen) == {("approved_fresh", True), ("stopped", False)}, seen
    assert sum(seen.values()) == RACE_RUNS


def test_standing_listed_reads_only_the_stop_s_own_list_while_still_open() -> None:
    """Ignore unlisted attempts in the scope and drop sealed or reconciled ones."""
    ledger = AttemptLedger(None)
    assert ledger.standing_listed(99) == ()
    fact = OperationFact("T1", "P/worker", "d" * 64, None, 1.0, "git_publication", "cooperative")
    ledger.note_declaration("op-1", fact)
    ledger.note_declaration("op-2", fact)
    ledger.note_declaration("op-3", fact)
    ledger.open_attempt("op-1", "a1", executor="P/worker", approval_seq=1)
    ledger.open_attempt("op-2", "b1", executor="P/worker", approval_seq=2)
    ledger.open_attempt("op-3", "c1", executor="P/worker", approval_seq=3)
    ledger.note_stop(
        "project:P",
        10,
        epoch=1,
        listed=(("op-1", "a1"), ("op-3", "c1"), ("op-missing", "m1"), ("op-1", "a-missing")),
    )
    ledger.note_outcome("op-1", "P/worker", "not_started", seq=11)
    assert ledger.standing_listed(10) == (
        OpenAttempt("op-1", "a1", "T1", "P/worker"),
        OpenAttempt("op-3", "c1", "T1", "P/worker"),
    )
    ledger.note_seal("op-3", "c1", seq=11)
    assert ledger.standing_listed(10) == (OpenAttempt("op-1", "a1", "T1", "P/worker"),)
    ledger.note_verification("op-1", "Q/checker", effect="absent", result="not_verified", seq=12)
    assert ledger.standing_listed(10) == ()
    ledger.note_declaration("op-4", fact)
    ledger.open_attempt("op-4", "d1", executor="P/worker", approval_seq=4)
    ledger.note_stop("project:P", 20, epoch=2, listed=(("op-4", "d1"),))
    ledger.note_verification("op-4", "Q/checker", effect="observed", result="verified", seq=21)
    assert ledger.standing_listed(20) == ()
