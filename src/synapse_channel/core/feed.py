# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — the authenticated forward event feed for memory consumers
"""Serve bounded, ascending feed pages bound to the hub and its journal instance.

A memory consumer (REMANENTIA first) reads the hub's findings and its marked chat
through ``history_request.feed_query`` on its authenticated connection, never by
opening the hub's journal. A page holds the rows after the consumer's cursor, in
sequence order, of the kinds it was granted. Its cursor names the hub and the
journal instance (:class:`~synapse_channel.core.persistence_store_format.JournalInstance`),
so a consumer can tell a different journal under the same hub id and never
continues across one by itself. The page is a pure read: the same query returns
the same rows, apart from rows deleted since.

``feed_ack`` records, durably, the highest sequence a consumer has ingested. An
ack never moves backwards and never runs ahead of the pages this hub served the
consumer. It is the consumer's own statement, not its cursor.

Feed kinds are a closed list. ``finding`` serves the hub's findings. ``chat_marker``
serves undirected, untagged chat whose text begins with one of the hub's
configured markers; directed and channel chat are never served, and plain
``chat`` is never a feed kind.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from synapse_channel.core.event_row_recovery import CORRUPT_EVENT_KIND
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import StoredEvent
from synapse_channel.core.persistence_store_format import JournalInstance

FEED_FINDING = "finding"
"""The feed kind of the hub's durable findings."""

FEED_CHAT_MARKER = "chat_marker"
"""The feed kind of undirected, untagged chat that opens with a configured marker."""

FEED_KINDS = frozenset({FEED_FINDING, FEED_CHAT_MARKER})
"""Every kind a feed may serve; chat and directed messages are never among them."""

DEFAULT_FEED_CHAT_MARKERS = ("Finding:", "Decision:")
"""The chat markers a hub serves as ``chat_marker`` when its operator names none."""

FEED_SCAN_LIMIT = 1000
"""Maximum journal rows one feed request inspects."""

FEED_PAGE_BYTES = 7 * 1024 * 1024
"""Maximum encoded row bytes of one page, below the client frame ceiling."""

FEED_MAX_LIMIT = 500
"""Largest page a consumer may ask for."""

_JOURNAL_KINDS = {FEED_FINDING: EventKind.FINDING, FEED_CHAT_MARKER: EventKind.CHAT}


class FeedJournal(Protocol):
    """The journal reads a feed page makes."""

    def read_since(
        self, after_seq: int, *, kinds: Iterable[str] | None = None, limit: int | None = None
    ) -> list[StoredEvent]:
        """Return events whose sequence is greater than a cursor, in order."""

    def max_seq(self) -> int:
        """Return the highest sequence number stored, or ``0`` when the log is empty."""

    def journal_instance(self) -> JournalInstance | None:
        """Return the journal instance, or ``None`` when the store has none."""


class FeedRefused(ValueError):
    """A feed request the hub answers with a refusal and no rows."""


@dataclass(frozen=True)
class FeedQuery:
    """One validated version 1 feed query.

    Attributes
    ----------
    consumer : str
        The consumer whose progress this is: the connection or one of its roles.
    hub_id : str
        The hub the cursor belongs to; empty only on a consumer's first query.
    journal_instance : str
        The journal instance the cursor belongs to; empty only on a first query.
    after_seq : int
        Exclusive sequence cursor.
    kinds : tuple of str
        Requested feed kinds, a subset of :data:`FEED_KINDS`.
    limit : int
        Page size, 1 to :data:`FEED_MAX_LIMIT`.
    """

    consumer: str
    hub_id: str
    journal_instance: str
    after_seq: int
    kinds: tuple[str, ...]
    limit: int


def _plain_int(value: object, low: int, high: int) -> int | None:
    """Return ``value`` when it is a plain integer inside ``[low, high]``."""
    return value if type(value) is int and low <= value <= high else None


def parse_feed_query(query: object) -> FeedQuery:
    """Validate a ``feed_query`` mapping.

    Raises
    ------
    FeedRefused
        When a field is missing, of the wrong type or out of range, or a kind is
        not a feed kind.
    """
    if not isinstance(query, Mapping) or query.get("version") != 1:
        raise FeedRefused("invalid feed query")
    consumer, hub_id, instance = _query_names(query)
    after_seq = _plain_int(query.get("after_seq"), 0, 9223372036854775807)
    limit = _plain_int(query.get("limit"), 1, FEED_MAX_LIMIT)
    if after_seq is None or limit is None:
        raise FeedRefused("invalid feed query")
    kinds = _query_kinds(query.get("kinds"))
    if not hub_id and not instance and after_seq != 0:
        raise FeedRefused("an unbound feed query starts at sequence 0")
    return FeedQuery(consumer, hub_id, instance, after_seq, kinds, limit)


def _query_names(query: Mapping[str, Any]) -> tuple[str, str, str]:
    """Return a query's consumer, hub id and journal instance, or refuse."""
    consumer, hub_id, instance = (
        query.get("consumer"),
        query.get("hub_id"),
        query.get("journal_instance"),
    )
    if (
        not isinstance(consumer, str)
        or not consumer.strip()
        or not isinstance(hub_id, str)
        or not isinstance(instance, str)
    ):
        raise FeedRefused("invalid feed query")
    return consumer, hub_id, instance


def _query_kinds(kinds: object) -> tuple[str, ...]:
    """Return a query's distinct feed kinds, sorted, or refuse."""
    if (
        not isinstance(kinds, list)
        or not kinds
        or not all(isinstance(kind, str) for kind in kinds)
        or len(set(kinds)) != len(kinds)
    ):
        raise FeedRefused("invalid feed query")
    if not set(kinds) <= FEED_KINDS:
        raise FeedRefused(f"unsupported feed kind: {sorted(set(kinds) - FEED_KINDS)}")
    return tuple(sorted(kinds))


def payload_sha256(payload: Mapping[str, Any]) -> str:
    """Return the canonical digest of a decoded payload, as a legacy binding checks it.

    The payload is encoded as JSON with sorted keys, ``,`` and ``:`` separators,
    ``ensure_ascii`` false and ``allow_nan`` false, then as UTF-8.

    Raises
    ------
    ValueError
        When the payload holds a non-finite number.
    """
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def is_chat_marker(payload: Mapping[str, Any], markers: Sequence[str]) -> bool:
    """Return whether a chat frame is undirected, untagged and opens with a marker.

    The journal keeps the chat frame as sent, so its text is the frame's
    ``payload`` field.
    """
    target = payload.get("target")
    text = payload.get("payload")
    return (
        target in (None, "", "all")
        and not payload.get("channel")
        and isinstance(text, str)
        and any(text.startswith(marker) for marker in markers)
    )


def _row(
    event: StoredEvent, kinds: tuple[str, ...], markers: Sequence[str]
) -> dict[str, Any] | None:
    """Return the feed row for ``event``, or ``None`` when the feed does not serve it."""
    if event.kind == CORRUPT_EVENT_KIND:
        original = event.payload.get("original_kind")
        if original == EventKind.FINDING and FEED_FINDING in kinds:
            return {"seq": event.seq, "kind": FEED_FINDING, "corrupt": True}
        return None  # a corrupt chat row cannot show whether it was marked
    if event.kind == EventKind.FINDING and FEED_FINDING in kinds:
        kind = FEED_FINDING
    elif (
        event.kind == EventKind.CHAT
        and FEED_CHAT_MARKER in kinds
        and is_chat_marker(event.payload, markers)
    ):
        kind = FEED_CHAT_MARKER
    else:
        return None
    try:
        digest = payload_sha256(event.payload)
    except ValueError:
        return {"seq": event.seq, "kind": kind, "corrupt": True}
    return {
        "seq": event.seq,
        "kind": kind,
        "ts": event.ts,
        "payload": event.payload,
        "payload_sha256": digest,
    }


@dataclass(frozen=True)
class FeedPage:
    """One served page: rows, the next cursor and whether more rows may follow."""

    rows: tuple[dict[str, Any], ...]
    next_after: int
    has_more: bool


def read_page(journal: FeedJournal, query: FeedQuery, markers: Sequence[str]) -> FeedPage:
    """Read one bounded, ascending page after ``query.after_seq``.

    The cursor advances over every scanned row, served or not, so a page of
    unserved rows still moves the consumer forward. A row that alone exceeds the
    byte cap is refused rather than split.

    Raises
    ------
    FeedRefused
        When the first row to serve exceeds :data:`FEED_PAGE_BYTES`.
    """
    journal_kinds = sorted({_JOURNAL_KINDS[kind] for kind in query.kinds})
    events = journal.read_since(query.after_seq, kinds=journal_kinds, limit=FEED_SCAN_LIMIT + 1)
    rows: list[dict[str, Any]] = []
    used = 0
    cursor = query.after_seq
    scanned = 0
    for event in events[:FEED_SCAN_LIMIT]:
        row = _row(event, query.kinds, markers)
        if row is not None:
            encoded = len(json.dumps(row, ensure_ascii=False).encode("utf-8"))
            if used + encoded > FEED_PAGE_BYTES:
                if not rows:
                    raise FeedRefused("a feed row exceeds the response size limit")
                break
            rows.append(row)
            used += encoded
        cursor = event.seq
        scanned += 1
        if len(rows) == query.limit:
            break
    return FeedPage(tuple(rows), cursor, scanned < len(events))


class FeedLedger:
    """What this hub served each consumer, and the acks its journal holds.

    The served high-water lives in this process only: after a restart a consumer
    reads a page before its next ack. The acks live in the journal as
    ``feed_ack`` events; the ledger reads a consumer's highest ack for the current
    instance on first use and keeps it current as acks are recorded.

    Parameters
    ----------
    markers : sequence of str
        The chat markers this hub serves as ``chat_marker``.
    """

    def __init__(self, markers: Sequence[str] = DEFAULT_FEED_CHAT_MARKERS) -> None:
        self.markers = tuple(markers)
        self._served: dict[tuple[str, str], int] = {}
        self._acked: dict[tuple[str, str], int] = {}
        self._lock = threading.Lock()

    def served(self, consumer: str, instance: str, next_after: int) -> None:
        """Record that a page ending at ``next_after`` was served to ``consumer``."""
        with self._lock:
            key = (consumer, instance)
            self._served[key] = max(self._served.get(key, 0), next_after)

    def served_through(self, consumer: str, instance: str) -> int:
        """Return the highest ``next_after`` this hub served ``consumer``, or ``-1``."""
        with self._lock:
            return self._served.get((consumer, instance), -1)

    def acked(self, journal: FeedJournal, consumer: str, instance: str) -> int:
        """Return the consumer's highest recorded ack for ``instance``, or ``0``."""
        key = (consumer, instance)
        with self._lock:
            if key in self._acked:
                return self._acked[key]
        highest = 0
        after = 0
        while True:
            events = journal.read_since(after, kinds=(EventKind.FEED_ACK,), limit=FEED_SCAN_LIMIT)
            for event in events:
                payload = event.payload
                if (
                    payload.get("consumer") == consumer
                    and payload.get("journal_instance") == instance
                ):
                    value = payload.get("acked_seq")
                    if type(value) is int:
                        highest = max(highest, value)
            if len(events) < FEED_SCAN_LIMIT:
                break
            after = events[-1].seq
        with self._lock:
            self._acked[key] = max(self._acked.get(key, 0), highest)
            return self._acked[key]

    def record(self, consumer: str, instance: str, acked_seq: int) -> None:
        """Keep ``acked_seq`` as the consumer's highest ack after it was journalled."""
        with self._lock:
            key = (consumer, instance)
            self._acked[key] = max(self._acked.get(key, 0), acked_seq)
