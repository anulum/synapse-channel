# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — one startup walk that fills mailbox hits and counters
"""Project a store before the socket binds, and again when the journal no longer matches.

The projection is the address hits, one counter per named mailbox, and each
chat's mentioned mailbox names. A store whose mentions fall behind its extent,
because a writer kept hits but no mentions, is projected again too.
"""

from __future__ import annotations

import logging
from typing import Any

from synapse_channel.core.mailbox_addresses import counts_at_all, identity_addresses, parts
from synapse_channel.core.mailbox_layout_counts import count_after
from synapse_channel.core.mailbox_layout_maintenance import _capped_through
from synapse_channel.core.mailbox_layout_mentions import record_mentions
from synapse_channel.core.mailbox_layout_schema import (
    LAYOUT_VERSION,
    _layout_matches,
    _mentions_current,
    _write_layout_extent,
    require_known_layout,
)

logger = logging.getLogger("synapse.hub")

_CHAT = "chat"
_WATERMARK = "mailbox_watermark"
_PAGE = 2000


def project_mailbox_layout(store: Any) -> None:
    """Project version 0, and any version-1 store whose recorded extent differs.

    A finished store whose highest sequence and chat-row count still match the
    journal, and whose mentions are current, returns without reading chats. Any
    other open drops the projection and walks. The walk writes hits and
    mentions, then one counter per mailbox the journal names, and records the
    extent only after that finishes, so a crash retries the whole walk. The
    caller constructs the mailbox tracker before the listening socket is bound.

    Parameters
    ----------
    store : Any
        An open event store. The walk uses that store's connection and lock.
    """
    connection = store.storage.connection
    with store.storage.lock:
        version = require_known_layout(connection)
        matches = version == LAYOUT_VERSION and _layout_matches(connection)
        if matches and _mentions_current(connection):
            return
        if matches:
            logger.info("mailbox name mentions are behind the journal")
        elif version == LAYOUT_VERSION:
            logger.info("journal changed outside this layout")
        _project_locked(store, connection)


def _project_locked(store: Any, connection: Any) -> None:
    event_rows = int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])
    logger.info("mailbox pending layout walk started event_rows=%s", event_rows)
    connection.execute("DELETE FROM mailbox_address_hits")
    connection.execute("DELETE FROM mailbox_pending_counts")
    connection.execute("DELETE FROM mailbox_name_mentions")
    connection.execute("DELETE FROM mailbox_pending_layout")
    counters = _project_pages(store, connection)
    _store_counters(connection, counters)
    _write_layout_extent(connection)
    connection.commit()
    hit_rows = int(connection.execute("SELECT COUNT(*) FROM mailbox_address_hits").fetchone()[0])
    logger.info(
        "mailbox pending layout walk finished event_rows=%s hit_rows=%s counter_rows=%s",
        event_rows,
        hit_rows,
        len(counters),
    )


def _store_counters(connection: Any, counters: tuple[tuple[str, int, int], ...]) -> None:
    connection.executemany(
        "INSERT INTO mailbox_pending_counts (identity, pending, watermark) VALUES (?, ?, ?)",
        counters,
    )


def _project_pages(store: Any, connection: Any) -> tuple[tuple[str, int, int], ...]:
    """Insert hits for every counted chat and return one counter per named mailbox.

    Only chats and watermarks are read and decoded; no other event kind feeds the
    projection.
    """
    watermarks: dict[str, int] = {}
    named: list[str] = []
    seen: set[str] = set()
    cursor = 0
    while True:
        events = store.read_window(min_seq=cursor + 1, kinds=(_CHAT, _WATERMARK), limit=_PAGE)
        if not events:
            break
        for event in events:
            cursor = int(event.seq)
            _project_event(connection, event, watermarks, seen, named)
        connection.commit()
    return _counters_for(connection, watermarks, named)


def _project_event(
    connection: Any,
    event: Any,
    watermarks: dict[str, int],
    seen: set[str],
    named: list[str],
) -> None:
    payload = event.payload
    if not isinstance(payload, dict):
        return
    if event.kind == _WATERMARK:
        _note_watermark(watermarks, seen, named, payload, int(event.seq))
        return
    if event.kind != _CHAT:
        return
    record_mentions(connection, int(event.seq), payload)
    _insert_hits(connection, int(event.seq), payload)
    for address in identity_addresses(payload):
        _remember_name(seen, named, address)


def _counters_for(
    connection: Any,
    watermarks: dict[str, int],
    named: list[str],
) -> tuple[tuple[str, int, int], ...]:
    rows: list[tuple[str, int, int]] = []
    for identity in named:
        mark = watermarks.get(identity, 0)
        rows.append((identity, count_after(connection, identity, mark), mark))
    return tuple(rows)


def _note_watermark(
    watermarks: dict[str, int],
    seen: set[str],
    named: list[str],
    payload: dict[str, Any],
    seq: int,
) -> None:
    identity = str(payload.get("identity") or "").strip()
    if not identity:
        return
    _remember_name(seen, named, identity)
    capped = _capped_through(payload, seq)
    if capped is None:
        return
    watermarks[identity] = max(watermarks.get(identity, 0), capped)


def _insert_hits(connection: Any, seq: int, payload: dict[str, Any]) -> None:
    if not counts_at_all(payload):
        return
    sender = str(payload.get("sender") or "")
    for address in parts(str(payload.get("target") or "all")):
        connection.execute(
            "INSERT OR IGNORE INTO mailbox_address_hits (seq, address, sender) VALUES (?, ?, ?)",
            (seq, address, sender),
        )


def _remember_name(seen: set[str], named: list[str], identity: str) -> None:
    if identity in seen:
        return
    seen.add(identity)
    named.append(identity)
