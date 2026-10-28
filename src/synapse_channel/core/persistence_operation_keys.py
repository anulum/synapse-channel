# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — bounded retained operation namespace reads
"""Read exact operation key namespaces without decoding retained response payloads."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from synapse_channel.core.persistence import EventStore


def read_operation_keys(
    store: EventStore, prefix: str, *, max_entries: int, max_key_bytes: int
) -> tuple[str, ...]:
    """Read a complete bounded ASCII namespace in binary key order.

    Parameters
    ----------
    store:
        Existing EventStore and its shared connection lock; no connection is opened.
    prefix:
        Nonempty printable ASCII namespace prefix, interpreted literally. Key
        suffixes may contain Unicode; percent and underscore are not wildcards.
    max_entries:
        Positive complete-result row budget, below 2**53. Overflow refuses the
        whole read rather than returning a truncated namespace.
    max_key_bytes:
        Positive per-key budget below 2**53, applied to both SQLite's native
        encoded bytes and the returned UTF-8 bytes. The prefix must also fit.

    Returns
    -------
    tuple of str
        Every matching key in binary order, or an empty tuple. No response,
        event or intent payload is decoded or returned.

    Raises
    ------
    ValueError
        On invalid namespace/budgets or an incomplete or oversized key result.
    Exception
        On an unavailable or damaged journal; no partial result is returned.

    Notes
    -----
    This single SELECT uses the binary primary-key range and reads at most one
    overflow row. It does not certify responses, enforce writer quiescence or
    provide a snapshot spanning subsequent queries. Database size, engine I/O
    and process resources require separate enrollment and bounds.
    """
    if any(
        type(value) is not int or not 0 < value < 2**53 for value in (max_entries, max_key_bytes)
    ):
        raise ValueError("invalid retained operation key read budget")
    if (
        type(prefix) is not str
        or not prefix
        or len(prefix) > max_key_bytes
        or any(not 32 <= ord(character) <= 126 for character in prefix)
    ):
        raise ValueError("invalid retained operation namespace")
    upper = prefix[:-1] + chr(ord(prefix[-1]) + 1)
    storage = store.storage
    with storage.lock:
        rows = storage.connection.execute(
            "SELECT CASE WHEN length(CAST(operation_key AS BLOB)) <= ? "
            "THEN operation_key END FROM operations "
            "WHERE operation_key >= ? COLLATE BINARY AND operation_key < ? COLLATE BINARY "
            "AND substr(operation_key, 1, ?) = ? COLLATE BINARY "
            "ORDER BY operation_key COLLATE BINARY LIMIT ?",
            (max_key_bytes, prefix, upper, len(prefix), prefix, max_entries + 1),
        ).fetchall()
    if len(rows) > max_entries:
        raise ValueError("retained operation namespace exceeds row budget")
    keys: list[str] = []
    for row in rows:
        key = row[0]
        if type(key) is not str or len(key.encode("utf-8")) > max_key_bytes:
            raise ValueError("retained operation key exceeds byte budget")
        keys.append(key)
    return tuple(keys)
