# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded durable exact-recipient activity pages
"""Read metadata-only notification pointers under an independent scanned cursor."""

from __future__ import annotations

import json
from dataclasses import dataclass

from synapse_channel.core.activity_streams import stream_activity
from synapse_channel.core.event_row_recovery import CORRUPT_EVENT_KIND
from synapse_channel.core.journal import EventKind
from synapse_channel.core.notification_events import event_activity
from synapse_channel.core.persistence import EventStore

ACTIVITY_SCAN_LIMIT = 1000
"""Maximum relevant-family rows inspected in one native request."""

ACTIVITY_SCAN_BYTES = 1024 * 1024
"""Stop scanning after one MiB of encoded payloads, permitting one oversized row."""


@dataclass(frozen=True)
class ActivityQuery:
    """Validate an exact-identity cursor with explicit stream metadata opt-in."""

    identity: str
    hub_id: str
    since_seq: int
    limit: int
    version: int = 1

    def __post_init__(self) -> None:
        """Refuse coercion, unknown identities and noninitial unbound cursors."""
        if (
            type(self.identity) is not str
            or not self.identity
            or self.identity.strip() != self.identity
            or len(self.identity) > 256
            or type(self.hub_id) is not str
            or len(self.hub_id) > 256
            or type(self.since_seq) is not int
            or not 0 <= self.since_seq <= 9223372036854775807
            or (self.since_seq != 0 and not self.hub_id)
            or type(self.limit) is not int
            or not 1 <= self.limit <= 100
            or type(self.version) is not int
            or self.version not in (1, 2)
        ):
            raise ValueError("invalid activity query")

    @classmethod
    def from_value(cls, value: object) -> ActivityQuery:
        """Parse an exact query shape without silently accepting extra selectors."""
        if (
            not isinstance(value, dict)
            or set(value) != {"version", "identity", "hub_id", "since_seq", "limit"}
            or type(value.get("version")) is not int
            or value["version"] not in (1, 2)
        ):
            raise ValueError("invalid activity query")
        return cls(
            value["identity"], value["hub_id"], value["since_seq"], value["limit"], value["version"]
        )


def _event_item(
    kind: str, payload: dict[str, object], query: ActivityQuery, *, seq: int, ts: float
) -> dict[str, object] | None:
    """Project one scoped event according to the explicitly negotiated version."""
    family = event_activity(kind, payload, query.identity)
    stream = (
        stream_activity(payload, query.identity, seq=seq, ts=ts)
        if query.version == 2 and kind == EventKind.CHAT
        else None
    )
    if family is None and stream is None:
        return None
    item: dict[str, object] = {"seq": seq, "kind": family or "streams"}
    if stream is not None:
        item["stream"] = stream.encode()
    return item


def read_activity_page(
    store: EventStore, query: ActivityQuery, *, hub_id: str
) -> dict[str, object]:
    """Scan a bounded metadata-only page after transport has authorised identity.

    Parameters
    ----------
    store : EventStore
        Authoritative durable journal; no separate mutable notification store.
    query : ActivityQuery
        Exact requesting identity, validated page limit and scanned cursor.
    hub_id : str
        Current admitted hub identity, refusing cursor reuse across hubs.

    Returns
    -------
    dict[str, object]
        Versioned available page with ordered sequence/kind items. Cursor
        advancement means scanned rows, never client processing or task effect.

    Raises
    ------
    ValueError
        For a foreign hub or a cursor beyond the retained journal.
    """
    if (query.hub_id and query.hub_id != hub_id) or query.since_seq > store.max_seq():
        raise ValueError("activity cursor is not available")
    cursor, scanned, encoded_bytes = query.since_seq, 0, 0
    has_more = False
    items: list[dict[str, object]] = []
    with store.stream_events(
        after_seq=query.since_seq,
        kinds=(EventKind.CHAT, EventKind.LEDGER_TASK, EventKind.HANDOFF),
    ) as events:
        for event in events:
            if event.kind == CORRUPT_EVENT_KIND:
                raise ValueError("activity journal row is not available")
            item = _event_item(event.kind, event.payload, query, seq=event.seq, ts=event.ts)
            cursor, scanned = event.seq, scanned + 1
            encoded_bytes += len(json.dumps(event.payload).encode())
            if item is not None:
                items.append(item)
            if (
                len(items) == query.limit
                or scanned == ACTIVITY_SCAN_LIMIT
                or encoded_bytes >= ACTIVITY_SCAN_BYTES
            ):
                has_more = next(events, None) is not None
                break
    return {
        "version": query.version,
        "available": True,
        "identity": query.identity,
        "hub_id": hub_id,
        "cursor": cursor,
        "items": items,
        "has_more": has_more,
    }
