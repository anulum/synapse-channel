# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a component's own state declared, attempted and verified on a live hub
"""A ``component_state`` operation closes only on another identity's ``component_read``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import operation_attempt_scenario as scene
from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub
from synapse_channel.core.execution_outcome import OutcomeState
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.result_verification_record import ResultVerification

TARGET = {
    "host": "ws",
    "vendor": "acme",
    "component": "editor",
    "component_version": "1.2.3",
    "os": "linux",
    "transport": "cli",
    "observed_by": "setting.read",
    "expected_sha256": "b" * 64,
}


async def _declare(
    worker: AgentHandle, epoch: int, operation_id: str, **changes: Any
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "record": "declare",
        "task_id": scene.TASK,
        "operation_id": operation_id,
        "epoch": epoch,
        "effect": "component_state",
        "effect_target": TARGET,
        "intent": scene.statement(pre_state="c" * 64),
    }
    fields.update(changes)
    return await scene.record(worker, **fields)


def _verification(
    verifier: str, result: str, operation_id: str = scene.OPERATION
) -> dict[str, Any]:
    body = ResultVerification(
        operation_id, verifier, "confirmed_done", "observed", result, "component read"
    ).to_json()
    return {
        "record": "verification",
        "task_id": scene.TASK,
        "operation_id": operation_id,
        "verification": body,
    }


async def _state(worker: AgentHandle) -> dict[str, Any]:
    return await scene.answer(
        worker,
        lambda: worker.agent.send_message(
            MessageType.OPERATION_STATE,
            task_id=scene.TASK,
            operation_id=scene.OPERATION,
            request_id="s1",
        ),
        lambda frame: frame.get("type") == MessageType.OPERATION_STATE_RESULT,
    )


async def test_a_component_state_closes_only_on_another_identity_s_component_read(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        hub = SynapseHub(journal=store, acl_policy=scene.policy())
        async with running_hub(hub) as (_h, uri):
            names = (scene.WORKER, scene.REVIEWER, scene.APPROVER, scene.CHECKER)
            seats = {name: await connect_agent(name, uri) for name in names}
            try:
                worker, checker = seats[scene.WORKER], seats[scene.CHECKER]
                epoch = await scene.claim(worker)
                refused = [
                    await _declare(worker, epoch, "op-x", effect_target={**TARGET, "path": "p"}),
                    await _declare(worker, epoch, "op-y", intent=scene.statement(pre_state="nope")),
                ]
                declared = await _declare(worker, epoch, scene.OPERATION)
                digest = declared["intent_sha256"]
                await scene.review(seats[scene.REVIEWER], digest)
                await scene.approve(seats[scene.APPROVER], digest)
                opened = await scene.attempt(worker, epoch, digest, "k1")
                done = scene.outcome(OutcomeState.CONFIRMED_DONE)
                answers = [
                    await scene.record(worker, **done),
                    await scene.record(worker, **_verification("component_read", "verified")),
                ]
                own = await _state(worker)
                answers += [
                    await scene.record(checker, **_verification("file_change", "verified")),
                    await scene.record(checker, **_verification("component_read", "verified")),
                ]
                closed = await _state(worker)
            finally:
                await close_agents(*seats.values())
    finally:
        store.close()
    assert [(item["status"], item["reason"]) for item in refused] == [
        ("refused", "invalid_request"),
        ("refused", "intent_invalid"),
    ]
    assert declared["status"] == "accepted"
    assert opened["answer"] == "approved_fresh"
    assert [item["status"] for item in answers] == ["accepted", "accepted", "refused", "accepted"]
    assert answers[2]["reason"] == "verifier_mismatch"
    assert (own["effect"], own["effect_target"]) == ("component_state", TARGET)
    assert own["closed"] is False
    assert closed["closed"] is True
