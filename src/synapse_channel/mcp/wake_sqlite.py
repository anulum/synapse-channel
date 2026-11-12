# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — verified bounded SQLite wake profile
"""Apply and verify the same bounded durability profile for native and root storage."""

from __future__ import annotations

import sqlite3

from synapse_channel.mcp.wake_limits import MAX_WAKE_DATABASE_BYTES
from synapse_channel.mcp.wake_pointer import WakeLedgerError

_PROFILE: tuple[tuple[str, str, int | str], ...] = (
    ("page_size", "4096", 4096),
    ("max_page_count", str(MAX_WAKE_DATABASE_BYTES // 4096), MAX_WAKE_DATABASE_BYTES // 4096),
    ("journal_mode", "DELETE", "delete"),
    ("synchronous", "FULL", 2),
    ("trusted_schema", "OFF", 0),
    ("temp_store", "MEMORY", 2),
    ("cache_size", "-8192", -8192),
    ("cache_spill", "OFF", 0),
)


def configure_wake_sqlite(connection: sqlite3.Connection) -> None:
    """Require each bound-critical setting to take effect before opening a transaction.

    SQLite can ignore unsupported PRAGMAs, and a requested maximum page count
    cannot reduce an already larger database. Reading every setting back prevents
    either case from becoming a false claim about the admitted storage profile.
    The SQL names and values come exclusively from this fixed internal profile.
    """
    for name, literal, expected in _PROFILE:
        connection.execute("PRAGMA " + name + "=" + literal)
        row = connection.execute("PRAGMA " + name).fetchone()
        if row is None or row[0] != expected:
            if name == "page_size":
                raise WakeLedgerError("unsupported wake SQLite page size")
            raise WakeLedgerError("required wake SQLite profile unavailable")
