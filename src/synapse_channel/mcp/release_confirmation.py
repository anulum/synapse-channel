# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — MCP retained release inquiry before replay
"""Read an exact durable release receipt without disturbing a later live fence."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.protocol import SENDER_HUB, MessageType
from synapse_channel.core.release_confirmation import ReleaseIntent
from synapse_channel.mcp.reply_exchange import Matcher, Sender

TimedReply = Callable[[Matcher, Sender, float], Awaitable[dict[str, Any] | None]]


class PriorReleaseUnconfirmed(RuntimeError):
    """An earlier keyed release has no confirmed receipt while a newer lease is held."""


async def confirm_prepared_release(
    agent: SynapseAgent, request: dict[str, Any], await_reply: TimedReply
) -> str | None:
    """Confirm an original keyed release before replay could discard a newer fence.

    Parameters
    ----------
    agent : SynapseAgent
        Already admitted owner whose connection and current fence are retained.
    request : dict[str, Any]
        Exact prepared native request, including the original key and epoch.
    await_reply : TimedReply
        Existing correlated exchange; the inquiry is bounded to one second and
        remains inside the HTTP operation's overall dispatch deadline.

    Returns
    -------
    str or None
        Original confirmed receipt text, or ``None`` to continue the same pinned
        mutation when no newer live fence is endangered. No lease is released here.

    Raises
    ------
    PriorReleaseUnconfirmed
        If the old outcome cannot be confirmed while this client owns a newer fence.
    """
    if not request.get("idem_key") or agent.lease_epochs.get(request["task_id"]) == request.get(
        "epoch"
    ):
        return None
    intent = ReleaseIntent.from_request(request)
    request_id = uuid.uuid4().hex
    response = await await_reply(
        lambda frame: (
            frame.get("sender") == SENDER_HUB
            and frame.get("hub_id") == agent.hub_id
            and frame.get("type") == MessageType.STATE_SNAPSHOT
            and frame.get("target") == agent.name
            and frame.get("request_id") == request_id
        ),
        lambda: agent.request_release_confirmation(
            intent.task_id, intent.operation_id, intent.request_digest, request_id
        ),
        1.0,
    )
    projection = response.get("release_confirmation") if response is not None else None
    if (
        isinstance(projection, dict)
        and projection.get("status") == "confirmed"
        and intent.matching_receipt(projection) is not None
    ):
        return f"released '{intent.task_id}' with receipt owner '{agent.name}'"
    current = agent.lease_epochs.get(intent.task_id)
    if current is not None and current != request.get("epoch"):
        raise PriorReleaseUnconfirmed("prior release is unconfirmed; current lease fence retained")
    return None
