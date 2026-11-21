# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — declared migrations of Core's own SQLite stores
"""Apply a declared migration from exactly its base schema, in one immediate transaction.

A declared migration names its identity, the digest of the schema it starts
from, its statements, the digest of the schema it produces and the layout
version it sets. Applying it checks the base digest, records an ``applied``
marker for the attempt and the migration's identity, runs the statements,
checks the produced digest and sets the version, all inside one ``BEGIN
IMMEDIATE`` transaction. Any failure rolls the whole transaction back, so a
store is either untouched or completely migrated.

The identity goes to ``schema_migrations(version)`` and the digests use
:func:`synapse_channel.core.sqlite_schema.schema_digest`, which is what
``synapse verify-result migration`` reads. A migration recorded by a
concurrent opener is reported as already applied, and the store is left as
that opener left it.
"""

from __future__ import annotations

import contextlib
import re
import sqlite3
from dataclasses import dataclass
from typing import Literal

from synapse_channel.core.sqlite_schema import schema_digest

MIGRATIONS_TABLE = "schema_migrations"
"""Table of applied migration identities, as the migration verifier reads it by default."""

ATTEMPTS_TABLE = "store_migration_attempts"
"""Table of attempt markers; each applied attempt holds one ``applied`` row.

The marker's second kind, ``sealed``, is the effect contract's stop primitive.
Core's own stores migrate when they open and have no stopper yet, so nothing
writes it today; the layout admits it so a later seal needs no migration.
"""

_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_HEX64 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class DeclaredMigration:
    """One migration of a store, declared by the schema it starts from and produces.

    Parameters
    ----------
    identity : str
        Plain token recorded in ``schema_migrations``, such as ``app-tasks-2``.
    base_schema_sha256 : str
        Schema digest of the layout the migration starts from.
    statements : tuple of str
        Statements applied in order inside the migration's transaction.
    schema_sha256 : str
        Schema digest of the layout it produces, bookkeeping tables included.
    user_version : int
        Layout version set by the migration.

    Raises
    ------
    ValueError
        When the identity is not a plain token, a digest is not 64 lower-case
        hexadecimal digits, there is no statement, or the version is not positive.
    """

    identity: str
    base_schema_sha256: str
    statements: tuple[str, ...]
    schema_sha256: str
    user_version: int

    def __post_init__(self) -> None:
        """Refuse a declaration the verifier could not check."""
        if not _IDENTITY.fullmatch(self.identity):
            raise ValueError("a migration identity is a plain token")
        if not (_HEX64.fullmatch(self.base_schema_sha256) and _HEX64.fullmatch(self.schema_sha256)):
            raise ValueError("a migration's schema digests are 64 lower-case hex digits")
        if not self.statements:
            raise ValueError("a migration has at least one statement")
        if self.user_version < 1:
            raise ValueError("a migration sets a positive layout version")


class MigrationRefused(ValueError):
    """A declared migration did not apply, and the store was left unchanged.

    Parameters
    ----------
    identity : str
        The migration's identity.
    reason : str
        ``base_schema_differs`` or ``produced_schema_differs``.
    observed_schema_sha256 : str
        The schema digest that did not match the declaration.
    """

    def __init__(self, identity: str, reason: str, observed_schema_sha256: str) -> None:
        self.identity = identity
        self.reason = reason
        self.observed_schema_sha256 = observed_schema_sha256
        super().__init__(
            f"migration {identity} refused ({reason}): observed schema "
            f"{observed_schema_sha256}; the store is unchanged"
        )


def _recorded(connection: sqlite3.Connection, identity: str) -> bool:
    """Return whether ``identity`` is already in the migrations table."""
    present = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (MIGRATIONS_TABLE,)
    ).fetchone()
    if present is None:
        return False
    found = connection.execute(
        f"SELECT 1 FROM {MIGRATIONS_TABLE} WHERE version = ? LIMIT 1", (identity,)
    ).fetchone()
    return found is not None


def apply_declared_migration(
    connection: sqlite3.Connection, migration: DeclaredMigration, *, attempt: str
) -> Literal["applied", "already_applied"]:
    """Apply ``migration`` from exactly its base schema, or leave the store unchanged.

    The connection must not be inside a transaction. The whole change is one
    ``BEGIN IMMEDIATE`` transaction: a second opener waits for the first and
    then finds the identity recorded.

    Parameters
    ----------
    connection : sqlite3.Connection
        The store owner's connection, outside any transaction.
    migration : DeclaredMigration
        The migration to apply.
    attempt : str
        Identity of this attempt, recorded with an ``applied`` marker.

    Returns
    -------
    str
        ``applied`` when this call migrated the store, ``already_applied`` when
        the identity was already recorded.

    Raises
    ------
    MigrationRefused
        When the store's schema is not the declared base, or the statements did
        not produce the declared schema.
    sqlite3.Error
        When a statement fails; the transaction is rolled back first.
    """
    connection.execute("BEGIN IMMEDIATE")
    try:
        if _recorded(connection, migration.identity):
            connection.execute("ROLLBACK")
            return "already_applied"
        observed = schema_digest(connection)
        if observed != migration.base_schema_sha256:
            raise MigrationRefused(migration.identity, "base_schema_differs", observed)
        # A later migration's base may already hold both bookkeeping tables.
        connection.execute(
            f"CREATE TABLE IF NOT EXISTS {MIGRATIONS_TABLE} (version TEXT PRIMARY KEY)"
        )
        connection.execute(
            f"CREATE TABLE IF NOT EXISTS {ATTEMPTS_TABLE} (attempt TEXT PRIMARY KEY, "
            "migration TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('applied', 'sealed')))"
        )
        connection.execute(
            f"INSERT INTO {ATTEMPTS_TABLE} (attempt, migration, kind) VALUES (?, ?, 'applied')",
            (attempt, migration.identity),
        )
        for statement in migration.statements:
            connection.execute(statement)
        connection.execute(
            f"INSERT INTO {MIGRATIONS_TABLE} (version) VALUES (?)", (migration.identity,)
        )
        produced = schema_digest(connection)
        if produced != migration.schema_sha256:
            raise MigrationRefused(migration.identity, "produced_schema_differs", produced)
        connection.execute(f"PRAGMA user_version = {int(migration.user_version)}")
        connection.execute("COMMIT")
    except BaseException:
        # SQLite may already have rolled back after an I/O error; the original
        # exception is the one to report.
        with contextlib.suppress(sqlite3.OperationalError):
            connection.execute("ROLLBACK")
        raise
    return "applied"
