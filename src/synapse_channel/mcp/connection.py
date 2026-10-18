# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — MCP backend connection lifetime
"""Keep one MCP backend identity connected without replaying its operations."""

from __future__ import annotations

import asyncio
from secrets import randbelow

from synapse_channel.client.agent import SynapseAgent

_INITIAL_DELAY = 0.25
_MAXIMUM_DELAY = 5.0
_STABLE_SECONDS = 1.0


async def maintain_connection(agent: SynapseAgent) -> None:
    """Reconnect one owned agent until its MCP transport shuts down.

    Parameters
    ----------
    agent : SynapseAgent
        Existing backend client whose identity, credentials, roles and mailbox
        cursor survive each serial connection attempt. No other task may own
        its connection loop.

    Returns
    -------
    None
        The coroutine runs until cancelled; it never resubmits a tool call,
        mutation or application message.

    Raises
    ------
    asyncio.CancelledError
        The owner stops the MCP transport. The current attempt finishes its
        own connection and heartbeat cleanup before cancellation propagates.
    RuntimeError
        Another task already owns the agent's connection attempt.

    Notes
    -----
    Network failures handled by the agent end one attempt. Retries back off
    from 0.25 to five seconds, with 20 percent jitter to spread simultaneous
    reconnects. An attempt lasting at least one second resets the delay.
    Cancellation interrupts both attempts and retry waits.
    """
    loop = asyncio.get_running_loop()
    delay = _INITIAL_DELAY
    while True:
        started = loop.time()
        await agent.connect()
        if loop.time() - started >= _STABLE_SECONDS:
            delay = _INITIAL_DELAY
        await asyncio.sleep(delay * (0.8 + randbelow(401) / 1000))
        delay = min(delay * 2, _MAXIMUM_DELAY)
