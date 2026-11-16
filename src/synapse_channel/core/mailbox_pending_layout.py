# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable mailbox pending counts and address hits
"""Keep mailbox pending counts exact without reading the chat journal on the loop.

Two tables live on the event store's one connection. ``mailbox_address_hits``
records each target part of a counted chat. ``mailbox_pending_counts`` stores
the integer a mailbox serves, for chats strictly after that mailbox's
watermark. Both are written in the same transaction as the chat, the watermark,
or the deletion. Layout version 1 is one row. A newer version refuses startup
and does not rewrite the tables. A store with no version row is projected once,
before the hub binds its socket.
"""

from __future__ import annotations

import json
import logging
from fnmatch import fnmatchcase
from typing import Any

from synapse_channel.core.dead_letters import is_directed_target
from synapse_channel.core.hub_address import is_hub_qualified_name
from synapse_channel.core.role_mailbox import mailbox_role

logger = logging.getLogger("synapse.hub")

LAYOUT_VERSION = 1
"""The only mailbox-count layout this code opens."""

_CHAT = "chat"
_WATERMARK = "mailbox_watermark"
_PAGE = 2000
_CHUNK = 400
_GLOB_SQL = "(instr(address, '?') > 0 OR instr(address, '[') > 0 OR instr(address, '*') > 0)"
_KNOWN_COLUMNS = (
    ("mailbox_pending_counts", "pending", "INTEGER NOT NULL DEFAULT 0"),
    ("mailbox_pending_counts", "watermark", "INTEGER NOT NULL DEFAULT 0"),
    ("mailbox_address_hits", "sender", "TEXT NOT NULL DEFAULT ''"),
)


class MailboxLayoutRefused(ValueError):
    """The mailbox-count tables are a layout this process must not interpret."""


def ensure_mailbox_schema(connection: Any) -> None:
    """Create the count tables when this store does not have them yet.

    An existing table is never dropped. A column this code already knows how to
    read is added when an older table lacks it. Nothing else is rewritten.

    Parameters
    ----------
    connection : Any
        The event store's own connection.
    """
    connection.execute(
        "CREATE TABLE IF NOT EXISTS mailbox_address_hits ("
        "seq INTEGER NOT NULL, address TEXT NOT NULL, sender TEXT NOT NULL, "
        "PRIMARY KEY(seq, address))"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS mailbox_address_hits_address_seq "
        "ON mailbox_address_hits(address, seq)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS mailbox_address_hits_glob_seq "
        "ON mailbox_address_hits(seq) WHERE " + _GLOB_SQL
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS mailbox_pending_counts ("
        "identity TEXT PRIMARY KEY, pending INTEGER NOT NULL, watermark INTEGER NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS mailbox_pending_layout (version INTEGER NOT NULL)"
    )
    for table, column, declaration in _KNOWN_COLUMNS:
        _add_known_column(connection, table, column, declaration)


def refuse_unknown_mailbox_layout(connection: Any) -> None:
    """Refuse a layout version this code does not know, and change nothing.

    Parameters
    ----------
    connection : Any
        The event store's own connection.

    Raises
    ------
    MailboxLayoutRefused
        When the stored version is newer than :data:`LAYOUT_VERSION`, or is not
        an integer. The tables are left as they are.
    """
    version = _stored_version(connection)
    if version is None or version <= LAYOUT_VERSION:
        return
    _refuse_version(version)


def layout_version(connection: Any) -> int:
    """Return the stored layout version, or 0 when the store has not been projected.

    Parameters
    ----------
    connection : Any
        The event store's own connection.

    Returns
    -------
    int
        ``0`` when no version row exists, otherwise the stored integer.
    """
    ensure_mailbox_schema(connection)
    version = _stored_version(connection)
    return 0 if version is None else version


def require_known_layout(connection: Any) -> int:
    """Return the layout version after refusing one this code does not know.

    Parameters
    ----------
    connection : Any
        The event store's own connection.

    Returns
    -------
    int
        ``0`` or :data:`LAYOUT_VERSION`.
    """
    refuse_unknown_mailbox_layout(connection)
    return layout_version(connection)


def project_mailbox_layout(store: Any) -> None:
    """Project version 0 once. A finished store returns without reading chats.

    The walk drops any partial projection, reads the journal in pages, writes
    hits, then stores one counter per mailbox the journal names. Version 1 is
    recorded only after that finishes, so a crash retries the whole walk. The
    caller constructs the mailbox tracker before the listening socket is bound.

    Parameters
    ----------
    store : Any
        An open event store. The walk uses that store's connection and lock.
    """
    connection = store.storage.connection
    with store.storage.lock:
        if require_known_layout(connection) == LAYOUT_VERSION:
            return
        _project_locked(store, connection)


def record_inserted_event(connection: Any, seq: int, kind: str, payload: str) -> None:
    """Record one new event's mailbox rows inside the caller's transaction.

    A chat writes one hit per target part and increments each existing counter
    the part reaches. A watermark moves that mailbox's cursor and stores the
    count strictly after it. Every other kind writes nothing. The caller commits.

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
    if kind == _CHAT:
        _record_chat(connection, seq, payload)
        return
    if kind == _WATERMARK:
        _record_watermark(connection, seq, payload)


def record_deleted_sequences(connection: Any, seqs: list[int]) -> None:
    """Drop hits for sequences that were actually deleted, and fix their counters.

    A chat already at or below a mailbox's watermark was not in that count, so
    the counter stays. The caller has removed the event rows and holds the
    transaction. A sequence the delete did not remove must not be passed in.

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


def stored_count(connection: Any, identity: str) -> tuple[int, int] | None:
    """Return one mailbox's pending count and watermark, or None when it has no row.

    Parameters
    ----------
    connection : Any
        The event store's own connection.
    identity : str
        A personal name or a ``role:`` mailbox key.

    Returns
    -------
    tuple[int, int] or None
        ``(pending, watermark)`` for a stored mailbox.
    """
    ensure_mailbox_schema(connection)
    row = connection.execute(
        "SELECT pending, watermark FROM mailbox_pending_counts WHERE identity = ?",
        (identity,),
    ).fetchone()
    if row is None:
        return None
    return int(row[0]), int(row[1])


def count_after(connection: Any, identity: str, watermark: int) -> int:
    """Count hits strictly after ``watermark`` that reach ``identity``.

    Parameters
    ----------
    connection : Any
        The event store's own connection.
    identity : str
        A personal name or a ``role:`` mailbox key.
    watermark : int
        The mailbox's accepted sequence. Hits at or below it are closed.

    Returns
    -------
    int
        The pending count.
    """
    return count_between(connection, identity, watermark + 1, None)


def count_between(connection: Any, identity: str, min_seq: int, max_seq: int | None) -> int:
    """Count hits in an inclusive sequence window that reach ``identity``.

    Parameters
    ----------
    connection : Any
        The event store's own connection.
    identity : str
        A personal name or a ``role:`` mailbox key.
    min_seq : int
        Inclusive lower sequence.
    max_seq : int or None
        Inclusive upper sequence. ``None`` does not cap the window.

    Returns
    -------
    int
        How many distinct chats in the window reach the mailbox.
    """
    ensure_mailbox_schema(connection)
    if max_seq is not None and max_seq < min_seq:
        return 0
    exact = _exact_addresses(identity)
    total = _count_exact(connection, identity, exact, min_seq, max_seq)
    return total + _count_glob_only(connection, identity, exact, min_seq, max_seq)


def remember_count(connection: Any, identity: str, pending: int, watermark: int) -> None:
    """Store one mailbox's count so the next read is a single row.

    Parameters
    ----------
    connection : Any
        The event store's own connection.
    identity : str
        A personal name or a ``role:`` mailbox key.
    pending : int
        The pending count to serve.
    watermark : int
        The watermark that count was taken after.
    """
    ensure_mailbox_schema(connection)
    connection.execute(
        "INSERT INTO mailbox_pending_counts (identity, pending, watermark) VALUES (?, ?, ?) "
        "ON CONFLICT(identity) DO UPDATE SET pending = excluded.pending, "
        "watermark = excluded.watermark",
        (identity, pending, watermark),
    )


def load_counters(connection: Any) -> tuple[tuple[str, int, int], ...]:
    """Return every stored counter in insertion order.

    Parameters
    ----------
    connection : Any
        The event store's own connection.

    Returns
    -------
    tuple[tuple[str, int, int], ...]
        ``(identity, pending, watermark)`` rows, oldest mailbox first.
    """
    ensure_mailbox_schema(connection)
    rows = connection.execute(
        "SELECT identity, pending, watermark FROM mailbox_pending_counts ORDER BY rowid"
    ).fetchall()
    return tuple(
        (str(identity), int(pending), int(watermark)) for identity, pending, watermark in rows
    )


def copy_mailbox_layout(source: Any, destination: Any) -> bool:
    """Copy a finished layout so a converted store does not walk again.

    A source that has not been projected is left out of ``destination``. The
    converted store's first open then walks the copied events. Rows keep their
    sequence numbers, which the copied hits still name. The caller commits.

    Parameters
    ----------
    source : Any
        The plaintext store connection.
    destination : Any
        The new store connection.

    Returns
    -------
    bool
        ``True`` when a version-1 layout was copied.
    """
    if _stored_version(source) != LAYOUT_VERSION:
        return False
    ensure_mailbox_schema(destination)
    _copy_rows(source, destination)
    return True


def _project_locked(store: Any, connection: Any) -> None:
    event_rows = int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])
    logger.info("mailbox pending layout walk started event_rows=%s", event_rows)
    connection.execute("DELETE FROM mailbox_address_hits")
    connection.execute("DELETE FROM mailbox_pending_counts")
    connection.execute("DELETE FROM mailbox_pending_layout")
    counters = _project_pages(store, connection)
    _store_counters(connection, counters)
    connection.execute("INSERT INTO mailbox_pending_layout (version) VALUES (?)", (LAYOUT_VERSION,))
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
    """Insert hits for every counted chat and return one counter per named mailbox."""
    watermarks: dict[str, int] = {}
    named: list[str] = []
    seen: set[str] = set()
    cursor = 0
    while True:
        events = store.read_window(min_seq=cursor + 1, limit=_PAGE)
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
    _insert_hits(connection, int(event.seq), payload)
    for address in _identity_addresses(payload):
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


def _record_chat(connection: Any, seq: int, payload: str) -> None:
    ensure_mailbox_schema(connection)
    body = _object(payload)
    if body is None or not _counts_at_all(body):
        return
    sender = str(body.get("sender") or "")
    addresses = _parts(str(body.get("target") or "all"))
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
        if _any_reaches(addresses, name):
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
        if _reaches(address, name):
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


def _insert_hits(connection: Any, seq: int, payload: dict[str, Any]) -> None:
    if not _counts_at_all(payload):
        return
    sender = str(payload.get("sender") or "")
    for address in _parts(str(payload.get("target") or "all")):
        connection.execute(
            "INSERT OR IGNORE INTO mailbox_address_hits (seq, address, sender) VALUES (?, ?, ?)",
            (seq, address, sender),
        )


def _count_exact(
    connection: Any,
    identity: str,
    addresses: tuple[str, ...],
    min_seq: int,
    max_seq: int | None,
) -> int:
    bound_sql, params = _bound(min_seq, max_seq)
    marks = ",".join("?" for _ in addresses)
    row = connection.execute(
        "SELECT COUNT(DISTINCT seq) FROM mailbox_address_hits "
        f"WHERE {bound_sql} AND sender != ? AND address IN ({marks})",
        (*params, identity, *addresses),
    ).fetchone()
    return int(row[0])


def _count_glob_only(
    connection: Any,
    identity: str,
    addresses: tuple[str, ...],
    min_seq: int,
    max_seq: int | None,
) -> int:
    bound_sql, params = _bound(min_seq, max_seq)
    rows = connection.execute(
        "SELECT seq, address FROM mailbox_address_hits "
        f"WHERE {bound_sql} AND sender != ? AND {_GLOB_SQL}",
        (*params, identity),
    ).fetchall()
    return _extra_glob_seqs(connection, identity, addresses, rows)


def _extra_glob_seqs(
    connection: Any,
    identity: str,
    addresses: tuple[str, ...],
    rows: list[Any],
) -> int:
    seen: set[int] = set()
    extra = 0
    for seq, address in rows:
        number = int(seq)
        if number in seen or not _reaches(str(address), identity):
            continue
        seen.add(number)
        if not _same_seq_exact(connection, number, addresses):
            extra += 1
    return extra


def _same_seq_exact(connection: Any, seq: int, addresses: tuple[str, ...]) -> bool:
    marks = ",".join("?" for _ in addresses)
    row = connection.execute(
        "SELECT 1 FROM mailbox_address_hits WHERE seq = ? AND address IN (" + marks + ") LIMIT 1",
        (seq, *addresses),
    ).fetchone()
    return row is not None


def _copy_rows(source: Any, destination: Any) -> None:
    destination.executemany(
        "INSERT INTO mailbox_address_hits (seq, address, sender) VALUES (?, ?, ?)",
        list(source.execute("SELECT seq, address, sender FROM mailbox_address_hits ORDER BY seq")),
    )
    destination.executemany(
        "INSERT INTO mailbox_pending_counts (identity, pending, watermark) VALUES (?, ?, ?)",
        list(source.execute("SELECT identity, pending, watermark FROM mailbox_pending_counts")),
    )
    destination.execute("DELETE FROM mailbox_pending_layout")
    destination.execute(
        "INSERT INTO mailbox_pending_layout (version) VALUES (?)",
        (LAYOUT_VERSION,),
    )


def _add_known_column(connection: Any, table: str, column: str, declaration: str) -> None:
    present = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in present:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def _stored_version(connection: Any) -> int | None:
    names = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if "mailbox_pending_layout" not in names:
        return None
    row = connection.execute("SELECT version FROM mailbox_pending_layout LIMIT 1").fetchone()
    if row is None:
        return None
    version = row[0]
    if isinstance(version, bool) or not isinstance(version, int):
        raise MailboxLayoutRefused("mailbox pending layout version is not an integer")
    return version


def _refuse_version(version: int) -> None:
    raise MailboxLayoutRefused(
        f"unsupported mailbox pending layout version {version}; "
        f"this build understands version {LAYOUT_VERSION} and does not change the tables"
    )


def _bound(min_seq: int, max_seq: int | None) -> tuple[str, list[int]]:
    if max_seq is None:
        return "seq >= ?", [min_seq]
    return "seq >= ? AND seq <= ?", [min_seq, max_seq]


def _chunks(seqs: list[int]) -> list[list[int]]:
    return [seqs[start : start + _CHUNK] for start in range(0, len(seqs), _CHUNK)]


def _capped_through(payload: dict[str, Any], seq: int) -> int | None:
    raw = payload.get("through_seq")
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        return None
    return min(raw, seq)


def _counts_at_all(payload: dict[str, Any]) -> bool:
    if payload.get("channel"):
        return False
    return is_directed_target(str(payload.get("target") or "all"))


def _parts(target: str) -> tuple[str, ...]:
    return tuple(part for raw in target.split(",") if (part := raw.strip()))


def _identity_addresses(payload: dict[str, Any]) -> tuple[str, ...]:
    """Return exact local parts a chat introduces as mailbox names."""
    if not _counts_at_all(payload):
        return ()
    return tuple(
        address
        for address in _parts(str(payload.get("target") or "all"))
        if not _is_glob(address) and not is_hub_qualified_name(address)
    )


def _exact_addresses(identity: str) -> tuple[str, ...]:
    role = mailbox_role(identity)
    if role is not None:
        return (role,)
    project = identity.split("/", 1)[0]
    if project == identity:
        return (identity,)
    return (identity, project)


def _is_glob(address: str) -> bool:
    return "?" in address or "[" in address or "*" in address


def _any_reaches(addresses: tuple[str, ...], identity: str) -> bool:
    return any(_reaches(address, identity) for address in addresses)


def _reaches(address: str, identity: str) -> bool:
    """Return whether one stored target part reaches one mailbox, as the scan did."""
    role = mailbox_role(identity)
    if role is not None:
        return fnmatchcase(role, address)
    project = identity.split("/", 1)[0]
    return fnmatchcase(identity, address) or fnmatchcase(project, address)


def _remember_name(seen: set[str], named: list[str], identity: str) -> None:
    if identity in seen:
        return
    seen.add(identity)
    named.append(identity)


def _object(payload: str) -> dict[str, Any] | None:
    try:
        body = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(body, dict):
        return None
    return body
