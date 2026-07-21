# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded soak of the waiter/liveness bookkeeping under churn
"""A bounded soak of the hub's waiter/liveness bookkeeping under connect churn.

The hub tracks, per connected identity, a live socket (``agent_sockets``) and a
last-reaction timestamp (``RecipientLiveness._last_reaction``). A disconnect must
prune both; a leak there would let the maps grow without bound across a long-lived
hub's reconnect traffic — a slow failure an example test with a handful of clients
never reaches. This soak drives many distinct connect→arm-waiter→disconnect cycles
and asserts the bookkeeping returns to baseline rather than growing with the cycle
count.

The cycle count is bounded by ``SYNAPSE_SOAK_ITERATIONS`` (default a handful, so the
ordinary suite runs it cheaply); the scheduled ``soak`` workflow sets a much larger
value, exactly as the fuzz lane scales ``SYNAPSE_FUZZ_EXAMPLES``. The behaviour is
identical at any bound — only the confidence grows — so the same test is both a
unit-cost regression guard and the soak lane's workload.
"""

from __future__ import annotations

import asyncio
import os

from hub_e2e_helpers import connect_agent, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.wake_capability import WAKE_PASSIVE

_DEFAULT_ITERATIONS = 15


def _soak_iterations() -> int:
    """Return the soak cycle count, bounded and read from the environment.

    ``SYNAPSE_SOAK_ITERATIONS`` scales the workload for the scheduled lane; an
    absent, non-integer, or sub-one value falls back to a cheap default so the
    ordinary test suite always runs a real, if small, soak.
    """
    raw = os.environ.get("SYNAPSE_SOAK_ITERATIONS", "")
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_ITERATIONS
    return max(value, 1)


async def _settle_until_pruned(hub: SynapseHub, name: str, *, timeout: float) -> bool:
    """Poll until ``name`` has left ``hub.agent_sockets`` or the timeout lapses."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if name not in hub.agent_sockets:
            return True
        await asyncio.sleep(0.01)
    return name not in hub.agent_sockets


async def test_waiter_liveness_bookkeeping_stays_bounded_under_connect_churn() -> None:
    """Distinct connect/disconnect cycles must not leak per-identity liveness state."""
    iterations = _soak_iterations()
    async with running_hub() as (hub, uri):
        base_sockets = len(hub.agent_sockets)
        base_reactions = len(hub._recipient_liveness._last_reaction)
        peak_sockets = base_sockets

        for index in range(iterations):
            name = f"SOAK-{index}"
            # A passive-wake agent arms the waiter path; waiting for its presence
            # guarantees the hub has registered the socket before we inspect it.
            handle = await connect_agent(name, uri, wake_capability=WAKE_PASSIVE)
            # While connected, the identity is tracked exactly once.
            assert name in hub.agent_sockets
            peak_sockets = max(peak_sockets, len(hub.agent_sockets))
            await handle.close()
            # The hub must observe the closed socket and prune it before the next cycle.
            assert await _settle_until_pruned(hub, name, timeout=3.0)

        # After the churn every disconnected identity is pruned: the live-socket and
        # last-reaction maps sit at baseline, never grown to the cycle count.
        assert len(hub.agent_sockets) == base_sockets, "agent_sockets leaked across cycles"
        assert len(hub._recipient_liveness._last_reaction) <= base_reactions, (
            "recipient-liveness reactions leaked across cycles"
        )
        # Cycles are sequential, so at most one soak client is ever live at once —
        # the peak never scales with the iteration count either.
        assert peak_sockets <= base_sockets + 1, "concurrent live sockets exceeded one"
