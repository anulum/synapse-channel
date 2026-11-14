# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — one request to the hub, one reply, nothing printed
"""Send one request to the hub as one identity and return its first answer.

``synapse operation declare``, ``synapse operation attempt`` and the hub path of
``synapse ops run`` each connect, send one frame and wait for the hub's answer
or error. This module does that once, and prints nothing: each command renders
the reply in its own way.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from synapse_channel.cli_messaging_types import AgentFactory
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.connect_failures import closed_after_ready, describe_connect_failure
from synapse_channel.core.protocol import MessageType


@dataclass(frozen=True, slots=True)
class HubReply:
    """The outcome of one request.

    Attributes
    ----------
    reply : dict[str, Any] or None
        The hub's answer frame or error frame; ``None`` when none came in time or
        the connection failed.
    failure : str
        Why the connection failed; empty when it was made.
    """

    reply: dict[str, Any] | None
    failure: str = ""


async def _first(replies: list[dict[str, Any]], *, timeout: float) -> dict[str, Any] | None:
    """Return the first collected frame before ``timeout`` seconds, or ``None``."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(float(timeout), 0.0)
    while loop.time() <= deadline:
        if replies:
            return replies[0]
        await asyncio.sleep(0.01)
    return None


def _failure(agent: Any, actor: str, uri: str) -> str:
    """Return why the connection as ``actor`` to ``uri`` failed."""
    return describe_connect_failure(
        actor, uri, close_code=agent.last_close_code, close_reason=agent.last_close_reason
    )


async def ask_hub(
    *,
    uri: str,
    actor: str,
    message_type: str,
    fields: dict[str, Any],
    answers: Callable[[dict[str, Any]], bool],
    token: str | None,
    ready_timeout: float,
    result_timeout: float,
    agent_factory: AgentFactory = SynapseAgent,
) -> HubReply:
    """Connect as ``actor``, send one ``message_type`` frame and return the first answer.

    Parameters
    ----------
    uri, actor, message_type : str
        Hub URI, the identity to connect as, and the request's message type.
    fields : dict[str, Any]
        The request's fields.
    answers : Callable[[dict[str, Any]], bool]
        Whether an incoming frame is this request's answer; a hub error always is.
    token : str or None
        Shared-secret token for a secured hub.
    ready_timeout, result_timeout : float
        Seconds to await readiness and the answer.
    agent_factory : AgentFactory, optional
        Client factory; injectable for testing.
    """
    replies: list[dict[str, Any]] = []

    async def collect(data: dict[str, Any]) -> None:
        if data.get("type") == MessageType.ERROR or answers(data):
            replies.append(data)

    agent = agent_factory(actor, collect, uri=uri, verbose=False, token=token)
    connection = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=ready_timeout) or await closed_after_ready(
            agent, timeout=ready_timeout
        ):
            return HubReply(None, _failure(agent, actor, uri))
        await agent.send_message(message_type, target="System", **fields)
        return HubReply(await _first(replies, timeout=result_timeout))
    finally:
        agent.running = False
        connection.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await connection
