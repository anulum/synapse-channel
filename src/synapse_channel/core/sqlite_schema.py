# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — SQLite schema text and digest
"""Define the SQLite schema text and its digest once, for every migration check.

A migration declares the digest of the schema it starts from and of the schema
it produces, and the migration verifier compares an observed digest with a
declared one. Both sides read the digest through this module, so a declaration
and its verification can never use two definitions.
"""

from __future__ import annotations

import hashlib
import sqlite3


def schema_text(connection: sqlite3.Connection) -> str:
    """Return the exact text whose SHA-256 is the schema digest.

    Each ``sqlite_master`` row with SQL, excluding internal ``sqlite_`` names, is
    one block of type, a tab, name, a newline, the stored SQL and a newline,
    ordered by type then name. The SQL text is copied as SQLite stored it, so
    different whitespace is a different digest.

    Parameters
    ----------
    connection : sqlite3.Connection
        An open connection; only ``sqlite_master`` is read.

    Returns
    -------
    str
        The schema text.
    """
    rows = connection.execute(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()
    return "".join(f"{kind}\t{name}\n{sql}\n" for kind, name, sql in rows)


def schema_digest(connection: sqlite3.Connection) -> str:
    """Return the lower-case hex SHA-256 of :func:`schema_text`.

    Parameters
    ----------
    connection : sqlite3.Connection
        An open connection; only ``sqlite_master`` is read.

    Returns
    -------
    str
        Sixty-four lower-case hexadecimal digits.
    """
    return hashlib.sha256(schema_text(connection).encode("utf-8")).hexdigest()
