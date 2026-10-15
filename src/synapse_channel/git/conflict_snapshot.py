# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — bounded read-only conflict snapshot transport
"""Recover one missing read-only snapshot without replaying a mutation."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from synapse_channel.client.owner_registration import close_owner, register_owner
from synapse_channel.core.protocol import MessageType
from synapse_channel.git.gitclaim import AgentFactory


async def read_conflict_snapshot(
    *,
    uri: str,
    name: str,
    token: str | None,
    agent_factory: AgentFactory,
    ready_timeout: float,
    attempts: int,
    poll_interval: float,
) -> tuple[bool, dict[str, Any] | None]:
    """Read state with at most one retry inside the caller's existing response budget.

    Parameters
    ----------
    uri, name : str
        Hub address and requesting identity.
    token : str or None
        Existing connection credential.
    agent_factory : callable
        Construct the same client profile used by the conflict command.
    ready_timeout : float
        Unchanged welcome deadline in seconds.
    attempts : int
        Original response polling allowance.
    poll_interval : float
        Original seconds per polling attempt; their product remains the deadline.

    Returns
    -------
    tuple
        Connection readiness and the actually received state, or ``None`` on loss.
        Only the read-only state request can be repeated, never a lease mutation.
    """
    snapshots: list[dict[str, Any]] = []
    received = asyncio.Event()

    async def collect(data: dict[str, Any]) -> None:
        """Wake only for an actual state snapshot, not registration or heartbeat traffic."""
        if data.get("type") == MessageType.STATE_SNAPSHOT:
            snapshots.append(data.get("snapshot", {}))
            received.set()

    agent = agent_factory(name, collect, uri=uri, verbose=False, token=token)
    listener = await register_owner(agent, ready_timeout)
    try:
        if listener is None:
            return False, None
        budget = max(0.0, attempts * poll_interval)
        if budget == 0:
            await agent.request_state()
            return True, None

        async def read() -> None:
            """Repeat this pure query once after half the bounded response allowance."""
            await agent.request_state()
            try:
                await asyncio.wait_for(received.wait(), timeout=budget / 2)
            except asyncio.TimeoutError:
                await agent.request_state()
                await received.wait()

        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(read(), timeout=budget)
        return True, snapshots[-1] if snapshots else None
    finally:
        agent.running = False
        if agent.connection is not None:
            with contextlib.suppress(Exception):
                await agent.connection.close()
        await close_owner(agent, listener)
