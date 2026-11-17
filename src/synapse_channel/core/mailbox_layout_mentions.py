# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the mailbox names each chat mentions, kept beside the journal
"""Keep, per chat, the mailbox names its target mentions, so a restart need not read chats.

A restarted tracker remembers mailboxes in the order the journal last mentioned
them, within its bound. Each chat's mentioned names are one row per name, keyed
by the chat's sequence and the name's position in the target, so the tracker
replays them in journal order without reading a chat body. Every chat writes its
rows, a channel chat and an audience target too, because the tracker remembers
their names as well. A deleted chat's rows go with it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from synapse_channel.core.mailbox_addresses import mention_names

_CHUNK = 400
_TABLE_EXISTS = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?"


def record_mentions(connection: Any, seq: int, payload: dict[str, Any]) -> None:
    """Store the mailbox names one chat's target mentions, in the caller's transaction.

    Parameters
    ----------
    connection : Any
        The event store's own connection.
    seq : int
        The chat's journal sequence.
    payload : dict[str, Any]
        The chat payload.
    """
    names = mention_names(str(payload.get("target") or "all"))
    connection.executemany(
        "INSERT OR IGNORE INTO mailbox_name_mentions (seq, position, name) VALUES (?, ?, ?)",
        [(seq, position, name) for position, name in enumerate(names)],
    )


def delete_mentions(connection: Any, seqs: list[int]) -> None:
    """Drop the mentions of chats whose event rows were removed.

    Parameters
    ----------
    connection : Any
        The event store's own connection, inside the deletion's transaction.
    seqs : list[int]
        Sequence numbers whose event rows were removed.
    """
    for start in range(0, len(seqs), _CHUNK):
        connection.execute(
            "DELETE FROM mailbox_name_mentions WHERE seq IN (SELECT value FROM json_each(?))",
            (json.dumps(seqs[start : start + _CHUNK]),),
        )


def mention_history(connection: Any) -> Iterator[tuple[int, tuple[str, ...]]]:
    """Yield each chat's mentioned names, in journal order.

    Parameters
    ----------
    connection : Any
        The event store's own connection.

    Yields
    ------
    tuple[int, tuple[str, ...]]
        ``(seq, names)`` for each chat that mentions a name, names in target order.
    """
    current: int | None = None
    names: list[str] = []
    rows = connection.execute("SELECT seq, name FROM mailbox_name_mentions ORDER BY seq, position")
    for seq, name in rows:
        if current is not None and int(seq) != current:
            yield current, tuple(names)
            names = []
        current = int(seq)
        names.append(str(name))
    if current is not None:
        yield current, tuple(names)


def copy_mentions(source: Any, destination: Any) -> bool:
    """Make ``destination``'s mentions a copy of ``source``'s; say whether it keeps any.

    Rows ``destination`` already holds are dropped first, so the copy is exactly
    the source's. A source without the table leaves ``destination`` with none.

    Parameters
    ----------
    source : Any
        The plaintext store connection.
    destination : Any
        The new store connection, which already has the table. The caller commits.

    Returns
    -------
    bool
        ``False`` when ``source`` has no mention table, so nothing was copied.
    """
    destination.execute("DELETE FROM mailbox_name_mentions")
    if source.execute(_TABLE_EXISTS, ("mailbox_name_mentions",)).fetchone() is None:
        return False
    destination.executemany(
        "INSERT INTO mailbox_name_mentions (seq, position, name) VALUES (?, ?, ?)",
        list(source.execute("SELECT seq, position, name FROM mailbox_name_mentions ORDER BY seq")),
    )
    return True
