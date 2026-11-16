# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — a real hub journal holding an operation in every state the report gives
"""Write, through a real hub's own requests, one operation in each state of the operation report.

The report's tests and the dashboard feed's tests read the same journal, so both
state what the hub wrote and nothing a test made by hand.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import operation_attempt_scenario as scene
from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub
from synapse_channel.core.acl import OPERATION_SEAL, AclPolicy, AclRule
from synapse_channel.core.approvals import APPROVAL_NOTE_KIND, format_approval_note
from synapse_channel.core.attempt_markers import LANDED, AttemptMarker
from synapse_channel.core.execution_outcome import OutcomeState
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore

RECOVERY = "R/recovery"


def policy() -> AclPolicy:
    """Return the scenario's policy plus ``operation-seal`` for the recovery project alone."""
    rules = list(scene.policy().rules)
    rules.append(AclRule(OPERATION_SEAL, "claim", "T*", "R", "the recovery role seals"))
    return AclPolicy(rules)


@asynccontextmanager
async def connected_seats(uri: str) -> AsyncIterator[dict[str, AgentHandle]]:
    """Connect the scenario's seats and the recovery seat, and close them all afterwards."""
    names = (scene.WORKER, scene.REVIEWER, scene.APPROVER, scene.CHECKER, RECOVERY)
    handles = {name: await connect_agent(name, uri) for name in names}
    try:
        yield handles
    finally:
        await close_agents(*handles.values())


async def declared(seats: dict[str, AgentHandle], epoch: int, operation: str, **intent: Any) -> str:
    """Declare ``operation`` with an intent and record the reviewer's accepted verdict on it."""
    digest = await scene.declare(
        seats[scene.WORKER], epoch, operation_id=operation, intent=scene.statement(**intent)
    )
    await scene.review(seats[scene.REVIEWER], digest)
    return digest


async def _decide(seats: dict[str, AgentHandle], digest: str, state: str, reason: str = "") -> None:
    text = format_approval_note(subject=f"sha256:{digest}", state=state, reason=reason)
    await scene.note(seats[scene.APPROVER], APPROVAL_NOTE_KIND, text)


async def _opened(seats: dict[str, AgentHandle], epoch: int, operation: str, key: str) -> str:
    """Declare, review, approve and open one attempt of ``operation``; return its attempt id."""
    digest = await declared(seats, epoch, operation)
    await scene.approve(seats[scene.APPROVER], digest)
    answer = await scene.attempt(seats[scene.WORKER], epoch, digest, key, operation_id=operation)
    assert answer["answer"] == "approved_fresh", answer
    return str(answer["attempt_id"])


async def _record(handle: AgentHandle, fields: dict[str, Any]) -> None:
    result = await scene.record(handle, **fields)
    assert result["status"] == "accepted", result


async def _attempt_states(seats: dict[str, AgentHandle], epoch: int) -> None:
    """Journal one operation in each attempt state through the hub's own requests."""
    worker, checker = seats[scene.WORKER], seats[scene.CHECKER]
    await _opened(seats, epoch, "op-verified", "v1")
    await _record(worker, scene.outcome(OutcomeState.CONFIRMED_DONE, "op-verified"))
    await _record(checker, scene.verification("observed", "verified", "op-verified"))
    await _opened(seats, epoch, "op-ended", "e1")
    await _record(worker, scene.outcome(OutcomeState.CONFIRMED_DONE, "op-ended"))
    await _opened(seats, epoch, "op-reconciled", "r1")
    await _record(worker, scene.outcome(OutcomeState.NOT_STARTED, "op-reconciled"))
    await _record(checker, scene.verification("absent", "not_verified", "op-reconciled"))
    await _opened(seats, epoch, "op-unknown", "u1")
    await _record(worker, scene.outcome(OutcomeState.STARTED, "op-unknown"))
    open_digest = await declared(seats, epoch, "op-open")
    await scene.approve(seats[scene.APPROVER], open_digest)
    await scene.attempt(worker, epoch, open_digest, "o1", operation_id="op-open")
    refused = await scene.attempt(worker, epoch, open_digest, "o2", operation_id="op-open")
    assert refused["answer"] == "attempt_exists", refused
    sealed_digest = await declared(seats, epoch, "op-sealed")
    await scene.approve(seats[scene.APPROVER], sealed_digest)
    opened = await scene.attempt(worker, epoch, sealed_digest, "s1", operation_id="op-sealed")
    marker = AttemptMarker("op-sealed", str(opened["attempt_id"]), sealed_digest)
    seal = {
        "attempt_id": opened["attempt_id"],
        "marker_ref": marker.ref,
        "object_format": "sha1",
        "marker_object": marker.objects("sha1").sealed,
        "observation": LANDED,
    }
    fields = {"record": "seal", "task_id": scene.TASK, "operation_id": "op-sealed", "seal": seal}
    await _record(seats[RECOVERY], fields)


async def _word_states(seats: dict[str, AgentHandle], epoch: int) -> None:
    """Journal one operation in each state that has no attempt."""
    worker = seats[scene.WORKER]
    refused = await declared(seats, epoch, "op-refused")
    await scene.attempt(worker, epoch, refused, "f1", operation_id="op-refused")
    later = await declared(seats, epoch, "op-refused-then-approved")
    await scene.attempt(worker, epoch, later, "f2", operation_id="op-refused-then-approved")
    await _decide(seats, later, "approved", "line one\nforged \x1b[31mred")
    await _decide(seats, await declared(seats, epoch, "op-awaiting"), "requested")
    await _decide(seats, await declared(seats, epoch, "op-approved"), "approved")
    await _decide(seats, await declared(seats, epoch, "op-rejected"), "rejected", "no")
    await scene.declare(worker, epoch, operation_id="op-declared")


async def journal_every_state_async(path: Path) -> None:
    """Write, in the running loop, a hub journal at ``path`` with an operation in every state.

    The cockpit's browser harness calls this from its own loop on the main thread,
    where the hub can install its signal handlers.
    """
    store = EventStore(path)
    try:
        hub = SynapseHub(journal=store, acl_policy=policy())
        async with running_hub(hub) as (_hub, uri), connected_seats(uri) as seats:
            epoch = await scene.claim(seats[scene.WORKER])
            await _attempt_states(seats, epoch)
            await _word_states(seats, epoch)
    finally:
        store.close()


def journal_every_state(path: Path) -> None:
    """Write a hub journal at ``path`` holding one operation in every state the report gives."""
    asyncio.run(journal_every_state_async(path))
