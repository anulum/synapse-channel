# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a live hub records triggers with their step down and refuses automation at A1
"""A trigger lowers the level in its own transaction; under ``--ladder`` A1 needs a person."""

from __future__ import annotations

import asyncio
import os
import random
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import pytest

import operation_attempt_scenario as scene
from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub
from hub_service_lifetime_examples import running_services, until
from synapse_channel.core import hub_service_lifetime as lifetime
from synapse_channel.core.acl import OPERATION_LEVEL, OPERATION_STOP, AclPolicy, AclRule
from synapse_channel.core.approvals import APPROVAL_NOTE_KIND, format_approval_note
from synapse_channel.core.execution_outcome import OutcomeState
from synapse_channel.core.handlers import operation_triggers as watch
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.role_grants import RoleGrants

OPERATOR = "H/operator"
LEAD = "P/lead"
"""A person: the role grants allow it ``P/person``."""
GRANTS = RoleGrants({"P/person": frozenset({LEAD}), "H/person": frozenset({OPERATOR})})
FOUR = ("effect:file", "project:P", "installation")
RACE_REPS = int(os.environ.get("SYNAPSE_LADDER_RACE_REPS", "20"))
"""Rounds of the trigger race; the gate runs it with 200."""


def _policy(*, stop: bool = False) -> AclPolicy:
    rules = [*scene.policy().rules, AclRule(OPERATION_LEVEL, "level-scope", "*", "H", "operators")]
    if stop:
        rules.append(AclRule(OPERATION_STOP, "stop-scope", "*", "H", "operators"))
    return AclPolicy(rules)


def _hub(store: EventStore | None, **options: Any) -> SynapseHub:
    return SynapseHub(journal=store, acl_policy=_policy(), role_grants=GRANTS, **options)


@asynccontextmanager
async def _seats(uri: str) -> AsyncIterator[dict[str, AgentHandle]]:
    names = (scene.WORKER, scene.REVIEWER, scene.APPROVER, scene.CHECKER, OPERATOR, LEAD)
    handles = {name: await connect_agent(name, uri) for name in names}
    try:
        yield handles
    finally:
        await close_agents(*handles.values())


async def _level_request(
    handle: AgentHandle, msg_type: str, scope: str, level: str, key: str
) -> dict[str, Any]:
    return await scene.answer(
        handle,
        lambda: handle.agent.send_message(
            msg_type, scope=scope, level=level, reason="r", idem_key=key
        ),
        lambda frame: (
            frame.get("type") == MessageType.OPERATION_LEVEL_RESULT
            and frame.get("request_key") == key
        ),
    )


async def _by_hand(handle: AgentHandle, scope: str, level: str, key: str) -> dict[str, Any]:
    return await _level_request(handle, MessageType.OPERATION_LEVEL, scope, level, key)


async def _lower(handle: AgentHandle, scope: str, level: str, key: str) -> dict[str, Any]:
    return await _level_request(handle, MessageType.OPERATION_LEVEL_LOWER, scope, level, key)


async def _raise_to_a2(operator: AgentHandle, operation_id: str, tag: str) -> None:
    for number, scope in enumerate((f"operation:{operation_id}", *FOUR)):
        answer = await _by_hand(operator, scope, "A2", f"{tag}-{number}")
        assert answer["answer"] in ("level_recorded", "already_at_level"), answer


async def _approve(handle: AgentHandle, digest: str, reason: str) -> dict[str, Any]:
    """Post an approval note; return the posted note's frame or the refusal."""
    text = format_approval_note(subject=f"sha256:{digest}", state="approved", reason=reason)
    return await scene.answer(
        handle,
        lambda: handle.agent.post_progress(scene.TASK, text, kind=APPROVAL_NOTE_KIND),
        lambda frame: (
            (frame.get("type") == "ledger_progress_posted" and frame["note"]["text"] == text)
            or frame.get("type") == MessageType.ERROR
        ),
    )


async def _declared(seats: dict[str, AgentHandle], epoch: int, operation_id: str) -> str:
    digest = await scene.declare(
        seats[scene.WORKER], epoch, operation_id=operation_id, intent=scene.statement()
    )
    await scene.review(seats[scene.REVIEWER], digest)
    return digest


async def _ran(
    seats: dict[str, AgentHandle], epoch: int, operation_id: str, approver: str, key: str
) -> str:
    """Declare, review, approve, open an attempt and report it done; return the digest."""
    digest = await _declared(seats, epoch, operation_id)
    approved = await _approve(seats[approver], digest, key)
    assert approved["type"] == "ledger_progress_posted", approved
    opened = await scene.attempt(seats[scene.WORKER], epoch, digest, key, operation_id=operation_id)
    assert opened["answer"] == "approved_fresh", opened
    done = scene.outcome(OutcomeState.CONFIRMED_DONE, operation_id)
    assert (await scene.record(seats[scene.WORKER], **done))["status"] == "accepted"
    return digest


async def _check(seats: dict[str, AgentHandle], operation_id: str, result: str) -> dict[str, Any]:
    verification = scene.verification("observed", result, operation_id)
    return await scene.record(seats[scene.CHECKER], **verification)


def _rows(store: EventStore, after: int = 0) -> list[Any]:
    return store.read_since(
        after,
        kinds=(
            EventKind.OPERATION_TRIGGER,
            EventKind.OPERATION_LEVEL,
            EventKind.OPERATION_LEVEL_ANSWERED,
        ),
    )


async def test_an_incorrect_acceptance_steps_every_scope_down_and_a1_needs_a_person(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(_hub(store, ladder_enabled=True)) as (_h, uri):
            async with _seats(uri) as seats:
                epoch = await scene.claim(seats[scene.WORKER])
                waiting = await _declared(seats, epoch, "op-2")
                early = await _approve(seats[scene.APPROVER], waiting, "at A1")
                await _raise_to_a2(seats[OPERATOR], "op-1", "up")
                await _raise_to_a2(seats[OPERATOR], "op-2", "up2")
                await _ran(seats, epoch, "op-1", scene.APPROVER, "k1")
                await _check(seats, "op-1", "verified")
                before = store.max_seq()
                wrong = await _check(seats, "op-1", "not_verified")
                rows = _rows(store, before)
                late = await _approve(seats[scene.APPROVER], waiting, "after the trigger")
                person = await _approve(seats[LEAD], waiting, "a person")
                text = format_approval_note(subject=f"sha256:{waiting}", state="rejected")
                rejection = await scene.answer(
                    seats[scene.APPROVER],
                    lambda: seats[scene.APPROVER].agent.post_progress(
                        scene.TASK, text, kind=APPROVAL_NOTE_KIND
                    ),
                    lambda frame: (
                        frame.get("type") in ("ledger_progress_posted", MessageType.ERROR)
                    ),
                )
    finally:
        store.close()
    refusal = "approval gate: an automated approval needs level A2 or above (scope {} is at A1)"
    assert (early["type"], early["payload"]) == (
        MessageType.ERROR,
        refusal.format("operation:op-2"),
    )
    assert wrong["status"] == "accepted"
    trigger, *levels = rows
    assert trigger.kind == EventKind.OPERATION_TRIGGER
    assert {key: trigger.payload[key] for key in ("scope", "cause", "target", "shown_by")} == {
        "scope": "operation:op-1",
        "cause": "incorrect_acceptance",
        "target": "A1",
        "shown_by": wrong["seq"],
    }
    assert trigger.payload["lowered"] == ["operation:op-1", *FOUR]
    assert [(row.kind, row.payload["scope"]) for row in levels] == [
        (EventKind.OPERATION_LEVEL, scope) for scope in ("operation:op-1", *FOUR)
    ]
    assert {
        (row.payload["from"], row.payload["level"], row.payload["cause"]) for row in levels
    } == {("A2", "A1", f"trigger:{trigger.seq}")}
    assert [row.seq for row in rows] == list(range(trigger.seq, trigger.seq + 5))
    assert (late["type"], late["payload"]) == (MessageType.ERROR, refusal.format("effect:file"))
    assert person["type"] == "ledger_progress_posted"
    assert rejection["type"] == "ledger_progress_posted"


async def test_without_the_ladder_a_default_hub_changes_nothing(tmp_path: Path) -> None:
    """An automated approval at A1 stands, an incorrect acceptance writes no trigger, no watcher."""
    store = EventStore(tmp_path / "hub.db")
    try:
        default = _hub(store)
        assert default._trigger_watcher() is None
        async with running_hub(default) as (_h, uri):
            async with _seats(uri) as seats:
                epoch = await scene.claim(seats[scene.WORKER])
                await _by_hand(seats[OPERATOR], "project:P", "A2", "l1")
                digest = await _declared(seats, epoch, "op-2")
                approved = await _approve(seats[scene.APPROVER], digest, "at A1")
                await _ran(seats, epoch, "op-1", scene.APPROVER, "k1")
                await _check(seats, "op-1", "verified")
                wrong = await _check(seats, "op-1", "not_verified")
        triggers = store.read_since(0, kinds=(EventKind.OPERATION_TRIGGER,))
    finally:
        store.close()
    laddered = _hub(None, ladder_enabled=True)
    assert laddered._trigger_watcher() is None
    other = EventStore(tmp_path / "other.db")
    try:
        watcher = _hub(other, ladder_enabled=True)._trigger_watcher()
        assert watcher is not None
        watcher.close()
    finally:
        other.close()
    assert approved["type"] == "ledger_progress_posted"
    assert (wrong["status"], triggers) == ("accepted", [])


async def test_the_watcher_writes_a_deadline_trigger_once_even_across_a_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "hub.db"
    store = EventStore(path)
    since = time.time()
    monkeypatch.setattr(watch, "wall_clock", lambda: time.time() + 1000)
    try:
        async with running_hub(_hub(store, ladder_enabled=True)) as (hub, uri):
            async with _seats(uri) as seats:
                epoch = await scene.claim(seats[scene.WORKER])
                await _by_hand(seats[OPERATOR], "project:P", "A2", "l1")
                await _ran(seats, epoch, "op-1", LEAD, "k1")
                first = await watch.tick(hub, since)
                second = await watch.tick(hub, since)
        rows = _rows(store)
    finally:
        store.close()
    store = EventStore(path)
    try:
        async with running_hub(_hub(store, ladder_enabled=True)) as (hub, _uri):
            after_restart = await watch.tick(hub, since)
    finally:
        store.close()
    assert [(item.cause, item.scope) for item in first] == [
        ("deadline_unverified", "operation:op-1")
    ]
    assert (second, after_restart) == ((), ())
    trigger = rows[-2]
    assert (trigger.kind, trigger.payload["operation_id"]) == (EventKind.OPERATION_TRIGGER, "op-1")
    assert (rows[-1].payload["scope"], rows[-1].payload["cause"]) == (
        "project:P",
        f"trigger:{trigger.seq}",
    )


async def test_the_watcher_writes_a_stop_bound_trigger_for_an_unsettled_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = EventStore(tmp_path / "hub.db")
    since = time.time()
    monkeypatch.setattr(watch, "wall_clock", lambda: time.time() + 61)
    try:
        options: dict[str, Any] = {"stop_bound_seconds": 60.0, "ladder_enabled": True}
        async with running_hub(_hub(store, **options)) as (hub, uri):
            async with _seats(uri) as seats:
                epoch = await scene.claim(seats[scene.WORKER])
                await _by_hand(seats[OPERATOR], "project:P", "A2", "l1")
                digest = await _declared(seats, epoch, "op-1")
                await _approve(seats[LEAD], digest, "k1")
                await scene.attempt(seats[scene.WORKER], epoch, digest, "k1", operation_id="op-1")
                operator = seats[OPERATOR]
                stop = await scene.answer(
                    operator,
                    lambda: operator.agent.send_message(
                        MessageType.OPERATION_STOP, scope="project:P", reason="r", idem_key="s1"
                    ),
                    lambda frame: frame.get("type") == MessageType.OPERATION_STOP_RESULT,
                )
                first = await watch.tick(hub, since)
                second = await watch.tick(hub, since)
        rows = _rows(store)
    finally:
        store.close()
    assert stop["state"] == "stop requested"
    assert [(item.cause, item.scope, item.shown_by) for item in first] == [
        ("stop_unsettled", "project:P", stop["seq"])
    ]
    assert second == ()
    assert (rows[-2].payload["stop_seq"], rows[-1].payload["level"]) == (stop["seq"], "A1")


async def test_a_hub_restored_from_a_long_journal_writes_no_burst_of_old_triggers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "hub.db"
    store = EventStore(path)
    try:
        async with running_hub(_hub(store, ladder_enabled=True)) as (_h, uri):
            async with _seats(uri) as seats:
                epoch = await scene.claim(seats[scene.WORKER])
                await _by_hand(seats[OPERATOR], "project:P", "A2", "l1")
                for number in range(12):
                    await _ran(seats, epoch, f"op-{number}", LEAD, f"k{number}")
    finally:
        store.close()
    monkeypatch.setattr(watch, "wall_clock", lambda: time.time() + 1000)
    store = EventStore(path)
    try:
        async with running_hub(_hub(store, ladder_enabled=True)) as (hub, _uri):
            restarted = watch.wall_clock()
            quiet = await watch.tick(hub, restarted)
            backlog = hub.operation_records.attempts.due_triggers(restarted, 900.0)
        triggers = store.read_since(0, kinds=(EventKind.OPERATION_TRIGGER,))
    finally:
        store.close()
    assert (quiet, triggers) == ((), [])
    assert len(backlog) == 12


async def test_an_actor_may_always_ask_for_its_own_step_down(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(_hub(store)) as (_h, uri):
            async with _seats(uri) as seats:
                worker = seats[scene.WORKER]
                epoch = await scene.claim(worker)
                await _declared(seats, epoch, "op-1")
                await _by_hand(seats[OPERATOR], "project:P", "A3", "l1")
                answers = [
                    await _lower(worker, "project:P", "A2", "w1"),
                    await _lower(seats[scene.CHECKER], "project:P", "A1", "c1"),
                    await _lower(worker, "project:P", "A2", "w2"),
                    await _lower(worker, "project:P", "A3", "w3"),
                    await _lower(worker, "nowhere", "A1", "w4"),
                ]
        rows = _rows(store)
    finally:
        store.close()
    assert [item["answer"] for item in answers] == [
        "lowered",
        "not_an_actor",
        "not_lower",
        "not_lower",
        "invalid_request",
    ]
    assert (answers[0]["from"], answers[0]["level"]) == ("A3", "A2")
    assert answers[1]["payload"] == f"{scene.CHECKER} is not an actor of project:P"
    assert answers[4]["scope"] == ""
    trigger = next(row for row in rows if row.kind == EventKind.OPERATION_TRIGGER)
    assert (trigger.payload["cause"], trigger.payload["by"]) == ("actor_request", scene.WORKER)
    assert trigger.seq == answers[0]["seq"]
    answered = [
        row.payload["answer"] for row in rows if row.kind == EventKind.OPERATION_LEVEL_ANSWERED
    ]
    assert answered == ["not_an_actor", "not_lower", "not_lower"]


async def test_an_enforcing_hub_asks_for_operation_stop_and_no_journal_records_nothing(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        hub = SynapseHub(journal=store, acl_policy=_policy(), require_acl=True)
        async with running_hub(hub) as (_h, uri):
            worker = await connect_agent(scene.WORKER, uri)
            try:
                denied = await scene.answer(
                    worker,
                    lambda: worker.agent.send_message(
                        MessageType.OPERATION_LEVEL_LOWER,
                        scope="project:P",
                        level="A1",
                        reason="r",
                        idem_key="w1",
                    ),
                    lambda frame: frame.get("type") == MessageType.ERROR and "acl_reason" in frame,
                )
            finally:
                await close_agents(worker)
    finally:
        store.close()
    async with running_hub(_hub(None)) as (_h, uri):
        worker = await connect_agent(scene.WORKER, uri)
        try:
            no_journal = await _lower(worker, "project:P", "A1", "w2")
        finally:
            await close_agents(worker)
    assert denied["payload"] == "access denied: operation-stop on stop-scope:project:P"
    assert (no_journal["answer"], no_journal["scope"]) == ("no_journal", "")


async def test_an_approval_admitted_before_a_trigger_but_written_after_it_is_refused(
    tmp_path: Path,
) -> None:
    """The admission passed at A2; the trigger took the lock first; the write refuses the note."""
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(_hub(store, ladder_enabled=True)) as (hub, uri):
            async with _seats(uri) as seats:
                epoch = await scene.claim(seats[scene.WORKER])
                await _raise_to_a2(seats[OPERATOR], "op-1", "up")
                digest = await _declared(seats, epoch, "op-1")
                await _ran(seats, epoch, "victim", LEAD, "v1")
                await _check(seats, "victim", "verified")
                async with hub.state_mutations._lock:
                    wrong = asyncio.create_task(_check(seats, "victim", "not_verified"))
                    await asyncio.sleep(0.2)
                    late = asyncio.create_task(_approve(seats[scene.APPROVER], digest, "queued"))
                    await asyncio.sleep(0.2)
                answers = (await wrong, await late)
        notes = [
            row
            for row in store.read_since(0, kinds=(EventKind.LEDGER_PROGRESS,))
            if row.payload["author"] == scene.APPROVER
        ]
    finally:
        store.close()
    refusal = (
        "approval gate: an automated approval needs level A2 or above (scope effect:file is at A1)"
    )
    assert answers[0]["status"] == "accepted"
    assert (answers[1]["type"], answers[1]["payload"]) == (MessageType.ERROR, refusal)
    assert notes == []


async def test_a_laddered_hub_runs_its_trigger_watcher_as_a_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[Any] = []

    async def watching(hub: Any) -> None:
        started.append(hub)
        await asyncio.Event().wait()

    monkeypatch.setattr(lifetime, "watch_operation_triggers", watching)
    store = EventStore(tmp_path / "hub.db")
    try:
        hub = _hub(store, ladder_enabled=True)
        async with running_services(hub):
            await until(lambda: bool(started))
    finally:
        store.close()
    assert started == [hub]


async def test_the_watch_loop_ticks_from_its_start_and_survives_a_failed_tick(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    assert await watch.tick(cast(Any, _NoJournal()), 0.0) == ()
    seen: list[float] = []

    async def ticking(_hub: Any, since: float) -> tuple[Any, ...]:
        seen.append(since)
        if len(seen) == 1:
            raise RuntimeError("tick failed")
        if len(seen) == 3:
            raise asyncio.CancelledError
        return ()

    monkeypatch.setattr(watch, "tick", ticking)
    monkeypatch.setattr(watch, "wall_clock", lambda: 123.0)
    with pytest.raises(asyncio.CancelledError):
        await watch.watch_operation_triggers(cast(Any, object()), interval=0)
    assert seen == [123.0, 123.0, 123.0]
    assert "cannot check the operations for triggers" in caplog.text


class _NoJournal:
    journal = None


async def _race_round(
    store: EventStore, seats: dict[str, AgentHandle], epoch: int, number: int
) -> tuple[int, int]:
    """One round: automated approvals stream while an incorrect acceptance triggers."""
    await _raise_to_a2(seats[OPERATOR], f"op-{number}", f"r{number}")
    digest = await _declared(seats, epoch, f"op-{number}")
    await _ran(seats, epoch, f"victim-{number}", LEAD, f"v{number}")
    await _check(seats, f"victim-{number}", "verified")
    answers: list[dict[str, Any]] = []

    async def stream() -> None:
        for count in range(6):
            answers.append(await _approve(seats[scene.APPROVER], digest, f"{number}-{count}"))

    async def wrong() -> None:
        await asyncio.sleep(random.uniform(0, 0.1))
        await _check(seats, f"victim-{number}", "not_verified")

    await asyncio.gather(stream(), wrong())
    trigger = store.read_since(0, kinds=(EventKind.OPERATION_TRIGGER,))[-1]
    assert trigger.payload["operation_id"] == f"victim-{number}"
    line = trigger.seq + 1
    notes = [
        row
        for row in store.read_since(0, kinds=(EventKind.LEDGER_PROGRESS,))
        if row.payload["author"] == scene.APPROVER and digest in row.payload["text"]
    ]
    assert all(row.seq < line for row in notes), (number, [row.seq for row in notes], line)
    refused = [item for item in answers if item["type"] == MessageType.ERROR]
    assert len(notes) + len(refused) == len(answers)
    opened = await scene.attempt(
        seats[scene.WORKER], epoch, digest, f"a{number}", operation_id=f"op-{number}"
    )
    assert opened["answer"] != "approved_fresh", opened
    return len(notes), len(refused)


async def test_no_automated_approval_survives_a_trigger_under_the_ladder(tmp_path: Path) -> None:
    """The done line: every admitted automated note lies below the step down; none opens."""
    store = EventStore(tmp_path / "hub.db")
    random.seed(20261117)
    try:
        async with running_hub(_hub(store, ladder_enabled=True)) as (_h, uri):
            async with _seats(uri) as seats:
                epoch = await scene.claim(seats[scene.WORKER])
                rounds = [
                    await _race_round(store, seats, epoch, number) for number in range(RACE_REPS)
                ]
    finally:
        store.close()
    print(f"race rounds (admitted, refused): {rounds}")
    assert any(admitted for admitted, _refused in rounds)
    assert any(refused for _admitted, refused in rounds)
