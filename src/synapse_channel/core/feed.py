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

``feed_bind`` moves a consumer that kept only a legacy, sequence-only position
onto a bound cursor, by proof: the consumer names the last row it ingested by
its feed kind, its payload digest and, when it kept it, its sequence. The hub
binds only when exactly that row is one the feed serves the consumer.

Feed kinds are a closed list. ``finding`` serves the hub's findings. ``chat_marker``
serves undirected, untagged chat whose text begins with one of the hub's
configured markers; directed and channel chat are never served, and plain
``chat`` is never a feed kind.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from synapse_channel.core.event_row_recovery import CORRUPT_EVENT_KIND
from synapse_channel.core.feed_retention import applied_compaction_floor, consumer_acks
from synapse_channel.core.hub_defaults import DEFAULT_FEED_CHAT_MARKERS
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import StoredEvent
from synapse_channel.core.persistence_store_format import JournalInstance

FEED_FINDING = "finding"
"""The feed kind of the hub's durable findings."""

FEED_CHAT_MARKER = "chat_marker"
"""The feed kind of undirected, untagged chat that opens with a configured marker."""

FEED_KINDS = frozenset({FEED_FINDING, FEED_CHAT_MARKER})
"""Every kind a feed may serve; chat and directed messages are never among them."""


FEED_CHAT_MARKERS_MAX = 16
"""Most chat markers one hub serves."""

FEED_CHAT_MARKER_MAX_CHARS = 64
"""Longest chat marker, in characters."""

FEED_SCAN_LIMIT = 1000
"""Maximum journal rows one feed request inspects."""

FEED_PAGE_BYTES = 7 * 1024 * 1024
"""Maximum encoded row bytes of one page, below the client frame ceiling."""

FEED_MAX_LIMIT = 500
"""Largest page a consumer may ask for."""

_JOURNAL_KINDS = {FEED_FINDING: EventKind.FINDING, FEED_CHAT_MARKER: EventKind.CHAT}

FEED_BIND_INTERVAL_SECONDS = 60.0
"""Least time between two binding attempts of one consumer on one journal instance."""

LEGACY_BINDING_UNVERIFIED = "legacy_binding_unverified"
"""Refusal code of a binding whose proof names no single row the feed serves."""

_MAX_SEQ = 9223372036854775807
_DIGEST = re.compile(r"[0-9a-f]{64}")


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
    """A feed request the hub answers with a refusal and no rows.

    Parameters
    ----------
    message : str
        The refusal the consumer reads.
    code : str
        A stable code a consumer may branch on; empty for most refusals.
    """

    def __init__(self, message: str, code: str = "") -> None:
        super().__init__(message)
        self.code = code


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
    after_seq = _plain_int(query.get("after_seq"), 0, _MAX_SEQ)
    limit = _plain_int(query.get("limit"), 1, FEED_MAX_LIMIT)
    if after_seq is None or limit is None:
        raise FeedRefused("invalid feed query")
    kinds = _query_kinds(query.get("kinds"))
    if after_seq != 0 and not (hub_id and instance):
        raise FeedRefused("a feed query past sequence 0 names its hub and journal instance")
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


def feed_chat_markers(values: Iterable[object]) -> tuple[str, ...]:
    """Return a hub's chat markers, in the operator's order, after checking them.

    Parameters
    ----------
    values : iterable
        The markers the operator named.

    Returns
    -------
    tuple of str
        The markers, unchanged.

    Raises
    ------
    ValueError
        When there are none, or more than :data:`FEED_CHAT_MARKERS_MAX`; when one
        is not text, is empty, begins or ends with whitespace, is longer than
        :data:`FEED_CHAT_MARKER_MAX_CHARS` or holds a character that is not
        printable; or when one repeats.
    """
    named = tuple(values)
    if not 1 <= len(named) <= FEED_CHAT_MARKERS_MAX:
        raise ValueError(
            f"a hub serves 1 to {FEED_CHAT_MARKERS_MAX} chat markers, not {len(named)}"
        )
    markers: list[str] = []
    for marker in named:
        if not isinstance(marker, str) or not marker or marker != marker.strip():
            raise ValueError(
                f"a chat marker is text without surrounding whitespace, not {marker!r:.80}"
            )
        if len(marker) > FEED_CHAT_MARKER_MAX_CHARS:
            raise ValueError(
                f"a chat marker is at most {FEED_CHAT_MARKER_MAX_CHARS} characters, "
                f"not {len(marker)}"
            )
        if not marker.isprintable():
            raise ValueError(f"a chat marker holds only printable characters, not {marker!r:.80}")
        if marker in markers:
            raise ValueError(f"the chat marker {marker!r} is named twice")
        markers.append(marker)
    return tuple(markers)


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


@dataclass(frozen=True)
class LegacyBinding:
    """One validated ``feed_bind`` request.

    Attributes
    ----------
    consumer : str
        The consumer to bind: the connection or one of its roles.
    hub_id : str
        The hub the consumer believes it read; empty when it does not know.
    kind : str
        The feed kind of the proof row.
    payload_sha256 : str
        The proof row's payload digest, as :func:`payload_sha256` computes it.
    seq : int or None
        The proof row's sequence, or ``None`` for a digest-only proof.
    legacy_seq : int or None
        The consumer's legacy cursor, at or above ``seq``; ``None`` with a
        digest-only proof, which binds at the matched row.
    """

    consumer: str
    hub_id: str
    kind: str
    payload_sha256: str
    seq: int | None
    legacy_seq: int | None


def parse_feed_bind(data: Mapping[str, Any]) -> LegacyBinding:
    """Validate a ``feed_bind`` frame.

    Raises
    ------
    FeedRefused
        When a field is missing, of the wrong type or out of range, the kind is
        not a feed kind, or the legacy cursor lies below the proof row.
    """
    consumer, hub_id, proof = data.get("consumer"), data.get("hub_id", ""), data.get("proof")
    if (
        not isinstance(consumer, str)
        or not consumer.strip()
        or not isinstance(hub_id, str)
        or not isinstance(proof, Mapping)
    ):
        raise FeedRefused("invalid feed binding")
    kind, digest, seq = proof.get("kind"), proof.get("payload_sha256"), proof.get("seq")
    if (
        not isinstance(kind, str)
        or kind not in FEED_KINDS
        or not isinstance(digest, str)
        or not _DIGEST.fullmatch(digest)
    ):
        raise FeedRefused("invalid feed binding")
    if seq is None:
        if data.get("legacy_seq") is not None:
            raise FeedRefused("a digest-only binding carries no legacy_seq")
        return LegacyBinding(consumer, hub_id, kind, digest, None, None)
    proof_seq = _plain_int(seq, 1, _MAX_SEQ)
    legacy_seq = _plain_int(data.get("legacy_seq"), 1, _MAX_SEQ)
    if proof_seq is None or legacy_seq is None or legacy_seq < proof_seq:
        raise FeedRefused("invalid feed binding")
    return LegacyBinding(consumer, hub_id, kind, digest, proof_seq, legacy_seq)


def _served_digest(event: StoredEvent, kind: str, markers: Sequence[str]) -> str | None:
    """Return the digest the feed serves for ``event`` as ``kind``, or ``None``."""
    row = _row(event, (kind,), markers)
    digest = None if row is None else row.get("payload_sha256")
    return digest if isinstance(digest, str) else None


def bind_legacy_position(
    journal: FeedJournal, binding: LegacyBinding, markers: Sequence[str]
) -> int:
    """Return the cursor a verified legacy position binds to.

    A proof with a sequence binds at ``legacy_seq`` when the row at that sequence
    is one the feed serves as the proof's kind, with that digest. A digest-only
    proof searches the retained rows of its kind and binds at the one row the
    feed serves with that digest. A row the feed would not serve, such as a
    directed chat or a corrupt row, never proves a position.

    Raises
    ------
    FeedRefused
        With :data:`LEGACY_BINDING_UNVERIFIED` when the proof names no such row,
        or several; without a code when the legacy cursor lies above the
        journal's highest sequence.
    """
    journal_kind = _JOURNAL_KINDS[binding.kind]
    if binding.seq is not None and binding.legacy_seq is not None:
        if binding.legacy_seq > journal.max_seq():
            raise FeedRefused("feed binding exceeds the retained journal")
        events = journal.read_since(binding.seq - 1, kinds=(journal_kind,), limit=1)
        if (
            not events
            or events[0].seq != binding.seq
            or _served_digest(events[0], binding.kind, markers) != binding.payload_sha256
        ):
            raise FeedRefused(
                "the proof row is not served by this journal with that digest",
                LEGACY_BINDING_UNVERIFIED,
            )
        return binding.legacy_seq
    matches: list[int] = []
    after = 0
    while len(matches) < 2:
        events = journal.read_since(after, kinds=(journal_kind,), limit=FEED_SCAN_LIMIT)
        matches += [
            event.seq
            for event in events
            if _served_digest(event, binding.kind, markers) == binding.payload_sha256
        ]
        if len(events) < FEED_SCAN_LIMIT:
            break
        after = events[-1].seq
    if len(matches) != 1:
        found = "no served row" if not matches else "more than one served row"
        raise FeedRefused(f"the proof digest matches {found}", LEGACY_BINDING_UNVERIFIED)
    return matches[0]


class FeedLedger:
    """What this hub served each consumer, and the acks its journal holds.

    The served high-water lives in this process only: after a restart a consumer
    reads a page before its next ack. The acks live in the journal as
    ``feed_ack`` events; the ledger reads a consumer's highest ack for the current
    instance on first use and keeps it current as acks are recorded. The
    compaction floor is read the same way, from ``compaction_floor`` events.

    Parameters
    ----------
    markers : sequence of str
        The chat markers this hub serves as ``chat_marker``, checked by
        :func:`feed_chat_markers`.
    """

    def __init__(self, markers: Sequence[str] = DEFAULT_FEED_CHAT_MARKERS) -> None:
        self.markers = feed_chat_markers(markers)
        self._served: dict[tuple[str, str], int] = {}
        self._acked: dict[tuple[str, str], int] = {}
        self._bind_attempts: dict[tuple[str, str], float] = {}
        self._floors: dict[str, tuple[int, int]] = {}
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
        highest = consumer_acks(journal, instance).get(consumer, 0)
        with self._lock:
            self._acked[key] = max(self._acked.get(key, 0), highest)
            return self._acked[key]

    def compaction_floor(self, journal: FeedJournal, instance: str, through: int) -> int:
        """Return the highest floor a compaction applied in ``instance``, or ``0``.

        The ledger keeps the floor with the sequence it has read through, and each
        call reads only the floor events above that sequence, so a page never
        scans the whole journal. ``through`` is the journal's highest sequence
        read before the scan; a floor journalled later lies above it and is read
        by the next call.
        """
        with self._lock:
            floor, read_through = self._floors.get(instance, (0, 0))
        found = applied_compaction_floor(journal, instance, after_seq=read_through)
        with self._lock:
            kept, kept_through = self._floors.get(instance, (0, 0))
            highest = max(floor, kept, found)
            self._floors[instance] = (highest, max(kept_through, through))
            return highest

    def admit_bind(self, consumer: str, instance: str, now: float) -> bool:
        """Return whether ``consumer`` may attempt a binding at monotonic time ``now``.

        One attempt, verified or not, is admitted per consumer and journal instance
        every :data:`FEED_BIND_INTERVAL_SECONDS`, because a digest-only proof scans
        every retained row of its kind.
        """
        with self._lock:
            key = (consumer, instance)
            last = self._bind_attempts.get(key)
            if last is not None and now - last < FEED_BIND_INTERVAL_SECONDS:
                return False
            self._bind_attempts[key] = now
            return True

    def record(self, consumer: str, instance: str, acked_seq: int) -> None:
        """Keep ``acked_seq`` as the consumer's highest ack after it was journalled."""
        with self._lock:
            key = (consumer, instance)
            self._acked[key] = max(self._acked.get(key, 0), acked_seq)
