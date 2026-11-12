# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — correlated historical pending work reads
"""Validate complete recipient-bound recovery metadata on the existing bridge."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from websockets.exceptions import WebSocketException

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.pending_work import MAX_PENDING_WORK, PendingItem, PendingQuery
from synapse_channel.core.protocol import MessageType
from synapse_channel.mcp.hub_inbox import AwaitReply


def decode_pending_items(
    value: object, *, limit: int = MAX_PENDING_WORK
) -> tuple[PendingItem, ...]:
    """Require a bounded, unique, canonically ordered complete item population."""
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError("invalid pending work population")
    items: list[PendingItem] = []
    keys: set[str] = set()
    previous = ("", "")
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != {"kind", "key"}:
            raise ValueError("invalid pending work entry")
        item = PendingItem(entry["kind"], entry["key"])
        ordering = item.kind, item.key
        if item.key in keys or ordering <= previous:
            raise ValueError("invalid pending work order")
        keys.add(item.key)
        previous = ordering
        items.append(item)
    return tuple(items)


@dataclass(frozen=True)
class PendingSnapshot:
    """Bind a complete metadata-only snapshot to one admitted native identity."""

    identity: str
    hub_id: str
    items: tuple[PendingItem, ...]

    @classmethod
    def from_value(cls, value: object, query: PendingQuery, connected_hub: str) -> PendingSnapshot:
        """Refuse unavailable, foreign, legacy or oversized native responses."""
        if (
            not isinstance(value, dict)
            or set(value) != {"version", "available", "identity", "hub_id", "items"}
            or type(value["version"]) is not int
            or value["version"] != 1
            or value["available"] is not True
            or value["identity"] != query.identity
            or value["hub_id"] != connected_hub
            or not connected_hub
            or len(connected_hub) > 256
            or (query.hub_id and query.hub_id != connected_hub)
        ):
            raise ValueError("invalid pending work binding")
        return cls(
            query.identity, connected_hub, decode_pending_items(value["items"], limit=query.limit)
        )


async def read_pending_work(
    agent: SynapseAgent, await_reply: AwaitReply, *, hub_id: str = ""
) -> PendingSnapshot:
    """Read bounded historical recovery metadata without opening another socket.

    Parameters
    ----------
    agent : SynapseAgent
        Existing fixed-identity native connection.
    await_reply : Callable
        Existing finite correlator, serialized by its owning HTTP runtime.
    hub_id : str
        Accepted hub binding; empty is allowed only for an initial read.

    Returns
    -------
    PendingSnapshot
        Complete content-free historical pending assignment population. An old
        hub's ordinary history response never becomes a successful empty result.
    """
    query = PendingQuery(agent.name, hub_id)
    expected_hub = agent.hub_id
    if not agent.ready_event.is_set():
        raise RuntimeError("historical pending work unavailable; observer unchanged")
    request_id = uuid.uuid4().hex

    async def send() -> None:
        await agent.send_message(
            MessageType.HISTORY_REQUEST,
            target="System",
            request_id=request_id,
            pending_query={
                "version": 1,
                "identity": query.identity,
                "hub_id": query.hub_id,
                "limit": query.limit,
            },
        )

    try:
        response = await await_reply(
            lambda frame: (
                frame.get("type") == MessageType.HISTORY_SNAPSHOT
                and frame.get("request_id") == request_id
            ),
            send,
        )
        if (
            response is None
            or response.get("target") != query.identity
            or agent.hub_id != expected_hub
        ):
            raise ValueError("pending response binding changed")
        return PendingSnapshot.from_value(response.get("pending_work"), query, expected_hub)
    except (ValueError, OSError, WebSocketException):
        raise RuntimeError("historical pending work unavailable; observer unchanged") from None
