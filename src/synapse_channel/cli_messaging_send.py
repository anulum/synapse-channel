# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — messaging CLI send command
"""One-shot chat send command for the ``synapse`` CLI."""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

from synapse_channel.cli_messaging_types import AgentFactory
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.protocol import MessageType


def _one_shot_sender_name(name: str) -> str:
    """Return the sender identity used by one-shot ``send`` connections.

    Parameters
    ----------
    name : str
        Identity supplied through ``synapse send --name``.

    Returns
    -------
    str
        ``name`` unchanged unless it looks like the common waiter identity
        ``<agent>-rx``. In that case the one-shot command sends as ``<agent>`` so
        it does not collide with the persistent wake socket.
    """
    if name.endswith("-rx") and len(name) > len("-rx"):
        return name[: -len("-rx")]
    return name


async def _send(
    *,
    uri: str,
    name: str,
    target: str,
    message: str,
    wait_seconds: float,
    priority: bool = False,
    agent_factory: AgentFactory = SynapseAgent,
    token: str | None = None,
    ready_timeout: float = 5.0,
) -> int:
    """Send one chat message and optionally print replies for a window.

    Parameters
    ----------
    uri, name, target, message : str
        Hub URI, sender name, recipient, and message body.
    wait_seconds : float
        Seconds to keep listening for replies after sending (``0`` to skip).
    priority : bool, optional
        Mark the message as priority so it wakes even directed-only waiters.
    agent_factory : AgentFactory, optional
        Factory for the client agent; injectable for testing.
    token : str or None, optional
        Shared-secret token for a secured hub.
    ready_timeout : float, optional
        Seconds to wait for the hub connection readiness event.

    Returns
    -------
    int
        ``0`` on success, ``1`` when the hub could not be reached.
    """
    sender_name = _one_shot_sender_name(name)
    replies: list[dict[str, Any]] = []

    async def collect(data: dict[str, Any]) -> None:
        if data.get("type") == MessageType.CHAT and data.get("sender") != sender_name:
            replies.append(data)

    agent = agent_factory(sender_name, collect, uri=uri, verbose=False, token=token)
    conn_task = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=ready_timeout):
            print(f"[{sender_name}] Could not reach hub at {uri}.")
            return 1
        await agent.chat(message, target=target, priority=priority)
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)
            for reply in replies:
                print(f"{reply.get('sender')}: {reply.get('payload')}")
        return 0
    finally:
        agent.running = False
        conn_task.cancel()


def _cmd_send(args: argparse.Namespace) -> int:
    """Dispatch the ``send`` subcommand."""
    return asyncio.run(
        _send(
            uri=args.uri,
            name=args.name,
            target=args.target,
            message=args.message,
            wait_seconds=args.wait_seconds,
            priority=args.priority,
            token=args.token,
            ready_timeout=args.ready_timeout,
        )
    )
