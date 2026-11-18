# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a live hub records criteria and judgements and raises a level by one
"""A person raises a scope one level on a met judgement while the count, taken again, holds."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import operation_attempt_scenario as scene
from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub
from synapse_channel.core.acl import OPERATION_JUDGE, OPERATION_LEVEL, AclPolicy, AclRule
from synapse_channel.core.execution_outcome import OutcomeState
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.role_grants import RoleGrants

OPERATOR = "H/operator"
"""A person holding ``operation-level``."""
BOT = "H/bot"
"""An automated role holding ``operation-level``."""
JUDGE = "R/judge"
GRANTS = RoleGrants({"H/person": frozenset({OPERATOR})})
ITEMS = [
    {"item": "verified_effects:file", "min": 1},
    {"item": "incorrect_acceptances", "max": 0},
    {"item": "unknown_outcomes", "max": 0},
]
LEVEL_ROWS = (
    EventKind.LEVEL_CRITERIA,
    EventKind.LEVEL_JUDGEMENT,
    EventKind.OPERATION_LEVEL,
    EventKind.OPERATION_LEVEL_ANSWERED,
)


def _policy() -> AclPolicy:
    rules = [
        *scene.policy().rules,
        AclRule(OPERATION_LEVEL, "level-scope", "*", "H", "operators"),
        AclRule(OPERATION_JUDGE, "level-scope", "*", "R", "judges"),
    ]
    return AclPolicy(rules)


def _hub(store: EventStore) -> SynapseHub:
    return SynapseHub(journal=store, acl_policy=_policy(), role_grants=GRANTS)


@asynccontextmanager
async def _seats(uri: str) -> AsyncIterator[dict[str, AgentHandle]]:
    names = (scene.WORKER, scene.REVIEWER, scene.APPROVER, scene.CHECKER, OPERATOR, BOT, JUDGE)
    handles = {name: await connect_agent(name, uri) for name in names}
    try:
        yield handles
    finally:
        await close_agents(*handles.values())


async def _ask(handle: AgentHandle, msg_type: str, key: str, **fields: Any) -> dict[str, Any]:
    return await scene.answer(
        handle,
        lambda: handle.agent.send_message(msg_type, idem_key=key, **fields),
        lambda frame: (
            frame.get("type") == MessageType.OPERATION_LEVEL_RESULT
            and frame.get("request_key") == key
        ),
    )


async def _criteria(handle: AgentHandle, target: str, key: str) -> dict[str, Any]:
    return await _ask(
        handle,
        MessageType.OPERATION_LEVEL_CRITERIA,
        key,
        scope="project:P",
        target=target,
        items=ITEMS,
    )


async def _judge(
    handle: AgentHandle, digest: str, key: str, verdict: str = "met"
) -> dict[str, Any]:
    return await _ask(
        handle,
        MessageType.OPERATION_LEVEL_JUDGEMENT,
        key,
        scope="project:P",
        digest=digest,
        verdict=verdict,
    )


async def _raise(
    handle: AgentHandle, digest: str, judged: int, key: str, target: str = "A2"
) -> dict[str, Any]:
    return await _ask(
        handle,
        MessageType.OPERATION_LEVEL_RAISE,
        key,
        scope="project:P",
        target=target,
        digest=digest,
        judgement_seq=judged,
    )


async def _by_hand(handle: AgentHandle, level: str, key: str) -> dict[str, Any]:
    return await scene.answer(
        handle,
        lambda: handle.agent.send_message(
            MessageType.OPERATION_LEVEL, scope="project:P", level=level, reason="r", idem_key=key
        ),
        lambda frame: frame.get("request_key") == key,
    )


async def _verified(seats: dict[str, AgentHandle], epoch: int, operation: str, key: str) -> None:
    """Declare, review, approve, run and independently verify one file operation."""
    worker = seats[scene.WORKER]
    digest = await scene.declare(worker, epoch, operation_id=operation, intent=scene.statement())
    await scene.review(seats[scene.REVIEWER], digest)
    await scene.approve(seats[scene.APPROVER], digest)
    opened = await scene.attempt(worker, epoch, digest, key, operation_id=operation)
    assert opened["answer"] == "approved_fresh", opened
    done = scene.outcome(OutcomeState.CONFIRMED_DONE, operation)
    assert (await scene.record(worker, **done))["status"] == "accepted"
    check = scene.verification("observed", "verified", operation)
    assert (await scene.record(seats[scene.CHECKER], **check))["status"] == "accepted"


async def test_a_judged_count_raises_a_scope_one_level_and_each_guard_answers_why(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(_hub(store)) as (_h, uri):
            async with _seats(uri) as seats:
                worker, operator = seats[scene.WORKER], seats[OPERATOR]
                epoch = await scene.claim(worker)
                answers = [
                    await _criteria(operator, "A2", "c1"),
                    await _criteria(operator, "A3", "c2"),
                    await _criteria(worker, "A2", "c3"),
                ]
                digest = answers[0]["record"]["digest"]
                intent = await scene.declare(worker, epoch, intent=scene.statement())
                await scene.review(seats[scene.REVIEWER], intent)
                await scene.approve(seats[scene.APPROVER], intent)
                opened = await scene.attempt(worker, epoch, intent, "k1")
                done = scene.outcome(OutcomeState.CONFIRMED_DONE)
                reported = await scene.record(worker, **done)
                own = await scene.record(worker, **scene.verification("observed", "verified"))
                answers += [
                    await _judge(worker, digest, "j1"),
                    await _judge(operator, digest, "j2"),
                    await _judge(seats[JUDGE], digest, "j3"),
                ]
                judged = answers[-1]["seq"]
                answers.append(await _raise(operator, digest, judged, "r1"))
                check = scene.verification("observed", "verified")
                checked = await scene.record(seats[scene.CHECKER], **check)
                answers += [
                    await _raise(seats[BOT], digest, judged, "r2"),
                    await _raise(seats[JUDGE], digest, judged, "r3"),
                    await _raise(operator, digest, 999_999, "r4"),
                    await _judge(seats[JUDGE], digest, "j4", verdict="not_met"),
                ]
                answers += [
                    await _raise(operator, digest, answers[-1]["seq"], "r5"),
                    await _raise(operator, digest, judged, "r6"),
                    await _raise(operator, digest, judged, "r7"),
                    await _criteria(operator, "A3", "c4"),
                ]
                higher = answers[-1]["record"]["digest"]
                answers += [
                    await _judge(seats[JUDGE], higher, "j5"),
                    await _by_hand(operator, "A1", "l1"),
                ]
                answers.append(await _raise(operator, higher, answers[-2]["seq"], "r8", "A3"))
        rows = store.read_since(0, kinds=LEVEL_ROWS)
    finally:
        store.close()
    assert opened["answer"] == "approved_fresh"
    assert [reported["status"], own["status"], checked["status"]] == ["accepted"] * 3
    assert [(item.get("request"), item["answer"]) for item in answers] == [
        ("criteria", "criteria_recorded"),
        ("criteria", "not_one_level"),
        ("criteria", "not_permitted"),
        ("judgement", "judge_is_actor"),
        ("judgement", "judge_is_actor"),
        ("judgement", "judgement_recorded"),
        ("raise", "criteria_unmet"),
        ("raise", "not_a_person"),
        ("raise", "not_permitted"),
        ("raise", "judgement_missing"),
        ("judgement", "judgement_recorded"),
        ("raise", "judgement_missing"),
        ("raise", "raised"),
        ("raise", "not_one_level"),
        ("criteria", "criteria_recorded"),
        ("judgement", "judgement_recorded"),
        (None, "level_recorded"),
        ("raise", "not_one_level"),
    ]
    assert [item["payload"] for item in answers[1:4]] == [
        "a raise of project:P goes one level above A1",
        f"{scene.WORKER} holds no operation-level on project:P",
        f"{scene.WORKER} is an actor of the count it would judge",
    ]
    assert answers[6]["payload"] == "verified_effects:file: 0, needs at least 1"
    assert answers[7]["payload"] == f"{BOT} is not a person; a person grants a raise"
    assert answers[11]["payload"] == (
        f"no met judgement at journal seq {answers[10]['seq']} of this criteria"
    )
    assert answers[-1]["payload"] == "a raise of project:P goes one level above A1"
    criteria, judgement, raised = answers[0]["record"], answers[5]["record"], answers[12]
    assert criteria == {
        "scope": "project:P",
        "target": "A2",
        "items": ITEMS,
        "digest": digest,
        "declared_by": OPERATOR,
    }
    assert {key: judgement[key] for key in ("digest", "verdict", "judge", "independence")} == {
        "digest": digest,
        "verdict": "met",
        "judge": JUDGE,
        "independence": "unassessed",
    }
    assert judgement["criteria_seq"] == answers[0]["seq"]
    assert judgement["evidence"]["from_seq"] == answers[0]["seq"]
    assert answers[4]["seq"] <= judgement["evidence"]["to_seq"] < judged
    assert {key: raised["record"][key] for key in ("level", "from", "cause", "granted_by")} == {
        "level": "A2",
        "from": "A1",
        "cause": f"judgement:{judged}",
        "granted_by": OPERATOR,
    }
    assert raised["record"]["epoch"] == 1
    assert raised["record"]["counts"] == {
        "verified_effects:file": 1,
        "incorrect_acceptances": 0,
        "unknown_outcomes": 0,
        "stops_at_rest": 0,
    }
    assert "the judge's independence" in raised["record"]["uncounted"]
    assert [(row.seq, row.payload["request_key"]) for row in rows] == [
        (item["seq"], item["request_key"]) for item in answers
    ]
    assert [row.kind for row in rows if row.kind != EventKind.OPERATION_LEVEL_ANSWERED] == [
        EventKind.LEVEL_CRITERIA,
        EventKind.LEVEL_JUDGEMENT,
        EventKind.LEVEL_JUDGEMENT,
        EventKind.OPERATION_LEVEL,
        EventKind.LEVEL_CRITERIA,
        EventKind.LEVEL_JUDGEMENT,
        EventKind.OPERATION_LEVEL,
    ]


async def test_the_window_starts_after_the_criteria_and_the_step_down_and_survives_a_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "hub.db"
    store = EventStore(path)
    try:
        async with running_hub(_hub(store)) as (_h, uri):
            async with _seats(uri) as seats:
                epoch = await scene.claim(seats[scene.WORKER])
                await _verified(seats, epoch, "op-before", "k1")
                declared = await _criteria(seats[OPERATOR], "A2", "c1")
                digest = declared["record"]["digest"]
                judged = (await _judge(seats[JUDGE], digest, "j1"))["seq"]
                before = await _raise(seats[OPERATOR], digest, judged, "r1")
    finally:
        store.close()
    store = EventStore(path)
    try:
        async with running_hub(_hub(store)) as (_h, uri):
            async with _seats(uri) as seats:
                operator = seats[OPERATOR]
                epoch = await scene.claim(seats[scene.WORKER])
                await _verified(seats, epoch, "op-after", "k2")
                raised = await _raise(operator, digest, judged, "r2")
                lowered = await _by_hand(operator, "A1", "l1")
                stale = await _raise(operator, digest, judged, "r3")
                early = (await _judge(seats[scene.WORKER], digest, "j2"))["seq"]
                emptied = await _raise(operator, digest, early, "r4")
                await _verified(seats, epoch, "op-third", "k3")
                turned = await _raise(operator, digest, early, "r5")
                again = (await _judge(seats[JUDGE], digest, "j3"))["seq"]
                stop = await scene.answer(
                    operator,
                    lambda: operator.agent.send_message(
                        MessageType.OPERATION_STOP, scope="installation", reason="r", idem_key="s1"
                    ),
                    lambda frame: frame.get("type") == MessageType.OPERATION_STOP_RESULT,
                )
                stopped = await _raise(operator, digest, again, "r6")
    finally:
        store.close()
    assert (turned["answer"], turned["payload"]) == (
        "judge_is_actor",
        f"the judge {scene.WORKER} is an actor of the count",
    )
    assert (before["answer"], before["payload"]) == (
        "criteria_unmet",
        "verified_effects:file: 0, needs at least 1",
    )
    assert (raised["answer"], raised["record"]["counts"]["verified_effects:file"]) == ("raised", 1)
    assert (lowered["answer"], lowered["from"]) == ("level_recorded", "A2")
    assert (stale["answer"], stale["payload"]) == (
        "judgement_missing",
        f"the judgement at journal seq {judged} predates the count's window",
    )
    assert (emptied["answer"], emptied["payload"]) == (
        "criteria_unmet",
        "verified_effects:file: 0, needs at least 1",
    )
    assert (stopped["answer"], stopped["payload"]) == (
        "scope_stopped",
        f"the scope installation is stopped (journal seq {stop['seq']})",
    )


async def test_an_enforcing_hub_gates_each_request_and_a_repeat_replays(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        hub = SynapseHub(journal=store, acl_policy=_policy(), role_grants=GRANTS, require_acl=True)
        async with running_hub(hub) as (_h, uri):
            worker = await connect_agent(scene.WORKER, uri)
            operator = await connect_agent(OPERATOR, uri)
            judge = await connect_agent(JUDGE, uri)
            try:
                recorded = await _criteria(operator, "A2", "c1")
                repeat = await _criteria(operator, "A2", "c1")
                changed = await scene.answer(
                    operator,
                    lambda: operator.agent.send_message(
                        MessageType.OPERATION_LEVEL_CRITERIA,
                        scope="project:P",
                        target="A2",
                        items=ITEMS[:1],
                        idem_key="c1",
                    ),
                    lambda frame: frame.get("error_code") == "idempotency_conflict",
                )
                digest = recorded["record"]["digest"]
                denied = await scene.answer(
                    worker,
                    lambda: worker.agent.send_message(
                        MessageType.OPERATION_LEVEL_JUDGEMENT,
                        scope="project:P",
                        digest=digest,
                        verdict="met",
                        idem_key="w1",
                    ),
                    lambda frame: frame.get("type") == MessageType.ERROR and "acl_reason" in frame,
                )
                judged = await _judge(judge, digest, "j1")
                unknown = await _judge(judge, "f" * 64, "j2")
                malformed = await _ask(
                    operator,
                    MessageType.OPERATION_LEVEL_CRITERIA,
                    "c2",
                    scope="project:P",
                    target="A2",
                    items=[{"item": "conformance_cases", "min": 1}],
                )
            finally:
                await close_agents(worker, operator, judge)
        rows = store.read_since(0, kinds=LEVEL_ROWS)
    finally:
        store.close()
    async with running_hub(SynapseHub(acl_policy=_policy(), role_grants=GRANTS)) as (_h, uri):
        operator = await connect_agent(OPERATOR, uri)
        try:
            no_journal = await _criteria(operator, "A2", "c3")
        finally:
            await close_agents(operator)
    assert recorded["answer"] == "criteria_recorded"
    assert (repeat["seq"], repeat["live"]) == (recorded["seq"], {"replayed": True})
    assert changed["error_code"] == "idempotency_conflict"
    assert denied["payload"] == "access denied: operation-judge on level-scope:project:P"
    assert judged["answer"] == "judgement_recorded"
    assert (unknown["answer"], unknown["payload"]) == (
        "criteria_unknown",
        f"no criteria {'f' * 64} for project:P",
    )
    assert (malformed["answer"], malformed["scope"]) == ("invalid_request", "")
    assert malformed["payload"] == "an item is not in the countable vocabulary"
    assert [row.kind for row in rows] == [
        EventKind.LEVEL_CRITERIA,
        EventKind.LEVEL_JUDGEMENT,
        EventKind.OPERATION_LEVEL_ANSWERED,
    ]
    assert (no_journal["answer"], no_journal["scope"]) == ("no_journal", "project:P")
