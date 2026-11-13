# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a real hub keeps a seal, reports an operation's attempts, and refuses the rest
"""A real hub ends a sealed attempt and answers ``operation_state`` to its holders only."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import operation_attempt_scenario as scene
from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub
from synapse_channel.core.acl import OPERATION_SEAL, AclPolicy, AclRule
from synapse_channel.core.attempt_markers import LANDED, AttemptMarker
from synapse_channel.core.execution_outcome import OutcomeState
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType

RECOVERY = "R/recovery"
STRANGER = "S/stranger"


def _policy() -> AclPolicy:
    """The scenario's policy plus ``operation-seal`` for the recovery project alone."""
    rules = list(scene.policy().rules)
    rules.append(AclRule(OPERATION_SEAL, "claim", "T*", "R", "the recovery role seals"))
    return AclPolicy(rules)


class _Seats:
    def __init__(self, handles: dict[str, AgentHandle]) -> None:
        self.worker = handles[scene.WORKER]
        self.reviewer = handles[scene.REVIEWER]
        self.approver = handles[scene.APPROVER]
        self.checker = handles[scene.CHECKER]
        self.recovery = handles[RECOVERY]
        self.stranger = handles[STRANGER]


@asynccontextmanager
async def _seats(uri: str) -> AsyncIterator[_Seats]:
    names = (scene.WORKER, scene.REVIEWER, scene.APPROVER, scene.CHECKER, RECOVERY, STRANGER)
    handles = {name: await connect_agent(name, uri) for name in names}
    try:
        yield _Seats(handles)
    finally:
        await close_agents(*handles.values())


async def _opened(seats: _Seats) -> tuple[int, str, str]:
    """Claim, declare, review, approve and open an attempt; return epoch, digest, attempt id."""
    epoch = await scene.claim(seats.worker)
    digest = await scene.declare(seats.worker, epoch, intent=scene.statement())
    await scene.review(seats.reviewer, digest)
    await scene.approve(seats.approver, digest)
    opened = await scene.attempt(seats.worker, epoch, digest, "k1")
    assert opened["answer"] == "approved_fresh", opened
    return epoch, digest, str(opened["attempt_id"])


def _seal(attempt: str, digest: str, **changes: Any) -> dict[str, Any]:
    """Return a seal record's fields naming this attempt's ``sealed`` blob, read back."""
    marker = AttemptMarker(scene.OPERATION, attempt, digest)
    body: dict[str, Any] = {
        "attempt_id": attempt,
        "marker_ref": marker.ref,
        "object_format": "sha1",
        "marker_object": marker.objects("sha1").sealed,
        "observation": LANDED,
    }
    body.update(changes)
    return {"record": "seal", "task_id": scene.TASK, "operation_id": scene.OPERATION, "seal": body}


async def _state(handle: AgentHandle, **fields: Any) -> dict[str, Any]:
    """Ask for an operation's attempts and return the answer."""
    request_id = uuid.uuid4().hex
    request = {"task_id": scene.TASK, "operation_id": scene.OPERATION, **fields}
    return await scene.answer(
        handle,
        lambda: handle.agent.send_message(
            MessageType.OPERATION_STATE, request_id=request_id, **request
        ),
        lambda frame: (
            frame.get("type") == MessageType.OPERATION_STATE_RESULT
            and frame.get("request_id") == request_id
        ),
    )


def _states(answer: dict[str, Any]) -> list[tuple[str, str, str]]:
    return [(a["attempt_id"], a["executor"], a["state"]) for a in answer["attempts"]]


async def test_a_seal_ends_the_open_attempt_until_another_identity_observes_it(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=_policy())) as (hub, uri):
            async with _seats(uri) as seats:
                epoch, digest, attempt = await _opened(seats)
                before = await _state(seats.recovery)
                kept = await scene.record(seats.recovery, **_seal(attempt, digest))
                counted = hub.operation_records.records_of(scene.OPERATION)
                after = await _state(seats.checker)
                replay = await scene.attempt(seats.worker, epoch, digest, "k1")
                await scene.approve(seats.approver, digest, reason="after the seal")
                waiting = await scene.attempt(seats.worker, epoch, digest, "k2")
                observed = await scene.record(
                    seats.checker, **scene.verification("absent", "not_verified")
                )
                reconciled = await _state(seats.recovery)
                fresh = await scene.attempt(seats.worker, epoch, digest, "k3")
        rows = store.read_since(0, kinds=(EventKind.OPERATION_SEALED,))
    finally:
        store.close()
    assert (before["status"], before["closed"]) == ("accepted", False)
    assert _states(before) == [(attempt, scene.WORKER, "open")]
    assert (kept["status"], kept["reason"], kept["payload"]) == ("accepted", "", "seal recorded")
    assert _states(after) == [(attempt, scene.WORKER, "sealed")]
    assert (replay["answer"], replay["live"]) == (
        "approved_fresh",
        {"attempt_state": "sealed", "replayed": True},
    )
    assert waiting["answer"] == "earlier_attempt_unreconciled"
    assert observed["status"] == "accepted"
    assert _states(reconciled) == [(attempt, scene.WORKER, "reconciled")]
    assert fresh["answer"] == "approved_fresh" and fresh["attempt_id"] != attempt
    assert [row.payload["sender"] for row in rows] == [RECOVERY]
    assert kept["seq"] == rows[0].seq
    assert counted == 1


async def test_every_refusal_of_a_seal_record(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=_policy())) as (_h, uri):
            async with _seats(uri) as seats:
                _epoch, digest, attempt = await _opened(seats)
                answers = {
                    "worker": await scene.record(seats.worker, **_seal(attempt, digest)),
                    "attempt": await scene.record(seats.recovery, **_seal("b" * 32, digest)),
                    "object": await scene.record(
                        seats.recovery, **_seal(attempt, digest, marker_object="0" * 40)
                    ),
                    "unknown": await scene.record(
                        seats.recovery, **{**_seal(attempt, digest), "operation_id": "op-9"}
                    ),
                    "task": await scene.record(
                        seats.recovery, **{**_seal(attempt, digest), "task_id": "T2"}
                    ),
                    "malformed": await scene.record(
                        seats.recovery, **_seal(attempt, digest, observation="maybe")
                    ),
                }
                kept = await scene.record(seats.recovery, **_seal(attempt, digest))
                answers["again"] = await scene.record(seats.recovery, **_seal(attempt, digest))
        rows = store.read_since(0, kinds=(EventKind.OPERATION_SEALED,))
    finally:
        store.close()
    reasons = {name: (frame["reason"], frame["payload"]) for name, frame in answers.items()}
    assert kept["status"] == "accepted"
    assert reasons == {
        "worker": ("not_authorised", f"{scene.WORKER} lacks operation-seal for {scene.TASK}"),
        "attempt": ("attempt_unknown", "the seal record cannot be kept: attempt_unknown"),
        "object": ("seal_not_observed", "the seal record cannot be kept: seal_not_observed"),
        "unknown": ("unknown_operation", "the seal record cannot be kept: unknown_operation"),
        "task": ("task_mismatch", "the seal record cannot be kept: task_mismatch"),
        "malformed": (
            "invalid_request",
            "seal observation must be one of ['base_gone', 'landed', 'landed_earlier', "
            "'not_reached', 'other_party_first', 'unknown']",
        ),
        "again": ("attempt_ended", "the seal record cannot be kept: attempt_ended"),
    }
    assert len(rows) == 1


async def test_an_open_hub_keeps_no_seal_and_answers_no_state(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store)) as (_h, uri):
            async with _seats(uri) as seats:
                _epoch, digest, attempt = await _opened(seats)
                sealed = await scene.record(seats.recovery, **_seal(attempt, digest))
                state = await _state(seats.recovery)
        rows = store.read_since(0, kinds=(EventKind.OPERATION_SEALED,))
    finally:
        store.close()
    assert (sealed["reason"], sealed["payload"]) == (
        "not_authorised",
        f"{RECOVERY} lacks operation-seal for {scene.TASK}",
    )
    assert (state["status"], state["reason"], "attempts" in state) == (
        "refused",
        "not_authorised",
        False,
    )
    assert rows == []


async def test_operation_state_answers_holders_only_and_journals_nothing(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=_policy())) as (_h, uri):
            async with _seats(uri) as seats:
                await _opened(seats)
                written = store.max_seq()
                stranger = await _state(seats.stranger)
                stranger_unknown = await _state(seats.stranger, operation_id="op-9")
                checker = await _state(seats.checker)
                recovery = await _state(seats.recovery)
                elsewhere = await _state(seats.recovery, task_id="T2")
                unknown = await _state(seats.recovery, operation_id="op-9")
                malformed = await _state(seats.recovery, operation_id="op 9")
                no_task = await _state(seats.recovery, task_id="")
                control = await _state(seats.recovery, task_id="T\x01")
                unchanged = store.max_seq()
    finally:
        store.close()
    assert (stranger["status"], stranger["reason"], stranger["payload"]) == (
        "refused",
        "not_authorised",
        f"{STRANGER} holds neither operation-evidence nor operation-seal for {scene.TASK}",
    )
    assert "attempts" not in stranger and "closed" not in stranger
    # An operation that does not exist reads the same to a stranger: no existence oracle.
    assert (stranger_unknown["reason"], stranger_unknown["payload"]) == (
        stranger["reason"],
        stranger["payload"],
    )
    assert checker["attempts"] == recovery["attempts"] and len(recovery["attempts"]) == 1
    assert (elsewhere["reason"], elsewhere["payload"]) == (
        "unknown_operation",
        f"no operation {scene.OPERATION} is declared on T2",
    )
    assert (unknown["reason"], unknown["payload"]) == (
        "unknown_operation",
        f"no operation op-9 is declared on {scene.TASK}",
    )
    assert (malformed["reason"], malformed["payload"]) == (
        "invalid_request",
        "an operation_state request names a task and an operation id",
    )
    for refused in (no_task, control):
        assert (refused["reason"], refused["payload"]) == (
            "invalid_request",
            "an operation_state request names a task and an operation id",
        )
    assert unchanged == written


async def test_a_restarted_hub_still_knows_the_seal_and_the_late_outcome_ends_nothing(
    tmp_path: Path,
) -> None:
    path = tmp_path / "hub.db"
    store = EventStore(path)
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=_policy())) as (_h, uri):
            async with _seats(uri) as seats:
                _epoch, digest, attempt = await _opened(seats)
                await scene.record(seats.recovery, **_seal(attempt, digest))
                late = await scene.record(
                    seats.worker, **scene.outcome(OutcomeState.CONFIRMED_DONE)
                )
                after_outcome = await _state(seats.recovery)
    finally:
        store.close()
    reopened = EventStore(path)
    try:
        async with running_hub(SynapseHub(journal=reopened, acl_policy=_policy())) as (_h, uri):
            recovery = await connect_agent(RECOVERY, uri)
            try:
                restored = await _state(recovery)
            finally:
                await close_agents(recovery)
    finally:
        reopened.close()
    assert late["status"] == "accepted"
    assert _states(after_outcome) == [(attempt, scene.WORKER, "sealed")]
    assert _states(restored) == [(attempt, scene.WORKER, "sealed")]
