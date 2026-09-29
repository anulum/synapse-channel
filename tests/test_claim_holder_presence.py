# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — claims of disconnected holders on a real hub
"""A disconnected holder keeps its claims for the lease window, then loses them.

Every test runs a real hub with a short ``lease_offline_ttl`` and real agents, so the
claim-and-drop case, the takeover, the reconnect and the journal recovery are exercised
through the public claim path, not by calling state methods.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub
from synapse_channel.core.claim_holder_presence import HOLDER_OFFLINE_ACTOR
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.metrics import collect_hub_metrics
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType

_WINDOW = 0.4


async def _claim(handle: AgentHandle, task: str, paths: list[str]) -> dict[str, Any]:
    await handle.agent.claim(task, worktree="/repo", paths=paths)
    return await handle.recorder.wait_for(
        lambda m: (
            m.get("task_id") == task
            and m.get("type") in (MessageType.CLAIM_GRANTED, MessageType.CLAIM_DENIED)
            and (m.get("type") == MessageType.CLAIM_DENIED or m.get("owner") == handle.agent.name)
        )
    )


async def _claims(handle: AgentHandle) -> dict[str, dict[str, Any]]:
    handle.recorder.messages.clear()
    await handle.agent.send_message(MessageType.STATE_REQUEST, target="System")
    snapshot = await handle.recorder.wait_for(lambda m: m.get("type") == MessageType.STATE_SNAPSHOT)
    return {claim["task_id"]: claim for claim in snapshot["snapshot"]["active_claims"]}


async def test_an_offline_holder_keeps_its_claim_until_the_window_then_loses_it(
    tmp_path: Path,
) -> None:
    """Claim, drop, conflict refused and visible as offline, then released and journalled."""
    store = EventStore(tmp_path / "hub.db")
    hub = SynapseHub(hub_id="syn-n3", journal=store, lease_offline_ttl=_WINDOW)
    async with running_hub(hub) as (_hub, uri):
        holder = await connect_agent("P/holder", uri)
        other = await connect_agent("P/other", uri)
        try:
            assert (await _claim(holder, "T-HOLD", ["src"]))["type"] == MessageType.CLAIM_GRANTED
            await holder.close()
            other.recorder.messages.clear()
            refused = await _claim(other, "T-OTHER", ["src/a.py"])
            assert refused["type"] == MessageType.CLAIM_DENIED
            held = (await _claims(other))["T-HOLD"]
            assert held["holder_online"] is False
            assert held["holder_offline_seconds"] >= 0.0

            await asyncio.sleep(_WINDOW + 0.1)
            other.recorder.messages.clear()
            granted = await _claim(other, "T-OTHER", ["src/a.py"])
            released = await other.recorder.wait_for(
                lambda m: (
                    m.get("type") == MessageType.RELEASE_GRANTED
                    and m.get("released_by") == HOLDER_OFFLINE_ACTOR
                )
            )
            remaining = await _claims(other)
        finally:
            await close_agents(other)
    by_name = {metric.name: metric.value for metric in collect_hub_metrics(hub)}
    store.close()
    assert by_name["synapse_claims_released_abandoned_total"] == 1
    assert granted["type"] == MessageType.CLAIM_GRANTED
    assert (released["task_id"], released["owner"]) == ("T-HOLD", "P/holder")
    assert released["holder_offline_seconds"] >= _WINDOW
    assert set(remaining) == {"T-OTHER"}
    assert remaining["T-OTHER"]["holder_online"] is True
    assert remaining["T-OTHER"]["holder_offline_seconds"] is None

    # The journal agrees: a restarted hub restores only the new holder's claim.
    reopened = EventStore(tmp_path / "hub.db")
    try:
        restored = SynapseHub(hub_id="syn-n3", journal=reopened, lease_offline_ttl=_WINDOW)
        assert set(restored.state.claims) == {"T-OTHER"}
    finally:
        reopened.close()


async def test_a_holder_that_returns_inside_the_window_keeps_its_claims() -> None:
    """Reconnecting before the window ends resets it; nothing is released."""
    hub = SynapseHub(hub_id="syn-n3", lease_offline_ttl=_WINDOW)
    async with running_hub(hub) as (_hub, uri):
        holder = await connect_agent("P/holder", uri)
        other = await connect_agent("P/other", uri)
        try:
            await _claim(holder, "T-HOLD", ["src"])
            await holder.close()
            await asyncio.sleep(_WINDOW / 2)
            holder = await connect_agent("P/holder", uri)
            await asyncio.sleep(_WINDOW / 2 + 0.1)
            other.recorder.messages.clear()
            refused = await _claim(other, "T-OTHER", ["src/a.py"])
            held = (await _claims(other))["T-HOLD"]
        finally:
            await close_agents(holder, other)
    assert refused["type"] == MessageType.CLAIM_DENIED
    assert held["holder_online"] is True
    assert hub.counters.claims_released_abandoned == 0


async def test_after_a_restart_a_restored_claim_counts_from_hub_start(tmp_path: Path) -> None:
    """A hub that restarts cannot know when the holder left, so the window starts at boot."""
    first_store = EventStore(tmp_path / "hub.db")
    first = SynapseHub(hub_id="syn-n3", journal=first_store, lease_offline_ttl=_WINDOW)
    async with running_hub(first) as (_hub, uri):
        holder = await connect_agent("P/holder", uri)
        try:
            await _claim(holder, "T-HOLD", ["src"])
        finally:
            await close_agents(holder)
    first_store.close()

    store = EventStore(tmp_path / "hub.db")
    hub = SynapseHub(hub_id="syn-n3", journal=store, lease_offline_ttl=_WINDOW)
    try:
        async with running_hub(hub) as (_hub, uri):
            other = await connect_agent("P/other", uri)
            try:
                early = await _claim(other, "T-OTHER", ["src/a.py"])
                await asyncio.sleep(_WINDOW + 0.1)
                other.recorder.messages.clear()
                late = await _claim(other, "T-OTHER", ["src/a.py"])
            finally:
                await close_agents(other)
    finally:
        store.close()
    assert early["type"] == MessageType.CLAIM_DENIED
    assert late["type"] == MessageType.CLAIM_GRANTED
    assert set(hub.state.claims) == {"T-OTHER"}


async def test_a_hub_without_a_journal_releases_abandoned_claims_in_memory() -> None:
    """An in-memory hub applies the same window; there is simply nothing to journal."""
    hub = SynapseHub(hub_id="syn-n3", lease_offline_ttl=_WINDOW)
    async with running_hub(hub) as (_hub, uri):
        holder = await connect_agent("P/holder", uri)
        other = await connect_agent("P/other", uri)
        try:
            await _claim(holder, "T-HOLD", ["src"])
            await holder.close()
            await asyncio.sleep(_WINDOW + 0.1)
            other.recorder.messages.clear()
            granted = await _claim(other, "T-OTHER", ["src/a.py"])
        finally:
            await close_agents(other)
    assert granted["type"] == MessageType.CLAIM_GRANTED
    assert set(hub.state.claims) == {"T-OTHER"}
    assert hub.counters.claims_released_abandoned == 1
