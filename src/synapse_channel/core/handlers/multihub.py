# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — serving half of the cross-host multi-hub event-log pull
"""Serving half of the cross-host multi-hub event-log pull.

A peer hub following this one asks for the events past a cursor with a
:data:`~synapse_channel.core.protocol.MessageType.MULTIHUB_LOG_REQUEST` frame, and this
handler answers with a single private
:data:`~synapse_channel.core.protocol.MessageType.MULTIHUB_LOG_SNAPSHOT` back to the asking
socket — the network counterpart of :func:`synapse_channel.core.multihub_follower.store_fetcher`,
which today only reads a peer's :class:`~synapse_channel.core.persistence.EventStore` off a shared
filesystem. The request body and the snapshot reply are framed by the shared codec
(:mod:`synapse_channel.core.multihub_wire`), so the serving half and the fetching half agree on
the format without importing each other.

The handler is read-only: it reads through the hub's durable
:attr:`~synapse_channel.core.persistence.EventStore.read_since` cursor and mutates nothing, so
the ACL layer leaves it ungated like the other read snapshots. It is also deliberately
forgiving of the *request* — a malformed cursor yields an empty snapshot rather than an error —
while the strictness lives on the fetching side, where a malformed *snapshot* fails the poll and
leaves the peer's cursor unadvanced. A hub running without persistence (no journal) has no log to
serve and answers with an empty snapshot anchored at the requested cursor.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from synapse_channel.core.multihub_wire import (
    LogRequest,
    LogSnapshot,
    MultiHubWireError,
    decode_log_request,
    encode_log_snapshot,
)
from synapse_channel.core.protocol import MessageType

if TYPE_CHECKING:
    from synapse_channel.core.hub import SynapseHub


async def handle_multihub_log_request(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Answer a peer hub's request for events past a cursor with one log snapshot.

    Parameters
    ----------
    hub : SynapseHub
        The hub whose durable event log is served; ``hub.journal`` is the
        :class:`~synapse_channel.core.persistence.EventStore`, or ``None`` when the hub runs
        without persistence.
    sender : str
        The requesting peer; the snapshot is addressed privately to it.
    data : dict[str, Any]
        The request frame; its ``after_seq`` cursor and optional ``limit`` are read by the
        shared codec. A body the codec rejects is answered with an empty snapshot.
    websocket : Any
        The requesting socket the snapshot is sent back on.
    """
    try:
        request = decode_log_request(data)
    except MultiHubWireError:
        snapshot = LogSnapshot(events=(), next_cursor=0)
    else:
        snapshot = _read_snapshot(hub, request)
    await hub._send_json(
        websocket,
        hub._system(
            "Multi-hub log snapshot",
            msg_type=MessageType.MULTIHUB_LOG_SNAPSHOT,
            target=sender,
            **encode_log_snapshot(snapshot),
        ),
    )


def _read_snapshot(hub: SynapseHub, request: LogRequest) -> LogSnapshot:
    """Read the events past the request cursor and pair them with a resume high-water.

    Parameters
    ----------
    hub : SynapseHub
        The hub whose ``journal`` is read.
    request : LogRequest
        The validated cursor and optional batch cap.

    Returns
    -------
    LogSnapshot
        The events with ``seq`` above the cursor (capped by ``limit``) and the ``seq`` the
        caller resumes from: the last event's ``seq`` when the batch is non-empty, otherwise
        the request cursor itself, so an empty batch never moves the cursor. A hub without a
        journal returns an empty snapshot anchored at the request cursor.
    """
    if hub.journal is None:
        return LogSnapshot(events=(), next_cursor=request.after_seq)
    events = tuple(hub.journal.read_since(request.after_seq, limit=request.limit))
    next_cursor = events[-1].seq if events else request.after_seq
    return LogSnapshot(events=events, next_cursor=next_cursor)
