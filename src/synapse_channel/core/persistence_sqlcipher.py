# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — SQLCipher open path for the live event store
"""Optional SQLCipher driver for the live hub event store.

The stock :class:`~synapse_channel.core.persistence.EventStore` uses the
standard library :mod:`sqlite3`. When an operator supplies a key file, this
module opens the same schema through SQLCipher so every page (main DB, WAL,
indexes) is encrypted at rest.

SQLCipher is an opt-in native dependency::

    pip install synapse-channel[sqlcipher]
    # or: pip install sqlcipher3-binary

The stock install stays dependency-free. Opening a keyed store without the
driver raises a clear install hint. This path does **not** protect a running
hub's in-memory state and does **not** replace host filesystem permissions.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from synapse_channel.core.at_rest import KEY_BYTES, load_key_file

SQLCIPHER_INSTALL_HINT = (
    "SQLCipher support requires the optional sqlcipher extra: "
    "pip install 'synapse-channel[sqlcipher]' (or sqlcipher3-binary)"
)


class SqlCipherUnavailableError(RuntimeError):
    """Raised when a keyed store is requested but SQLCipher is not installed."""


class SqlCipherKeyError(ValueError):
    """Raised when the provided key cannot open an encrypted store."""


def sqlcipher_available() -> bool:
    """Return whether a SQLCipher DB-API driver can be imported."""
    try:
        import_sqlcipher_module()
    except SqlCipherUnavailableError:
        return False
    return True


def import_sqlcipher_module() -> Any:
    """Import and return the SQLCipher ``dbapi2`` module.

    Tries ``sqlcipher3.dbapi2`` (sqlcipher3 / sqlcipher3-binary) first, then
    ``pysqlcipher3.dbapi2``.

    Returns
    -------
    module
        A DB-API 2.0 module with a ``connect`` callable compatible with
        :mod:`sqlite3`.

    Raises
    ------
    SqlCipherUnavailableError
        When no supported SQLCipher package is installed.
    """
    try:
        from sqlcipher3 import dbapi2 as sqlcipher
    except ImportError:
        try:
            from pysqlcipher3 import dbapi2 as sqlcipher  # type: ignore[no-redef]
        except ImportError as exc:
            raise SqlCipherUnavailableError(SQLCIPHER_INSTALL_HINT) from exc
    return sqlcipher


def pragma_key_literal(key: bytes) -> str:
    """Return a SQLCipher ``PRAGMA key`` raw-key literal for a 32-byte key.

    Parameters
    ----------
    key : bytes
        Exactly :data:`~synapse_channel.core.at_rest.KEY_BYTES` random key bytes.

    Returns
    -------
    str
        ``x'<hex>'`` form accepted by SQLCipher for raw binary keys.
    """
    if len(key) != KEY_BYTES:
        raise ValueError(f"SQLCipher key must be {KEY_BYTES} bytes, got {len(key)}")
    return f"x'{key.hex()}'"


def apply_sqlcipher_key(conn: Any, key: bytes) -> None:
    """Issue ``PRAGMA key`` and verify the connection can read the schema.

    Parameters
    ----------
    conn :
        An open SQLCipher connection.
    key : bytes
        Raw 32-byte key material.

    Raises
    ------
    SqlCipherKeyError
        When the key is rejected (wrong key or corrupted store).
    """
    literal = pragma_key_literal(key)
    # SQLCipher accepts the x'hex' form only as a SQL literal, not a bound param.
    conn.execute(f"PRAGMA key = \"{literal}\"")
    try:
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except Exception as exc:  # noqa: BLE001 — driver raises DatabaseError variants
        raise SqlCipherKeyError(
            "SQLCipher key rejected (wrong key or not an encrypted store)"
        ) from exc


def connect_sqlcipher(path: str | Path, key: bytes) -> Any:
    """Open a SQLCipher database at ``path`` with the given raw key.

    Parameters
    ----------
    path : str or pathlib.Path
        Database file path (``:memory:`` accepted for tests).
    key : bytes
        Raw 32-byte key material.

    Returns
    -------
    connection
        An open SQLCipher connection with the key applied and verified.

    Raises
    ------
    SqlCipherUnavailableError
        When the SQLCipher driver is not installed.
    SqlCipherKeyError
        When the key cannot open the database.
    """
    sqlcipher = import_sqlcipher_module()
    conn = sqlcipher.connect(str(path))
    try:
        apply_sqlcipher_key(conn, key)
    except Exception:
        conn.close()
        raise
    return conn


def connect_event_store(
    path: str | Path,
    *,
    key: bytes | None = None,
    key_file: str | Path | None = None,
) -> tuple[Any, bool]:
    """Open a stock or SQLCipher connection for the event store.

    Parameters
    ----------
    path : str or pathlib.Path
        Database file path.
    key : bytes or None, optional
        Raw key bytes. Mutually exclusive with ``key_file`` when both would
        disagree; when both are set, ``key`` wins.
    key_file : str or pathlib.Path or None, optional
        Owner-only 32-byte key file (checked via :func:`load_key_file`).

    Returns
    -------
    tuple
        ``(connection, encrypted)`` where ``encrypted`` is ``True`` when the
        SQLCipher path was used.

    Raises
    ------
    ValueError
        When ``key_file`` fails validation.
    SqlCipherUnavailableError
        When a key is supplied but SQLCipher is not installed.
    SqlCipherKeyError
        When the key cannot open an encrypted store.
    """
    material = key
    if material is None and key_file is not None:
        material = load_key_file(key_file)
    if material is None:
        return sqlite3.connect(str(path)), False
    return connect_sqlcipher(path, material), True


def migrate_plaintext_to_sqlcipher(
    source: str | Path,
    destination: str | Path,
    *,
    key: bytes | None = None,
    key_file: str | Path | None = None,
) -> Mapping[str, int]:
    """Offline-copy a plaintext event store into a new SQLCipher database.

    The hub (and any other writer) must be stopped. ``destination`` must not
    already exist. Rows are copied with their original ``seq`` / ``ts`` / ``kind``
    / ``payload`` so resume cursors stay valid. The destination is restricted to
    owner-only mode when the platform supports it.

    Parameters
    ----------
    source : str or pathlib.Path
        Existing plaintext SQLite event-store path.
    destination : str or pathlib.Path
        New encrypted database path (must not exist).
    key : bytes or None, optional
        Raw key bytes for the destination.
    key_file : str or pathlib.Path or None, optional
        Key file for the destination when ``key`` is omitted.

    Returns
    -------
    Mapping[str, int]
        ``{"rows": N}`` for the number of events copied.

    Raises
    ------
    FileNotFoundError
        When ``source`` is missing.
    FileExistsError
        When ``destination`` already exists.
    ValueError
        When neither ``key`` nor ``key_file`` is supplied.
    SqlCipherUnavailableError
        When SQLCipher is not installed.
    """
    src = Path(source)
    dst = Path(destination)
    if not src.is_file():
        raise FileNotFoundError(f"plaintext event store not found: {src}")
    if dst.exists():
        raise FileExistsError(f"encrypted destination already exists: {dst}")
    material = key if key is not None else None
    if material is None:
        if key_file is None:
            raise ValueError("migrate_plaintext_to_sqlcipher requires key or key_file")
        material = load_key_file(key_file)

    plain = sqlite3.connect(str(src))
    try:
        rows = list(
            plain.execute("SELECT seq, ts, kind, payload FROM events ORDER BY seq")
        )
    finally:
        plain.close()

    enc = connect_sqlcipher(dst, material)
    try:
        enc.execute(
            "CREATE TABLE IF NOT EXISTS events ("
            "seq INTEGER PRIMARY KEY, "
            "ts REAL NOT NULL, "
            "kind TEXT NOT NULL, "
            "payload TEXT NOT NULL)"
        )
        enc.executemany(
            "INSERT INTO events (seq, ts, kind, payload) VALUES (?, ?, ?, ?)",
            rows,
        )
        enc.commit()
        enc.execute("PRAGMA journal_mode=WAL")
        enc.commit()
    finally:
        enc.close()
    _restrict_owner(dst)
    _restrict_owner(Path(f"{dst}-wal"))
    _restrict_owner(Path(f"{dst}-shm"))
    return {"rows": len(rows)}


def _restrict_owner(path: Path) -> None:
    """Best-effort owner-only chmod for an on-disk path."""
    if not path.exists() or str(path).startswith(":memory:"):
        return
    try:
        os.chmod(path, 0o600)
    except OSError:
        return
