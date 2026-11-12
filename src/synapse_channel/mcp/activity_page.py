# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — correlated native activity reads for an existing MCP bridge
"""Validate metadata-only native activity pages without committing observer progress."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Literal, cast

from websockets.exceptions import WebSocketException

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.activity_pages import ActivityQuery
from synapse_channel.core.activity_streams import StreamActivity
from synapse_channel.core.protocol import MessageType
from synapse_channel.mcp.hub_inbox import AwaitReply
from synapse_channel.mcp.wake_pointer import WAKE_SOURCES, WakeSource


@dataclass(frozen=True)
class ActivityItem:
    """Retain one validated native sequence and its notification family."""

    seq: int
    kind: Literal["messages", "tasks", "handoffs", "streams"]
    stream: StreamActivity | None = None


@dataclass(frozen=True)
class ActivityPage:
    """Hold one validated page independently from business processing receipts."""

    identity: str
    hub_id: str
    cursor: int
    items: tuple[ActivityItem, ...]
    has_more: bool

    @property
    def sources(self) -> tuple[WakeSource, ...]:
        """Return each present family once, in canonical pointer order."""
        kinds = {item.kind for item in self.items}
        return tuple(cast(WakeSource, kind) for kind in WAKE_SOURCES if kind in kinds)


def _page_binding(value: object, query: ActivityQuery, connected_hub: str) -> dict[str, Any]:
    """Validate the schema and exact identity before interpreting cursor fields."""
    if (
        not isinstance(value, dict)
        or set(value)
        != {"version", "available", "identity", "hub_id", "cursor", "items", "has_more"}
        or type(value.get("version")) is not int
        or value["version"] != query.version
        or value["available"] is not True
        or value["identity"] != query.identity
        or value["hub_id"] != connected_hub
        or not connected_hub
        or len(connected_hub) > 256
        or (query.hub_id and value["hub_id"] != query.hub_id)
    ):
        raise ValueError("invalid native activity binding")
    return value


def _stream_entry(entry: dict[str, Any], version: int) -> StreamActivity | None:
    """Require explicit version opt-in and the correct terminal/progress family."""
    stream = StreamActivity.from_value(entry["seq"], entry["stream"]) if "stream" in entry else None
    if (
        (version == 1 and (stream is not None or entry["kind"] == "streams"))
        or (stream is None and entry["kind"] == "streams")
        or (stream is not None and entry["kind"] != ("messages" if stream.terminal else "streams"))
    ):
        raise ValueError("invalid native stream activity family")
    return stream


def _activity_items(
    entries: list[object], previous: int, cursor: int, version: int
) -> tuple[ActivityItem, ...]:
    """Validate each strictly ascending sequence and known metadata family."""
    items: list[ActivityItem] = []
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or set(entry) not in ({"seq", "kind"}, {"seq", "kind", "stream"})
            or type(entry.get("seq")) is not int
            or not previous < entry["seq"] <= cursor
            or type(entry.get("kind")) is not str
            or entry["kind"] not in (*WAKE_SOURCES, "streams")
        ):
            raise ValueError("invalid native activity item")
        previous = entry["seq"]
        stream = _stream_entry(entry, version)
        items.append(
            ActivityItem(
                previous,
                cast(Literal["messages", "tasks", "handoffs", "streams"], entry["kind"]),
                stream,
            )
        )
    return tuple(items)


def _validate_page(value: object, query: ActivityQuery, connected_hub: str) -> ActivityPage:
    """Require bounded progress before creating the immutable observation page."""
    page = _page_binding(value, query, connected_hub)
    if (
        type(page["cursor"]) is not int
        or not query.since_seq <= page["cursor"] <= 9223372036854775807
        or type(page["has_more"]) is not bool
        or (page["has_more"] and page["cursor"] == query.since_seq)
        or not isinstance(page["items"], list)
        or len(page["items"]) > query.limit
    ):
        raise ValueError("invalid native activity progress")
    items = _activity_items(page["items"], query.since_seq, page["cursor"], query.version)
    return ActivityPage(query.identity, connected_hub, page["cursor"], items, page["has_more"])


async def read_activity_page(
    agent: SynapseAgent,
    await_reply: AwaitReply,
    *,
    hub_id: str = "",
    after_seq: int = 0,
    limit: int = 50,
    version: int = 1,
) -> ActivityPage:
    """Read a correlated page over the bridge's single admitted connection.

    Parameters
    ----------
    agent : SynapseAgent
        Existing fixed-identity connection. No additional native socket is opened.
    await_reply : Callable
        Existing finite request/reply correlator, serialized by its caller.
    hub_id : str
        Previously accepted hub binding; empty only for an initial scan.
    after_seq : int
        Last committed scanned position. Reading does not write this cursor.
    limit : int
        Maximum metadata items, from one through one hundred.
    version : int
        Version two includes bounded stream progress for restart-safe recovery.

    Returns
    -------
    ActivityPage
        Explicitly available, exact-recipient metadata with canonical source kinds.

    Raises
    ------
    ValueError
        For malformed cursor or page bounds before any request is sent.
    RuntimeError
        When admission, correlation or response validation cannot prove a page.
        Legacy ordinary history responses never become an empty success.
    """
    query = ActivityQuery(agent.name, hub_id, after_seq, limit, version)
    expected_hub = agent.hub_id
    if not agent.ready_event.is_set():
        raise RuntimeError("durable activity page unavailable; observer cursor unchanged")
    request_id = uuid.uuid4().hex

    async def send() -> None:
        await agent.send_message(
            MessageType.HISTORY_REQUEST,
            target="System",
            payload="durable activity",
            request_id=request_id,
            activity_query={
                "version": query.version,
                "identity": query.identity,
                "hub_id": query.hub_id,
                "since_seq": query.since_seq,
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
            raise ValueError("native activity response binding changed")
        return _validate_page(response.get("activity_page"), query, expected_hub)
    except (OSError, ValueError, WebSocketException):
        raise RuntimeError("durable activity page unavailable; observer cursor unchanged") from None
