# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — mailbox hits written with each chat, watermark, and deletion
"""Update mailbox hits and existing counters inside the caller's transaction."""

from __future__ import annotations

import json
from typing import Any

from synapse_channel.core.mailbox_addresses import any_reaches, counts_at_all, parts, reaches
from synapse_channel.core.mailbox_layout_counts import count_after, remember_count, stored_count
from synapse_channel.core.mailbox_layout_schema import (
    _advance_layout_extent,
    _refresh_layout_extent,
    ensure_mailbox_schema,
)

_CHAT = "chat"
_WATERMARK = "mailbox_watermark"
_CHUNK = 400


def record_inserted_event(connection: Any, seq: int, kind: str, payload: str) -> None:
    """Record one new event's mailbox rows inside the caller's transaction.

    A chat writes one hit per target part and increments each existing counter
    the part reaches. A watermark moves that mailbox's cursor and stores the
    count strictly after it. Every kind moves the recorded extent when a layout
    row already exists: the highest sequence, and the chat-row count when the
    kind is a chat. The caller commits.

    Parameters
    ----------
    connection : Any
        The event store's own connection, already inside the append transaction.
    seq : int
        The sequence just assigned to the event.
    kind : str
        The event kind.
    payload : str
        The event payload as its stored JSON text.
    """
    ensure_mailbox_schema(connection)
    _advance_layout_extent(connection, seq, kind)
    if kind == _CHAT:
        _record_chat(connection, seq, payload)
        return
    if kind == _WATERMARK:
        _record_watermark(connection, seq, payload)


def record_deleted_sequences(connection: Any, seqs: list[int]) -> None:
    """Drop hits for sequences that were actually deleted, and fix their counters.

    A chat already at or below a mailbox's watermark was not in that count, so
    the counter stays. The recorded extent is taken from the journal again,
    after those rows are gone. The caller has removed the event rows and holds
    the transaction. A sequence the delete did not remove must not be passed in.

    Parameters
    ----------
    connection : Any
        The event store's own connection.
    seqs : list[int]
        Sequence numbers whose event rows were removed.
    """
    if not seqs:
        return
    ensure_mailbox_schema(connection)
    _decrement_deleted(connection, _hits_for(connection, seqs))
    _delete_hits(connection, seqs)
    _refresh_layout_extent(connection)


def _record_chat(connection: Any, seq: int, payload: str) -> None:
    ensure_mailbox_schema(connection)
    body = _object(payload)
    if body is None or not counts_at_all(body):
        return
    sender = str(body.get("sender") or "")
    addresses = parts(str(body.get("target") or "all"))
    for address in addresses:
        connection.execute(
            "INSERT OR IGNORE INTO mailbox_address_hits (seq, address, sender) VALUES (?, ?, ?)",
            (seq, address, sender),
        )
    _bump_existing(connection, seq, sender, addresses)


def _bump_existing(connection: Any, seq: int, sender: str, addresses: tuple[str, ...]) -> None:
    if not addresses:
        return
    rows = connection.execute("SELECT identity, watermark FROM mailbox_pending_counts").fetchall()
    for identity, watermark in rows:
        name = str(identity)
        if int(seq) <= int(watermark) or sender == name:
            continue
        if any_reaches(addresses, name):
            connection.execute(
                "UPDATE mailbox_pending_counts SET pending = pending + 1 WHERE identity = ?",
                (name,),
            )


def _record_watermark(connection: Any, seq: int, payload: str) -> None:
    ensure_mailbox_schema(connection)
    body = _object(payload)
    if body is None:
        return
    identity = str(body.get("identity") or "").strip()
    if not identity:
        return
    capped = _capped_through(body, seq)
    if capped is None:
        return
    current = stored_count(connection, identity)
    previous = 0 if current is None else current[1]
    if capped <= previous:
        return
    remember_count(connection, identity, count_after(connection, identity, capped), capped)


def _decrement_deleted(connection: Any, hits: list[tuple[Any, Any, Any]]) -> None:
    counters = connection.execute(
        "SELECT identity, pending, watermark FROM mailbox_pending_counts"
    ).fetchall()
    removed: dict[str, int] = {}
    seen: set[tuple[int, str]] = set()
    for seq, address, sender in hits:
        _count_deleted_hit(counters, removed, seen, int(seq), str(address), str(sender))
    for identity, count in removed.items():
        connection.execute(
            "UPDATE mailbox_pending_counts SET pending = MAX(0, pending - ?) WHERE identity = ?",
            (count, identity),
        )


def _count_deleted_hit(
    counters: list[Any],
    removed: dict[str, int],
    seen: set[tuple[int, str]],
    seq: int,
    address: str,
    sender: str,
) -> None:
    for identity, _pending, watermark in counters:
        name = str(identity)
        key = (seq, name)
        if key in seen or seq <= int(watermark) or sender == name:
            continue
        if reaches(address, name):
            seen.add(key)
            removed[name] = removed.get(name, 0) + 1


def _hits_for(connection: Any, seqs: list[int]) -> list[tuple[Any, Any, Any]]:
    found: list[tuple[Any, Any, Any]] = []
    for chunk in _chunks(seqs):
        marks = ",".join("?" for _ in chunk)
        found.extend(
            connection.execute(
                f"SELECT seq, address, sender FROM mailbox_address_hits WHERE seq IN ({marks})",
                chunk,
            )
        )
    return found


def _delete_hits(connection: Any, seqs: list[int]) -> None:
    for chunk in _chunks(seqs):
        marks = ",".join("?" for _ in chunk)
        connection.execute(
            f"DELETE FROM mailbox_address_hits WHERE seq IN ({marks})",
            chunk,
        )


def _chunks(seqs: list[int]) -> list[list[int]]:
    return [seqs[start : start + _CHUNK] for start in range(0, len(seqs), _CHUNK)]


def _capped_through(payload: dict[str, Any], seq: int) -> int | None:
    raw = payload.get("through_seq")
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        return None
    return min(raw, seq)


def _object(payload: str) -> dict[str, Any] | None:
    try:
        body = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(body, dict):
        return None
    return body
