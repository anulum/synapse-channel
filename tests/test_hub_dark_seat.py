# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — live hub dark-seat alert acceptance test
"""Prove a real hub broadcasts and re-arms exact-identity dark-seat alerts."""

from __future__ import annotations

import asyncio
from typing import Any

from hub_e2e_helpers import AgentHandle, Recorder, close_agents, connect_agent, running_hub
from synapse_channel.core.dark_seat import DarkSeatMonitor
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.wake_capability import WAKE_PASSIVE


async def _wait_for_alert_count(recorder: Recorder, count: int) -> list[dict[str, Any]]:
    """Return once ``recorder`` contains at least ``count`` dark-seat alerts."""
    for _ in range(300):
        alerts = [
            message
            for message in recorder.messages
            if message.get("type") == MessageType.DARK_SEAT_ALERT
        ]
        if len(alerts) >= count:
            return alerts
        await asyncio.sleep(0.01)
    raise TimeoutError(f"expected {count} dark-seat alert(s)")


async def test_live_hub_alerts_an_unarmed_claimant_and_realerts_after_waiter_loss() -> None:
    """An exact waiter clears an episode; losing it starts a new broadcast episode."""
    hub = SynapseHub(hub_id="syn-dark-live")
    hub._dark_seats = DarkSeatMonitor(
        claims=lambda: hub.state.claims,
        tasks=lambda: hub.blackboard.tasks,
        has_live_waiter=hub._liveness.has_live_waiter,
        broadcast=hub._broadcast,
        system=hub._system,
        grace_seconds=0.0,
        poll_seconds=0.01,
    )
    handles: list[AgentHandle] = []

    async with running_hub(hub) as (_, uri):
        watcher = await connect_agent("WATCHER", uri)
        claimant = await connect_agent("DARK", uri)
        handles.extend((watcher, claimant))
        try:
            await claimant.agent.claim("WORK")
            first = (await _wait_for_alert_count(watcher.recorder, 1))[0]
            assert first["identity"] == "DARK"
            assert first["claims"] == ["WORK"]
            assert first["tasks"] == []
            assert first["target"] == "all"
            assert "--name=DARK --for=DARK" in str(first["remedy"])

            waiter = await connect_agent(
                "DARK-rx",
                uri,
                wake_capability=WAKE_PASSIVE,
            )
            handles.append(waiter)
            assert await hub._dark_seats.check() == ()
            await waiter.close()
            handles.remove(waiter)

            alerts = await _wait_for_alert_count(watcher.recorder, 2)
            assert [alert["identity"] for alert in alerts[:2]] == ["DARK", "DARK"]
        finally:
            await close_agents(*handles)
