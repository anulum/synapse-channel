# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a live hub sets levels by hand, fixes approver kinds, and stales automation
"""A level change makes an automated approval stale and leaves a person's standing."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import operation_attempt_scenario as scene
from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub
from synapse_channel.core.acl import OPERATION_LEVEL, AclPolicy, AclRule
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.role_grants import RoleGrants

OPERATOR = "H/operator"
LEAD = "P/lead"
"""A person: the role grants allow it ``P/person``."""

GRANTS = RoleGrants({"P/person": frozenset({LEAD})})


def _policy() -> AclPolicy:
    rules = [*scene.policy().rules, AclRule(OPERATION_LEVEL, "level-scope", "*", "H", "operators")]
    return AclPolicy(rules)


def _hub(store: EventStore, *, grants: RoleGrants | None = GRANTS) -> SynapseHub:
    return SynapseHub(journal=store, acl_policy=_policy(), role_grants=grants)


@asynccontextmanager
async def _seats(uri: str) -> AsyncIterator[dict[str, AgentHandle]]:
    names = (scene.WORKER, scene.REVIEWER, scene.APPROVER, LEAD, OPERATOR)
    handles = {name: await connect_agent(name, uri) for name in names}
    try:
        yield handles
    finally:
        await close_agents(*handles.values())


async def _level(handle: AgentHandle, scope: str, level: str, key: str) -> dict[str, Any]:
    return await scene.answer(
        handle,
        lambda: handle.agent.send_message(
            MessageType.OPERATION_LEVEL, scope=scope, level=level, reason="by hand", idem_key=key
        ),
        lambda frame: (
            frame.get("type") == MessageType.OPERATION_LEVEL_RESULT
            and frame.get("request_key") == key
        ),
    )


async def _declared(seats: dict[str, AgentHandle], epoch: int, operation_id: str) -> str:
    digest = await scene.declare(
        seats[scene.WORKER], epoch, operation_id=operation_id, intent=scene.statement()
    )
    await scene.review(seats[scene.REVIEWER], digest)
    return digest


async def test_a_step_down_stales_the_automated_approval_and_not_the_person_s(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(_hub(store)) as (_h, uri):
            async with _seats(uri) as seats:
                epoch = await scene.claim(seats[scene.WORKER])
                raised = await _level(seats[OPERATOR], "project:P", "A2", "l1")
                auto = await _declared(seats, epoch, "op-auto")
                person = await _declared(seats, epoch, "op-person")
                await scene.approve(seats[scene.APPROVER], auto)
                await scene.approve(seats[LEAD], person)
                lowered = await _level(seats[OPERATOR], "project:P", "A1", "l2")
                stale = await scene.attempt(
                    seats[scene.WORKER], epoch, auto, "k1", operation_id="op-auto"
                )
                fresh = await scene.attempt(
                    seats[scene.WORKER], epoch, person, "k2", operation_id="op-person"
                )
        kinds = [dict(row.payload) for row in store.read_since(0, kinds=(EventKind.APPROVAL_KIND,))]
        level_rows = store.read_since(0, kinds=(EventKind.OPERATION_LEVEL,))
        levels = [dict(row.payload) for row in level_rows]
    finally:
        store.close()
    assert (raised["answer"], raised["from"], raised["epoch"]) == ("level_recorded", "A1", 1)
    assert (lowered["answer"], lowered["from"], lowered["epoch"]) == ("level_recorded", "A2", 2)
    assert (stale["answer"], stale["payload"]) == (
        "approval_stale",
        "an automated approval older than the latest level change of scope project:P "
        f"(journal seq {lowered['seq']})",
    )
    assert fresh["answer"] == "approved_fresh"
    assert [(row["author"], row["kind"]) for row in kinds] == [
        (scene.APPROVER, "automated"),
        (LEAD, "person"),
    ]
    assert [(row["level"], row["from"], row["cause"], row["granted_by"]) for row in levels] == [
        ("A2", "A1", "by_hand", OPERATOR),
        ("A1", "A2", "by_hand", OPERATOR),
    ]


async def test_a_kind_stands_as_admitted_through_a_grant_change_and_a_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "hub.db"
    store = EventStore(path)
    try:
        async with running_hub(_hub(store)) as (hub, uri):
            async with _seats(uri) as seats:
                epoch = await scene.claim(seats[scene.WORKER])
                digest = await _declared(seats, epoch, scene.OPERATION)
                await scene.approve(seats[LEAD], digest)
                hub.role_grants = RoleGrants({})
                await _level(seats[OPERATOR], "project:P", "A3", "l1")
    finally:
        store.close()
    store = EventStore(path)
    try:
        async with running_hub(_hub(store, grants=None)) as (_h, uri):
            async with _seats(uri) as seats:
                epoch = await scene.claim(seats[scene.WORKER])
                again = await _level(seats[OPERATOR], "project:P", "A1", "l2")
                opened = await scene.attempt(seats[scene.WORKER], epoch, digest, "k1")
                state = await scene.answer(
                    seats[scene.WORKER],
                    lambda: seats[scene.WORKER].agent.send_message(
                        MessageType.OPERATION_STATE,
                        task_id=scene.TASK,
                        operation_id=scene.OPERATION,
                        request_id="r1",
                    ),
                    lambda frame: frame.get("type") == MessageType.OPERATION_STATE_RESULT,
                )
    finally:
        store.close()
    assert (again["from"], again["epoch"]) == ("A3", 2)
    assert opened["answer"] == "approved_fresh"
    assert state["level"] == {"level": "A1", "line_scope": "project:P", "line_seq": again["seq"]}


async def test_each_answer_is_journalled_a_raise_waits_for_a_stop_and_the_lowest_scope_wins(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(_hub(store)) as (_h, uri):
            async with _seats(uri) as seats:
                epoch = await scene.claim(seats[scene.WORKER])
                await _declared(seats, epoch, scene.OPERATION)
                operator = seats[OPERATOR]
                answers = [
                    await _level(seats[scene.WORKER], "project:P", "A2", "a1"),
                    await _level(operator, f"operation:{scene.OPERATION}", "A3", "a2"),
                    await _level(operator, f"operation:{scene.OPERATION}", "A3", "a3"),
                ]
                stop = await scene.answer(
                    operator,
                    lambda: operator.agent.send_message(
                        MessageType.OPERATION_STOP, scope="project:P", reason="r", idem_key="s1"
                    ),
                    lambda frame: frame.get("type") == MessageType.OPERATION_STOP_RESULT,
                )
                answers += [
                    await _level(operator, f"operation:{scene.OPERATION}", "A4", "a4"),
                    await _level(operator, f"operation:{scene.OPERATION}", "A2", "a5"),
                ]
                state = await scene.answer(
                    seats[scene.WORKER],
                    lambda: seats[scene.WORKER].agent.send_message(
                        MessageType.OPERATION_STATE,
                        task_id=scene.TASK,
                        operation_id=scene.OPERATION,
                        request_id="r1",
                    ),
                    lambda frame: frame.get("type") == MessageType.OPERATION_STATE_RESULT,
                )
        rows = store.read_since(
            0, kinds=(EventKind.OPERATION_LEVEL, EventKind.OPERATION_LEVEL_ANSWERED)
        )
    finally:
        store.close()
    assert [item["answer"] for item in answers] == [
        "not_permitted",
        "level_recorded",
        "already_at_level",
        "raise_stopped",
        "level_recorded",
    ]
    assert answers[0]["payload"] == f"{scene.WORKER} holds no operation-level on project:P"
    assert answers[3]["payload"] == (
        f"the scope project:P is stopped (journal seq {stop['seq']}); no raise"
    )
    assert [(row.seq, row.payload["request_key"]) for row in rows] == [
        (item["seq"], item["request_key"]) for item in answers
    ]
    assert state["level"]["level"] == "A1"
    assert state["level"]["line_scope"] == f"operation:{scene.OPERATION}"


async def test_a_hub_without_an_access_file_sets_no_level(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store)) as (_h, uri):
            operator = await connect_agent(OPERATOR, uri)
            try:
                refused = await _level(operator, "installation", "A2", "l1")
                malformed = await _level(operator, "installation", "A9", "l2")
            finally:
                await close_agents(operator)
        rows = store.read_since(0, kinds=(EventKind.OPERATION_LEVEL,))
    finally:
        store.close()
    async with running_hub(SynapseHub(acl_policy=_policy())) as (_h, uri):
        operator = await connect_agent(OPERATOR, uri)
        try:
            no_journal = await _level(operator, "installation", "A2", "l3")
        finally:
            await close_agents(operator)
    assert refused["answer"] == "not_permitted" and rows == []
    assert (malformed["answer"], malformed["scope"]) == ("invalid_request", "")
    assert (no_journal["answer"], no_journal["scope"]) == ("no_journal", "installation")


async def test_an_enforcing_hub_checks_the_grant_at_its_gate_and_a_changed_repeat_conflicts(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        hub = SynapseHub(journal=store, acl_policy=_policy(), require_acl=True)
        async with running_hub(hub) as (_h, uri):
            worker = await connect_agent(scene.WORKER, uri)
            operator = await connect_agent(OPERATOR, uri)
            try:
                denied = await scene.answer(
                    worker,
                    lambda: worker.agent.send_message(
                        MessageType.OPERATION_LEVEL,
                        scope="project:P",
                        level="A2",
                        reason="r",
                        idem_key="w1",
                    ),
                    lambda frame: frame.get("type") == MessageType.ERROR and "acl_reason" in frame,
                )
                recorded = await _level(operator, "project:P", "A2", "l1")
                repeat = await _level(operator, "project:P", "A2", "l1")
                changed = await scene.answer(
                    operator,
                    lambda: operator.agent.send_message(
                        MessageType.OPERATION_LEVEL,
                        scope="project:P",
                        level="A3",
                        reason="by hand",
                        idem_key="l1",
                    ),
                    lambda frame: frame.get("error_code") == "idempotency_conflict",
                )
            finally:
                await close_agents(worker, operator)
    finally:
        store.close()
    assert denied["payload"] == "access denied: operation-level on level-scope:project:P"
    assert recorded["answer"] == "level_recorded"
    assert (repeat["seq"], repeat["live"]) == (recorded["seq"], {"replayed": True})
    assert changed["error_code"] == "idempotency_conflict"
