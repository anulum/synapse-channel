# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — end-to-end tests for the task-aware wait lifecycle

from __future__ import annotations

import asyncio

from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub


async def _claim(agent: AgentHandle, task_id: str) -> None:
    # Disjoint file scopes so several agents can hold tasks at once (a bare
    # whole-worktree claim contends with every other bare claim).
    await agent.agent.claim(task_id, paths=[f"{task_id.lower()}.py"])
    await agent.recorder.wait_for(
        lambda m: m.get("type") == "claim_granted" and m.get("task_id") == task_id
    )


async def _release(agent: AgentHandle, task_id: str) -> None:
    await agent.agent.release(task_id)
    await agent.recorder.wait_for(
        lambda m: m.get("type") == "release_granted" and m.get("task_id") == task_id
    )


async def _wait_granted(agent: AgentHandle, task_id: str) -> None:
    await agent.agent.request_wait(task_id)
    await agent.recorder.wait_for(
        lambda m: m.get("type") == "wait_granted" and m.get("task_id") == task_id
    )


async def _wait_denied(agent: AgentHandle, task_id: str, fragment: str) -> None:
    await agent.agent.request_wait(task_id)
    denied = await agent.recorder.wait_for(
        lambda m: m.get("type") == "wait_denied" and m.get("task_id") == task_id
    )
    assert fragment in denied["payload"]


async def test_unrelated_claim_preserves_the_wait_and_the_cycle_check_sees_it() -> None:
    """WF-4: a claim on task U must not erase the claimant's wait on task T."""
    async with running_hub() as (hub, uri):
        a = await connect_agent("A", uri)
        b = await connect_agent("B", uri)
        try:
            await _claim(a, "T1")
            await _wait_granted(b, "T1")
            assert hub._waits["B"] == {"T1"}

            await _claim(b, "T2")
            assert hub._waits["B"] == {"T1"}  # survived the unrelated claim

            # The preserved edge is real: A waiting on B's task closes the loop.
            await _wait_denied(a, "T2", "would deadlock")
        finally:
            await close_agents(a, b)


async def test_renewal_preserves_the_wait_edge() -> None:
    """WF-4: re-claiming (renewing) an unrelated task keeps the wait alive."""
    async with running_hub() as (hub, uri):
        a = await connect_agent("A", uri)
        b = await connect_agent("B", uri)
        try:
            await _claim(a, "T1")
            await _wait_granted(b, "T1")
            await _claim(b, "T2")
            await _claim(b, "T2")  # renewal
            assert hub._waits["B"] == {"T1"}
        finally:
            await close_agents(a, b)


async def test_satisfied_wait_is_cleared_by_claiming_that_task() -> None:
    """The waiter claiming the very task it waits for clears only that edge."""
    async with running_hub() as (hub, uri):
        a = await connect_agent("A", uri)
        b = await connect_agent("B", uri)
        try:
            await _claim(a, "T1")
            await _claim(a, "T9")
            await _wait_granted(b, "T1")
            await _wait_granted(b, "T9")
            await _release(a, "T1")
            await _claim(b, "T1")
            assert hub._waits["B"] == {"T9"}
        finally:
            await close_agents(a, b)


async def test_handoff_repoints_the_wait_at_the_new_holder() -> None:
    """WF-4/5: a wait follows the task, so handoff re-aims it at the new owner."""
    async with running_hub() as (hub, uri):
        a = await connect_agent("A", uri)
        b = await connect_agent("B", uri)
        c = await connect_agent("C", uri)
        try:
            await _claim(a, "T1")
            await _claim(b, "T2")
            await _wait_granted(c, "T1")
            await a.agent.handoff("T1", "C")
            await a.recorder.wait_for(
                lambda m: m.get("type") == "handoff_granted" and m.get("task_id") == "T1"
            )
            # C now holds T1 and waits on it was satisfied by the handoff itself...
            assert "C" not in hub._waits or "T1" not in hub._waits.get("C", set())
            # ...and B's reciprocal wait on A is now SAFE: nothing points at A.
            await _wait_granted(b, "T1")  # B waits on C's task — fine, no cycle
        finally:
            await close_agents(a, b, c)


async def test_release_prunes_the_wait_edge_and_kills_false_deadlocks() -> None:
    """WF-5: releasing the waited task removes the edge; no false refusal later."""
    async with running_hub() as (hub, uri):
        a = await connect_agent("A", uri)
        b = await connect_agent("B", uri)
        try:
            await _claim(a, "T1")
            await _wait_granted(b, "T1")
            await _release(a, "T1")
            assert "B" not in hub._waits or "T1" not in hub._waits.get("B", set())

            # With the stale edge gone, reciprocal waits on fresh tasks are legal.
            await _claim(a, "T3")
            await _claim(b, "T4")
            await _wait_granted(b, "T3")
            await _wait_denied(a, "T4", "would deadlock")  # a REAL cycle still refuses
        finally:
            await close_agents(a, b)


async def test_disconnect_drops_only_the_waiters_own_edges() -> None:
    """A disconnecting waiter loses its edges; other waiters are unaffected."""
    async with running_hub() as (hub, uri):
        a = await connect_agent("A", uri)
        b = await connect_agent("B", uri)
        c = await connect_agent("C", uri)
        try:
            await _claim(a, "T1")
            await _wait_granted(b, "T1")
            await _wait_granted(c, "T1")
            await b.close()
            for _ in range(100):
                if "B" not in hub._waits:
                    break
                await asyncio.sleep(0.02)
            assert "B" not in hub._waits
            assert hub._waits.get("C") == {"T1"}
        finally:
            await close_agents(a, c)


async def test_claiming_the_only_waited_task_pops_the_waiter() -> None:
    """The set empties: claiming the sole waited task removes the waiter row."""
    async with running_hub() as (hub, uri):
        a = await connect_agent("A", uri)
        b = await connect_agent("B", uri)
        try:
            await _claim(a, "T1")
            await _wait_granted(b, "T1")
            await _release(a, "T1")
            await _claim(b, "T1")
            assert "B" not in hub._waits
        finally:
            await close_agents(a, b)


async def test_handoff_of_the_only_waited_task_pops_the_recipient() -> None:
    """Receiving the sole waited task empties and removes the recipient row."""
    async with running_hub() as (hub, uri):
        a = await connect_agent("A", uri)
        b = await connect_agent("B", uri)
        try:
            await _claim(a, "T1")
            await _wait_granted(b, "T1")
            await a.agent.handoff("T1", "B")
            await a.recorder.wait_for(
                lambda m: m.get("type") == "handoff_granted" and m.get("task_id") == "T1"
            )
            assert "B" not in hub._waits
        finally:
            await close_agents(a, b)


async def test_expiry_prunes_the_wait_edge_on_the_next_heartbeat() -> None:
    """WF-5: a waited task losing its lease prunes the edge — no false deadlock."""
    import time

    async with running_hub() as (hub, uri):
        a = await connect_agent("A", uri)
        b = await connect_agent("B", uri)
        try:
            await _claim(a, "T1")
            await _wait_granted(b, "T1")
            assert hub._waits["B"] == {"T1"}
            # Backdate the lease (heap-scheduled) so the next frame's heartbeat
            # expires it and prunes B's edge.
            hub.state.claim("A", "T1", ttl_seconds=30.0, now=time.time() - 40.0)
            await a.agent.chat("tick")
            for _ in range(200):
                if "T1" not in hub.state.claims and "B" not in hub._waits:
                    break
                await asyncio.sleep(0.02)
            assert "T1" not in hub.state.claims
            assert "B" not in hub._waits
        finally:
            await close_agents(a, b)


def test_claim_discard_clears_the_edge_even_without_a_prior_prune() -> None:
    """Defensive belt: a waiter claiming its waited task clears the edge directly."""
    from synapse_channel.core.handlers.leasing import apply_claim
    from synapse_channel.core.hub import SynapseHub

    hub = SynapseHub(hub_id="syn-test")
    hub.state.claim("A", "T1")
    hub._waits["B"] = {"T1", "T9"}
    del hub.state.claims["T1"]  # freed out-of-band: no release/expiry prune ran
    application = apply_claim(hub, "B", {"task_id": "T1"})
    assert application.claim is not None
    assert hub._waits["B"] == {"T9"}
    application2 = apply_claim(hub, "B", {"task_id": "T9"})
    assert application2.claim is not None
    assert "B" not in hub._waits


async def test_handoff_discards_only_the_handed_task_from_a_multi_edge_wait() -> None:
    """A recipient with several waits keeps the rest when one is satisfied."""
    async with running_hub() as (hub, uri):
        a = await connect_agent("A", uri)
        b = await connect_agent("B", uri)
        try:
            await _claim(a, "T1")
            await _claim(a, "T9")
            await _wait_granted(b, "T1")
            await _wait_granted(b, "T9")
            await a.agent.handoff("T1", "B")
            await a.recorder.wait_for(
                lambda m: m.get("type") == "handoff_granted" and m.get("task_id") == "T1"
            )
            assert hub._waits["B"] == {"T9"}
        finally:
            await close_agents(a, b)
