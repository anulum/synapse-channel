# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — read and store one mailbox's pending count
"""Read a mailbox count from the address index, without scanning chat bodies.

The tables exist from the moment the event store opens, which creates them, so
no read here checks the schema again.
"""

from __future__ import annotations

import json
from typing import Any

from synapse_channel.core.mailbox_addresses import exact_addresses, reaches


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
    if max_seq is not None and max_seq < min_seq:
        return 0
    exact = exact_addresses(identity)
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
    rows = connection.execute(
        "SELECT identity, pending, watermark FROM mailbox_pending_counts ORDER BY rowid"
    ).fetchall()
    return tuple(
        (str(identity), int(pending), int(watermark)) for identity, pending, watermark in rows
    )


def _count_exact(
    connection: Any,
    identity: str,
    addresses: tuple[str, ...],
    min_seq: int,
    max_seq: int | None,
) -> int:
    names = json.dumps(list(addresses))
    if max_seq is None:
        row = connection.execute(
            "SELECT COUNT(DISTINCT seq) FROM mailbox_address_hits "
            "WHERE seq >= ? AND sender != ? AND address IN (SELECT value FROM json_each(?))",
            (min_seq, identity, names),
        ).fetchone()
    else:
        row = connection.execute(
            "SELECT COUNT(DISTINCT seq) FROM mailbox_address_hits "
            "WHERE seq >= ? AND seq <= ? AND sender != ? "
            "AND address IN (SELECT value FROM json_each(?))",
            (min_seq, max_seq, identity, names),
        ).fetchone()
    return int(row[0])


def _count_glob_only(
    connection: Any,
    identity: str,
    addresses: tuple[str, ...],
    min_seq: int,
    max_seq: int | None,
) -> int:
    if max_seq is None:
        rows = connection.execute(
            "SELECT seq, address FROM mailbox_address_hits "
            "WHERE seq >= ? AND sender != ? AND "
            "(instr(address, '?') > 0 OR instr(address, '[') > 0 OR instr(address, '*') > 0)",
            (min_seq, identity),
        ).fetchall()
    else:
        rows = connection.execute(
            "SELECT seq, address FROM mailbox_address_hits "
            "WHERE seq >= ? AND seq <= ? AND sender != ? AND "
            "(instr(address, '?') > 0 OR instr(address, '[') > 0 OR instr(address, '*') > 0)",
            (min_seq, max_seq, identity),
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
        if number in seen or not reaches(str(address), identity):
            continue
        seen.add(number)
        if not _same_seq_exact(connection, number, addresses):
            extra += 1
    return extra


def _same_seq_exact(connection: Any, seq: int, addresses: tuple[str, ...]) -> bool:
    row = connection.execute(
        "SELECT 1 FROM mailbox_address_hits WHERE seq = ? "
        "AND address IN (SELECT value FROM json_each(?)) LIMIT 1",
        (seq, json.dumps(list(addresses))),
    ).fetchone()
    return row is not None
