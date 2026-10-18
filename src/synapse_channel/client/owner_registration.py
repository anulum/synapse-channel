# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded authenticated owner registration
"""Bound same-owner socket retirement without transferring or taking over identity."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from websockets.exceptions import ConnectionClosed

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.client.agent_dispatch import invoke_message_callback
from synapse_channel.connect_failures import closed_after_ready
from synapse_channel.core.protocol import SENDER_HUB, MessageType


async def _registered_profile(agent: SynapseAgent, timeout: float) -> bool:
    """Verify binding after an open hub's pre-registration welcome."""
    callback = agent.callback
    request_id = uuid.uuid4().hex
    bound = asyncio.Event()
    supports_confirmation = False
    deadline = asyncio.get_running_loop().time() + timeout

    async def collect(data: dict[str, Any]) -> None:
        """Consume this binding probe and preserve the caller's other inbound frames."""
        nonlocal supports_confirmation
        if data.get("type") == MessageType.WELCOME:
            supports_confirmation = bool(data.get("lock_renewal_session"))
        if (
            data.get("type") == MessageType.STATE_SNAPSHOT
            and data.get("sender") == SENDER_HUB
            and data.get("hub_id") == agent.hub_id
            and data.get("target") == agent.name
            and data.get("request_id") == request_id
        ):
            bound.set()
        elif callback is not None:
            await invoke_message_callback(callback, data)

    agent.callback = collect
    try:
        if not await agent.wait_until_ready(timeout=timeout):
            return False
        if not supports_confirmation:
            return not await closed_after_ready(
                agent,
                grace_seconds=min(0.25, max(0.0, deadline - asyncio.get_running_loop().time())),
            )
        await agent.send_message(
            MessageType.STATE_REQUEST,
            target="System",
            payload="snapshot",
            request_id=request_id,
        )
        await bound.wait()
        return True
    except (ConnectionClosed, OSError):
        return False
    finally:
        agent.callback = callback


async def _cancel_registration(
    agent: SynapseAgent,
    listener: asyncio.Task[None],
    ready: asyncio.Task[bool],
) -> bool:
    """Join both tasks and report whether cancellation interrupted the listener."""
    unfinished = not listener.done()
    ready.cancel()
    agent.running = False
    listener.cancel()
    await asyncio.gather(ready, listener, return_exceptions=True)
    return unfinished


async def register_owner(agent: SynapseAgent, timeout: float) -> asyncio.Task[None] | None:
    """Return a ready listener, retrying only exact name conflicts within one budget.

    Parameters
    ----------
    agent : SynapseAgent
        Original authenticated identity, with no takeover enabled.
    timeout : float
        Whole registration allowance, including retirement retries.

    Returns
    -------
    asyncio.Task[None] or None
        Caller-owned live listener, or None after refusal or timeout.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    last_refusal: tuple[int, str] | None = None
    while (remaining := deadline - loop.time()) > 0:
        listener = asyncio.create_task(agent.connect())
        ready = asyncio.create_task(_registered_profile(agent, remaining))
        connected = False
        pending_listener = False
        try:
            done, _ = await asyncio.wait(
                (listener, ready), timeout=remaining, return_when=asyncio.FIRST_COMPLETED
            )
            if ready in done and ready.result() and not listener.done():
                connected = True
                return listener
            if listener in done and not (
                agent.last_close_code == 4009 and agent.last_close_reason == "name conflict"
            ):
                # Preserve the existing complete readiness budget on other
                # failures, including cancellation-safe outage cleanup.
                await asyncio.wait((ready,), timeout=max(0.0, deadline - loop.time()))
            elif ready in done and not ready.result():
                await asyncio.wait((listener,), timeout=max(0.0, deadline - loop.time()))
        finally:
            if not connected:
                pending_listener = await _cancel_registration(agent, listener, ready)
        # A Git hook can finish before the hub retires its socket. Retry only
        # that precise admission refusal; credentials and ownership rules
        # remain unchanged, and no release has crossed the send boundary.
        if agent.last_close_code != 4009 or agent.last_close_reason != "name conflict":
            if pending_listener and last_refusal and agent.last_close_code is None:
                agent.last_close_code, agent.last_close_reason = last_refusal
            return None
        last_refusal = (4009, "name conflict")
        await asyncio.sleep(min(0.05, max(0.0, deadline - loop.time())))
    return None


async def close_owner(agent: SynapseAgent, listener: asyncio.Task[None] | None) -> None:
    """Stop the caller's profile and join its listener when registration succeeded."""
    agent.running = False
    if listener is not None:
        listener.cancel()
        await asyncio.gather(listener, return_exceptions=True)
