# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — owner-local entitlement event storage
"""Persist private entitlement facts outside the shared hub event stream.

The store keeps immutable, uniquely identified records. A correction is another
record that names the earlier record; callers project only the latest valid
revision. No record is sent to a hub or replicated by this module.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from pathlib import Path
from typing import Final, cast

from synapse_channel.core.entitlements import EntitlementError, active_events, validate_event
from synapse_channel.core.secure_path import (
    SecurePathError,
    apply_owner_only_dir,
    apply_owner_only_file,
    assert_owner_only_dir_path,
    assert_owner_only_file_path,
)

SCHEMA_VERSION: Final = 1
"""SQLite schema version accepted by this implementation."""


def default_entitlement_store() -> Path:
    """Return an owner-local ledger path outside the shared coordination home."""
    state = os.environ.get("XDG_STATE_HOME", "")
    root = Path(state) if state and Path(state).is_absolute() else Path.home() / ".local" / "state"
    return root / "synapse-channel" / "entitlements" / "ledger.sqlite3"


_CREATE_EVENTS = """CREATE TABLE events (
    event_id TEXT PRIMARY KEY,
    recorded_at TEXT NOT NULL,
    payload TEXT NOT NULL
)"""


class EntitlementStoreError(ValueError):
    """Raised when private ledger storage is invalid or unavailable."""


def _prepare_directory(path: Path) -> None:
    """Create or validate the private directory holding one ledger file."""
    parent = path.parent
    if not parent.exists():
        try:
            parent.mkdir(mode=0o700, parents=True)
        except OSError as exc:
            raise EntitlementStoreError("cannot create private entitlement home") from exc
    try:
        assert_owner_only_dir_path(parent, purpose="entitlement home")
    except SecurePathError as exc:
        raise EntitlementStoreError(str(exc)) from exc
    if path.is_symlink():
        raise EntitlementStoreError("entitlement directory must not be a symlink")
    if not path.exists():
        try:
            path.mkdir(mode=0o700)
            apply_owner_only_dir(path)
        except OSError as exc:
            raise EntitlementStoreError("cannot create private entitlement directory") from exc
    try:
        assert_owner_only_dir_path(path, purpose="entitlement directory")
    except SecurePathError as exc:
        raise EntitlementStoreError(str(exc)) from exc


def _open(path: Path) -> sqlite3.Connection:
    """Open a version-checked SQLite file inside an owner-only directory."""
    _prepare_directory(path.parent)
    if path.is_symlink():
        raise EntitlementStoreError("entitlement store must not be a symlink")
    existed = path.exists()
    if existed:
        try:
            assert_owner_only_file_path(path, purpose="entitlement store")
        except SecurePathError as exc:
            raise EntitlementStoreError(str(exc)) from exc
    try:
        connection = sqlite3.connect(path, timeout=5.0)
        if not existed:
            apply_owner_only_file(path)
        connection.execute("PRAGMA foreign_keys=ON")
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version == 0:
            tables = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if existed or tables:
                raise EntitlementStoreError("unversioned entitlement store refused")
            connection.execute(_CREATE_EVENTS)
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            connection.commit()
        elif version != SCHEMA_VERSION:
            raise EntitlementStoreError(
                f"unsupported entitlement store version {version}; expected {SCHEMA_VERSION}"
            )
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise EntitlementStoreError("entitlement store integrity check failed")
        return connection
    except (sqlite3.DatabaseError, OSError, EntitlementStoreError) as exc:
        if "connection" in locals():
            connection.close()
        if isinstance(exc, EntitlementStoreError):
            raise
        raise EntitlementStoreError("cannot open entitlement store") from exc


def append_event(path: Path, event: Mapping[str, object]) -> bool:
    """Append one immutable record, returning false for an exact replay.

    Parameters
    ----------
    path : pathlib.Path
        Owner-local SQLite ledger path under its private directory.
    event : Mapping[str, object]
        Validated domain event with ``event_id`` and ``recorded_at`` fields.

    Returns
    -------
    bool
        True when inserted; false for a byte-equivalent duplicate id.

    Raises
    ------
    EntitlementStoreError
        When the store fails validation or an id is reused for different data.
    """
    try:
        validated = validate_event(event)
    except EntitlementError as exc:
        raise EntitlementStoreError(str(exc)) from exc
    event_id = str(validated["event_id"])
    recorded_at = str(validated["recorded_at"])
    try:
        payload = json.dumps(validated, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise EntitlementStoreError("event is not finite JSON") from exc
    with closing(_open(path)) as connection, connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            prior = [
                json.loads(row[0])
                for row in connection.execute("SELECT payload FROM events ORDER BY rowid")
            ]
            try:
                active_events(prior)
            except (EntitlementError, TypeError, AttributeError, KeyError) as exc:
                raise EntitlementStoreError(f"stored entitlement event is invalid: {exc}") from exc
            existing = connection.execute(
                "SELECT payload FROM events WHERE event_id=?", (event_id,)
            ).fetchone()
            if existing is not None:
                if existing[0] == payload:
                    return False
                raise EntitlementStoreError("event id already exists with different content")
            try:
                active_events([*prior, validated])
            except (EntitlementError, TypeError, AttributeError, KeyError) as exc:
                raise EntitlementStoreError(f"event conflicts with ledger: {exc}") from exc
            connection.execute(
                "INSERT INTO events (event_id, recorded_at, payload) VALUES (?, ?, ?)",
                (event_id, recorded_at, payload),
            )
            connection.commit()
        except (sqlite3.DatabaseError, json.JSONDecodeError) as exc:
            raise EntitlementStoreError("cannot append entitlement event") from exc
    return True


def read_events(path: Path) -> tuple[dict[str, object], ...]:
    """Return immutable records in insertion order after integrity checks.

    The caller must validate each domain event before projecting it. Private
    fields must never be returned to an unauthorised MCP caller.
    """
    if path.is_symlink():
        raise EntitlementStoreError("entitlement store must not be a symlink")
    if not path.exists():
        return ()
    with closing(_open(path)) as connection:
        try:
            rows = connection.execute("SELECT payload FROM events ORDER BY rowid").fetchall()
            decoded = [json.loads(payload) for (payload,) in rows]
        except (sqlite3.DatabaseError, json.JSONDecodeError) as exc:
            raise EntitlementStoreError("entitlement records are corrupt") from exc
    if not all(isinstance(item, dict) for item in decoded):
        raise EntitlementStoreError("entitlement record must be a JSON object")
    return tuple(cast("dict[str, object]", item) for item in decoded)
