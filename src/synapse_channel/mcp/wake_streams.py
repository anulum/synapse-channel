# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable incomplete stream notification recovery
"""Commit stream deadlines with native scan progress before coalescing recovery."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from synapse_channel.mcp.activity_page import ActivityItem, ActivityPage
from synapse_channel.mcp.wake_database import wake_database
from synapse_channel.mcp.wake_pointer import (
    MAX_WAKE_INTEGER,
    WAKE_SOURCES,
    WakeLedgerError,
    WakeScope,
)

STREAM_IDLE_SECONDS = 300
MAX_PENDING_STREAMS = 1024


def _clock(now: int) -> None:
    """Bound deadline arithmetic without accepting boolean timestamps."""
    if type(now) is not int or not 0 <= now <= MAX_WAKE_INTEGER - STREAM_IDLE_SECONDS:
        raise WakeLedgerError("invalid stream recovery timestamp")


def _read(connection: sqlite3.Connection, source: str) -> dict[str, Any] | None:
    """Read bounded JSON state, refusing corruption rather than resetting it."""
    row = connection.execute("SELECT position FROM sources WHERE source=?", (source,)).fetchone()
    if row is None:
        return None
    try:
        if type(row[0]) is not str or len(row[0]) > 160000:
            raise ValueError("invalid stored stream state")
        value: object = json.loads(row[0])
        if not isinstance(value, dict):
            raise ValueError("invalid stored stream state")
        return value
    except (TypeError, ValueError):
        raise WakeLedgerError("invalid stored stream recovery state") from None


def _write(connection: sqlite3.Connection, source: str, value: dict[str, Any]) -> None:
    """Store content-free state in the same transaction as its native cursor."""
    connection.execute(
        "INSERT INTO sources VALUES (?,?) ON CONFLICT(source) "
        "DO UPDATE SET position=excluded.position",
        (source, json.dumps(value, sort_keys=True, separators=(",", ":"))),
    )


def _progress(connection: sqlite3.Connection) -> dict[str, Any] | None:
    """Validate the independent version-two cursor and frozen legacy boundary."""
    value = _read(connection, "stream-recovery")
    if value is None:
        return None
    if (
        set(value) != {"previous", "cursor", "floor", "digest"}
        or any(
            type(value[k]) is not int or not 0 <= value[k] <= MAX_WAKE_INTEGER
            for k in ("previous", "cursor", "floor")
        )
        or value["previous"] > value["cursor"]
        or type(value["digest"]) is not str
        or re.fullmatch(r"[0-9a-f]{64}", value["digest"]) is None
    ):
        raise WakeLedgerError("invalid stored stream recovery cursor")
    return value


def _streams(connection: sqlite3.Connection) -> dict[str, int]:
    """Validate the finite fingerprint-to-deadline population."""
    value = _read(connection, "stream-progress")
    if value is None:
        return {}
    if len(value) > MAX_PENDING_STREAMS or any(
        re.fullmatch(r"[0-9a-f]{64}", key) is None
        or type(deadline) is not int
        or not 0 <= deadline <= MAX_WAKE_INTEGER
        for key, deadline in value.items()
    ):
        raise WakeLedgerError("invalid stored stream deadline population")
    return value


def _legacy_floor(connection: sqlite3.Connection) -> int:
    """Keep already observed messages from waking again during stream backfill."""
    value = _read(connection, "activity")
    if value is None:
        return 0
    if (
        set(value) != {"previous", "cursor", "bits"}
        or any(
            type(value[k]) is not int or not 0 <= value[k] <= MAX_WAKE_INTEGER
            for k in ("previous", "cursor", "bits")
        )
        or value["previous"] > value["cursor"]
        or value["bits"] > 7
    ):
        raise WakeLedgerError("invalid legacy stream recovery boundary")
    return int(value["cursor"])


def _notify(connection: sqlite3.Connection, bits: int) -> None:
    """Set pending families without permitting revision overflow or half commits."""
    if (
        bits
        and connection.execute(
            "UPDATE meta SET revision=revision+1,pending=pending|? WHERE revision<?",
            (bits, MAX_WAKE_INTEGER),
        ).rowcount
        != 1
    ):
        raise WakeLedgerError("wake revision budget exhausted")


def _recovery_item(item: ActivityItem) -> dict[str, object]:
    """Validate one typed stream/source relationship before hashing its page."""
    if (
        item.kind not in (*WAKE_SOURCES, "streams")
        or (item.stream is None and item.kind == "streams")
        or (
            item.stream is not None
            and (
                item.stream.seq != item.seq
                or item.kind != ("messages" if item.stream.terminal else "streams")
            )
        )
    ):
        raise WakeLedgerError("invalid stream recovery family")
    return {
        "seq": item.seq,
        "kind": item.kind,
        "stream": item.stream.encode() if item.stream else None,
    }


def _page_digest(page: ActivityPage, previous: int, scope: WakeScope) -> str:
    """Fence the full ordered page, including nonterminal stream progress."""
    if (
        page.identity != scope.seat
        or page.hub_id != scope.hub_id
        or type(previous) is not int
        or type(page.cursor) is not int
        or not 0 <= previous <= page.cursor <= MAX_WAKE_INTEGER
        or type(page.items) is not tuple
        or len(page.items) > 100
        or type(page.has_more) is not bool
        or (page.has_more and previous == page.cursor)
    ):
        raise WakeLedgerError("invalid stream recovery page")
    entries: list[dict[str, object]] = []
    for item in page.items:
        if type(item.seq) is not int or not previous < item.seq <= page.cursor:
            raise WakeLedgerError("invalid stream recovery sequence")
        previous = item.seq
        entries.append(_recovery_item(item))
    return hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()


class WakeStreamObservation:
    """Persist incomplete streams independently of business inbox consumption."""

    path: Path
    scope: WakeScope

    def recovery_cursor(self) -> int:
        """Read stream-aware progress; legacy cursor migration starts at zero."""
        with wake_database(self.path, self.scope) as connection:
            value = _progress(connection)
            return int(value["cursor"]) if value is not None else 0

    def observe_recovery_page(self, previous: int, page: ActivityPage, *, now: int) -> bool:
        """Atomically keep stream deadlines, cursor and relevant pending families.

        Parameters
        ----------
        previous : int
            Committed stream-aware cursor used to request this page.
        page : ActivityPage
            Validated version-two page from the existing admitted connection.
        now : int
            Observer UTC seconds; future native timestamps are clamped to it.

        Returns
        -------
        bool
            Whether newly scanned external work added a wake. Partial stream
            progress only records its deadline; exact repeated pages do nothing.
        """
        _clock(now)
        digest = _page_digest(page, previous, self.scope)
        with wake_database(self.path, self.scope) as connection:
            current = _progress(connection)
            floor = current["floor"] if current else _legacy_floor(connection)
            marker = {"previous": previous, "cursor": page.cursor, "floor": floor, "digest": digest}
            if current == marker:
                return False
            if previous != (current["cursor"] if current else 0):
                raise WakeLedgerError("stream recovery position changed")
            streams = _streams(connection)
            bits = 0
            for item in page.items:
                if item.kind != "streams" and item.seq > floor:
                    bits |= 1 << WAKE_SOURCES.index(item.kind)
                stream = item.stream
                if stream is None:
                    continue
                if stream.terminal:
                    streams.pop(stream.key, None)
                else:
                    streams[stream.key] = max(
                        streams.get(stream.key, 0), min(now, stream.at) + STREAM_IDLE_SECONDS
                    )
            if len(streams) > MAX_PENDING_STREAMS:
                raise WakeLedgerError("stream recovery capacity exhausted")
            _write(connection, "stream-progress", streams)
            _write(connection, "stream-recovery", marker)
            _notify(connection, bits)
            return bool(bits)

    def expire_streams(self, *, now: int) -> int:
        """Queue one coalesced recovery hint after a fully caught-up native scan.

        Parameters
        ----------
        now : int
            Observer UTC seconds. Call only after catching up the native journal,
            so a terminal frame on a later page cannot be mistaken for a failure.

        Returns
        -------
        int
            Number of expired streams. Removal and the messages pending bit
            commit together; restart cannot silently lose or repeat this decision.
            No synthetic publisher ABORT or native processing ACK is written.
        """
        _clock(now)
        with wake_database(self.path, self.scope) as connection:
            streams = _streams(connection)
            remaining = {key: deadline for key, deadline in streams.items() if deadline > now}
            expired = len(streams) - len(remaining)
            if expired:
                _write(connection, "stream-progress", remaining)
                _notify(connection, 1)
            return expired
