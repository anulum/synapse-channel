# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — FENCE-01 narrow slice: opt-in strict lease-epoch fencing
"""A lease mutation without its epoch is unchecked by default and refused under strict fencing.

The epoch is the lease's fencing token: a writer that names a superseded epoch
is refused. The epoch was optional, so a writer that names none was not checked
at all. These tests drive real hubs through the public frames. On a default hub,
a stale writer's epoch-less release still succeeds, which is the gap. With
``require_fencing_epoch`` the same release is refused. The shipped client sends
its current epoch on its own, so it keeps working under strict fencing.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from cli_processes_helpers import _hub_ns
from cli_processes_hub_helpers import _close_runner
from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub
from synapse_channel import cli_processes
from synapse_channel.cli import build_parser
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.handlers.leasing import FENCING_EPOCH_REQUIRED
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.protocol import MessageType


def _is(msg_type: str) -> Callable[[dict[str, Any]], bool]:
    return lambda message: message.get("type") == msg_type


async def _claim(handle: AgentHandle, task_id: str) -> dict[str, Any]:
    await handle.agent.claim(task_id, worktree="/repo", paths=[task_id])
    return await handle.recorder.wait_for(
        lambda m: m.get("type") == MessageType.CLAIM_GRANTED and m.get("task_id") == task_id
    )


async def _handoff(giver: AgentHandle, receiver: AgentHandle, task_id: str) -> dict[str, Any]:
    await giver.agent.handoff(task_id, receiver.agent.name)
    return await receiver.recorder.wait_for(
        lambda m: (
            m.get("type") == MessageType.HANDOFF_GRANTED
            and m.get("owner") == receiver.agent.name
            and m.get("task_id") == task_id
        )
    )


@pytest.mark.parametrize("strict", [False, True])
async def test_an_epochless_stale_release_is_refused_only_under_strict_fencing(
    strict: bool,
) -> None:
    async with running_hub(SynapseHub(require_fencing_epoch=strict)) as (_hub, uri):
        alice = await connect_agent("P/alice", uri)
        bob = await connect_agent("P/bob", uri)
        try:
            first = await _claim(alice, "T1")
            await _handoff(alice, bob, "T1")
            back = await _handoff(bob, alice, "T1")
            alice.recorder.messages.clear()
            # a stale writer of alice's first lease names its old epoch: always refused
            await alice.agent.send_message(
                MessageType.RELEASE, target="System", task_id="T1", epoch=first["epoch"]
            )
            stale = await alice.recorder.wait_for(
                lambda m: m.get("type") == MessageType.RELEASE_DENIED
            )
            # the same writer naming no epoch at all
            await alice.agent.send_message(MessageType.RELEASE, target="System", task_id="T1")
            outcome = await alice.recorder.wait_for(
                lambda m: (
                    m.get("type") in (MessageType.RELEASE_DENIED, MessageType.RELEASE_GRANTED)
                    and m.get("task_id") == "T1"
                    and m is not stale
                )
            )
        finally:
            await close_agents(alice, bob)
    assert back["epoch"] > first["epoch"]
    if strict:
        assert outcome["type"] == MessageType.RELEASE_DENIED
        assert outcome["payload"] == FENCING_EPOCH_REQUIRED
    else:
        assert outcome["type"] == MessageType.RELEASE_GRANTED


async def test_every_covered_mutation_needs_the_epoch_under_strict_fencing() -> None:
    async with running_hub(SynapseHub(require_fencing_epoch=True)) as (_hub, uri):
        alice = await connect_agent("P/alice", uri)
        try:
            await _claim(alice, "T2")
            refusals = []
            frames: tuple[tuple[str, str, dict[str, Any]], ...] = (
                (MessageType.TASK_UPDATE, MessageType.ERROR, {"status": "working"}),
                (MessageType.CHECKPOINT, MessageType.CHECKPOINT_DENIED, {"checkpoint": "x"}),
                (MessageType.HANDOFF, MessageType.HANDOFF_DENIED, {"to_agent": "P/alice"}),
                (MessageType.RELEASE, MessageType.RELEASE_DENIED, {}),
            )
            for msg_type, denied, extra in frames:
                alice.recorder.messages.clear()
                await alice.agent.send_message(msg_type, target="System", task_id="T2", **extra)
                refusals.append(await alice.recorder.wait_for(_is(denied)))
        finally:
            await close_agents(alice)
    assert [refusal["payload"] for refusal in refusals] == [FENCING_EPOCH_REQUIRED] * 4
    assert {refusal["task_id"] for refusal in refusals} == {"T2"}


async def test_the_shipped_client_sends_its_own_epoch_and_passes_strict_fencing() -> None:
    async with running_hub(SynapseHub(require_fencing_epoch=True)) as (hub, uri):
        alice = await connect_agent("P/alice", uri)
        bob = await connect_agent("P/bob", uri)
        try:
            grant = await _claim(alice, "T3")
            assert alice.agent.lease_epochs == {"T3": grant["epoch"]}
            assert "T3" not in bob.agent.lease_epochs  # another seat's grant is not ours
            await alice.agent.update_task("T3", status="working")
            await alice.recorder.wait_for(lambda m: m.get("type") == MessageType.TASK_UPDATED)
            await alice.agent.save_checkpoint("T3", "step 1")
            await alice.recorder.wait_for(lambda m: m.get("type") == MessageType.CHECKPOINT_SAVED)
            received = await _handoff(alice, bob, "T3")
            assert bob.agent.lease_epochs["T3"] == received["epoch"]
            await alice.recorder.wait_for(
                lambda m: m.get("type") == MessageType.HANDOFF_GRANTED and m.get("owner") == "P/bob"
            )
            assert "T3" not in alice.agent.lease_epochs  # handed away, so forgotten
            await bob.agent.release("T3")
            await bob.recorder.wait_for(lambda m: m.get("type") == MessageType.RELEASE_GRANTED)
        finally:
            await close_agents(alice, bob)
    assert "T3" not in hub.state.claims
    assert bob.agent.lease_epochs == {}  # released, so forgotten


async def test_a_client_holding_no_lease_sends_no_epoch_and_strict_refuses_it() -> None:
    async with running_hub(SynapseHub(require_fencing_epoch=True)) as (_hub, uri):
        alice = await connect_agent("P/alice", uri)
        mallory = await connect_agent("P/mallory", uri)
        try:
            await _claim(alice, "T4")
            refusals = []
            mutations: tuple[tuple[Callable[[], Awaitable[None]], str], ...] = (
                (lambda: mallory.agent.update_task("T4", status="done"), MessageType.ERROR),
                (
                    lambda: mallory.agent.save_checkpoint("T4", "x"),
                    MessageType.CHECKPOINT_DENIED,
                ),
                (lambda: mallory.agent.handoff("T4", "P/mallory"), MessageType.HANDOFF_DENIED),
                (lambda: mallory.agent.release("T4"), MessageType.RELEASE_DENIED),
            )
            for mutate, denied in mutations:
                mallory.recorder.messages.clear()
                await mutate()
                refusals.append(await mallory.recorder.wait_for(_is(denied)))
        finally:
            await close_agents(alice, mallory)
    assert mallory.agent.lease_epochs == {}
    assert [refusal["payload"] for refusal in refusals] == [FENCING_EPOCH_REQUIRED] * 4


def test_the_hub_parser_accepts_the_fencing_flag() -> None:
    parser = build_parser()
    assert parser.parse_args(["hub", "--require-fencing-epoch"]).require_fencing_epoch is True
    assert parser.parse_args(["hub"]).require_fencing_epoch is False


def test_the_hub_command_threads_the_fencing_flag() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    for flag in (True, False):
        assert (
            cli_processes._cmd_hub(
                _hub_ns(require_fencing_epoch=flag), runner=_close_runner, hub_factory=build_hub
            )
            == 0
        )
        assert captured["require_fencing_epoch"] is flag


async def test_the_client_keeps_only_well_formed_epochs_of_leases_it_holds() -> None:
    agent = SynapseAgent("P/alice", verbose=False)

    async def frame(**fields: Any) -> None:
        await agent._dispatch(json.dumps(fields))

    await frame(type=MessageType.CLAIM_GRANTED, task_id="T", owner="P/alice", epoch=3)
    # malformed frames change nothing: no task id, a boolean or text epoch, a foreign claim
    await frame(type=MessageType.CLAIM_GRANTED, task_id=7, owner="P/alice", epoch=9)
    await frame(type=MessageType.CLAIM_GRANTED, task_id="T", owner="P/alice", epoch=True)
    await frame(type=MessageType.CLAIM_GRANTED, task_id="T", owner="P/alice", epoch="9")
    await frame(type=MessageType.CLAIM_GRANTED, task_id="T", owner="P/bob", epoch=9)
    assert agent.lease_epochs == {"T": 3}
    await frame(type=MessageType.HANDOFF_GRANTED, task_id="T", owner="P/alice", epoch=5)
    assert agent.lease_epochs == {"T": 5}
    await frame(type=MessageType.HANDOFF_GRANTED, task_id="T", owner="P/bob", epoch=6)
    assert agent.lease_epochs == {}
    await frame(type=MessageType.CLAIM_GRANTED, task_id="U", owner="P/alice", epoch=1)
    await frame(type=MessageType.RELEASE_GRANTED, task_id="U", owner="P/alice")
    assert agent.lease_epochs == {}
