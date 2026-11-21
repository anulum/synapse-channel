# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — client side of the forward event feed
"""Read a feed page and acknowledge it over one authenticated hub connection.

A memory consumer calls :func:`request_feed_page` with its cursor and keeps the
returned ``next_after``, ``hub_id`` and ``journal_instance`` as its next cursor.
After it has durably ingested a page it calls :func:`send_feed_ack`. The hub
decides; this module only carries the request and returns the hub's answer.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.client.owner_registration import register_owner
from synapse_channel.core.protocol import MessageType
from synapse_channel.machine_identity import machine_identity_agent_kwargs


class FeedUnavailable(RuntimeError):
    """The hub could not be reached, or it gave no answer in time."""


async def _exchange(
    *,
    uri: str,
    identity: str,
    token: str | None,
    timeout: float,
    reply_type: str,
    reply_key: str,
    send: Callable[[SynapseAgent, str], Awaitable[None]],
) -> dict[str, Any]:
    """Connect as ``identity``, send one request and return the reply's ``reply_key`` body.

    The connection carries this one request, so an ``error`` frame that arrives
    after it was sent is the hub's refusal of it: an access-control denial names
    no request id. It is returned as a body holding only ``error`` and, when the
    hub gave it, ``acl_reason``.
    """
    request_id = uuid.uuid4().hex
    reply: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
    sent = False

    async def receive(data: dict[str, Any]) -> None:
        if reply.done():
            return
        if data.get("type") == reply_type and data.get("request_id") == request_id:
            reply.set_result(data)
        elif sent and data.get("type") == MessageType.ERROR:
            refusal = {"error": str(data.get("payload") or "the hub refused the request")}
            if isinstance(data.get("acl_reason"), str):
                refusal["acl_reason"] = data["acl_reason"]
            reply.set_result({reply_key: refusal})

    agent = SynapseAgent(
        identity, receive, uri=uri, token=token, verbose=False, **machine_identity_agent_kwargs()
    )
    task = await register_owner(agent, timeout)
    try:
        if task is None:
            raise FeedUnavailable(f"the hub at {uri} did not accept {identity}")
        sent = True
        await send(agent, request_id)
        try:
            response = await asyncio.wait_for(reply, timeout=timeout)
        except TimeoutError as exc:
            raise FeedUnavailable(f"the hub at {uri} gave no answer in {timeout} s") from exc
        body = response.get(reply_key)
        if not isinstance(body, dict):
            raise FeedUnavailable(f"the hub's answer carries no {reply_key}")
        return body
    finally:
        agent.running = False
        if agent.connection is not None:
            with contextlib.suppress(Exception):
                await agent.connection.close()
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


async def request_feed_page(
    *,
    uri: str,
    identity: str,
    token: str | None,
    consumer: str,
    kinds: Sequence[str],
    after_seq: int = 0,
    hub_id: str = "",
    journal_instance: str = "",
    limit: int = 100,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Return one feed page, or the hub's refusal in it under ``error``.

    Parameters
    ----------
    uri, identity, token : str, str, str or None
        The hub and the connection's identity and shared secret.
    consumer : str
        The consumer, the identity itself or one of its roles.
    kinds : sequence of str
        Feed kinds, ``finding`` and or ``chat_marker``.
    after_seq, hub_id, journal_instance : int, str, str
        The cursor from the previous page; all empty or zero on a first read.
    limit : int
        Page size, 1 to 500.
    timeout : float
        Seconds to wait for the connection and for the answer.

    Raises
    ------
    FeedUnavailable
        When the hub cannot be reached or gives no answer.
    """
    query = {
        "version": 1,
        "consumer": consumer,
        "hub_id": hub_id,
        "journal_instance": journal_instance,
        "after_seq": after_seq,
        "kinds": list(kinds),
        "limit": limit,
    }

    async def send(agent: SynapseAgent, request_id: str) -> None:
        await agent.send_message(
            MessageType.HISTORY_REQUEST,
            target="System",
            payload="feed page",
            request_id=request_id,
            feed_query=query,
        )

    return await _exchange(
        uri=uri,
        identity=identity,
        token=token,
        timeout=timeout,
        reply_type=MessageType.HISTORY_SNAPSHOT,
        reply_key="feed_page",
        send=send,
    )


async def send_feed_ack(
    *,
    uri: str,
    identity: str,
    token: str | None,
    consumer: str,
    hub_id: str,
    journal_instance: str,
    acked_seq: int,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Acknowledge every row through ``acked_seq``; return the hub's record of it.

    Raises
    ------
    FeedUnavailable
        When the hub cannot be reached or gives no answer.
    """

    async def send(agent: SynapseAgent, request_id: str) -> None:
        await agent.send_message(
            MessageType.FEED_ACK,
            target="System",
            payload="feed acknowledgement",
            request_id=request_id,
            consumer=consumer,
            hub_id=hub_id,
            journal_instance=journal_instance,
            acked_seq=acked_seq,
        )

    return await _exchange(
        uri=uri,
        identity=identity,
        token=token,
        timeout=timeout,
        reply_type=MessageType.FEED_ACK_RESULT,
        reply_key="feed_ack",
        send=send,
    )
