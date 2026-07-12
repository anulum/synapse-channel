# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable signed capability-card lifecycle history
"""Persist signed-card replay and downgrade state in a bounded SQLite store."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import stat
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.core.capability_card_trust import (
    CapabilityCardHistory,
    CapabilityCardHistoryEntry,
    CapabilityCardHistoryResult,
    CapabilityCardTrustError,
)

CAPABILITY_CARD_HISTORY_SCHEMA_VERSION = 1
"""Current on-disk signed-card history schema."""

CAPABILITY_CARD_HISTORY_FILE_MODE = 0o600
"""Required POSIX mode for the history database."""

MAX_CAPABILITY_CARD_HISTORY_FIELD_BYTES = 512
"""Maximum encoded size of an agent, key id, or digest field."""

MAX_CAPABILITY_CARD_HISTORY_CAPABILITIES_BYTES = 2 * 1024 * 1024
"""Maximum canonical route-capability payload retained for one binding."""

_BUSY_TIMEOUT_MILLISECONDS = 250
_TABLE = "capability_card_history"
_LOGGER = logging.getLogger(__name__)
_EXPECTED_COLUMNS = (
    ("agent", "TEXT", 1, 1),
    ("key_id", "TEXT", 1, 2),
    ("sequence", "INTEGER", 1, 0),
    ("route_capabilities", "TEXT", 1, 0),
    ("card_digest", "TEXT", 1, 0),
    ("expires_at", "REAL", 1, 0),
    ("observed_at", "REAL", 1, 0),
)


@dataclass(frozen=True)
class _HistoryTransition:
    """Validated values for one persistent lifecycle transition."""

    agent: str
    key_id: str
    sequence: int
    route_capabilities: frozenset[str]
    card_digest: str
    expires_at: float
    now: float


class PersistentCapabilityCardHistory(CapabilityCardHistory):
    """SQLite-backed replay and downgrade history shared across hub restarts.

    The database is a separate operator-controlled file. SQLite serialises
    concurrent writers with ``BEGIN IMMEDIATE``; a lock, I/O, schema, or row
    validation failure returns ``history_unavailable`` through the normal
    advisory verification result instead of reporting a card as valid.

    Parameters
    ----------
    path : str or pathlib.Path
        Dedicated history database. A new file is created owner-only; an
        existing file must be a regular non-symlink file owned by the current
        POSIX user with mode ``0600``.
    max_entries : int, optional
        Maximum retained agent/key bindings.
    retention_seconds : float, optional
        Time after card expiry for which its replay floor remains retained.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        max_entries: int,
        retention_seconds: float,
    ) -> None:
        super().__init__(max_entries=max_entries, retention_seconds=retention_seconds)
        self.path = Path(path).expanduser()
        created = _prepare_history_file(self.path)
        try:
            self._initialize(created=created)
        except BaseException:
            if created:
                _remove_new_database(self.path)
            raise

    def assess_and_remember(
        self,
        *,
        agent: str,
        key_id: str,
        sequence: int,
        route_capabilities: frozenset[str],
        card_digest: str,
        expires_at: float,
        now: float,
    ) -> CapabilityCardHistoryResult:
        """Atomically apply lifecycle policy and persist the resulting floor."""
        try:
            values = _validated_input(
                agent=agent,
                key_id=key_id,
                sequence=sequence,
                route_capabilities=route_capabilities,
                card_digest=card_digest,
                expires_at=expires_at,
                now=now,
            )
            with closing(_connect(self.path)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    result = self._assess(
                        connection,
                        agent=values.agent,
                        key_id=values.key_id,
                        sequence=values.sequence,
                        route_capabilities=values.route_capabilities,
                        card_digest=values.card_digest,
                        expires_at=values.expires_at,
                        now=values.now,
                    )
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
            return result
        except (CapabilityCardTrustError, OSError, sqlite3.Error) as exc:
            _LOGGER.warning(
                "capability-card history unavailable at %s: %s",
                self.path,
                exc,
            )
            return CapabilityCardHistoryResult.HISTORY_UNAVAILABLE

    def _initialize(self, *, created: bool) -> None:
        """Create or validate the schema and every retained lifecycle row."""
        try:
            with closing(_connect(self.path)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    _ensure_schema(connection, created=created)
                    _validate_rows(connection, max_entries=self.max_entries)
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
        except CapabilityCardTrustError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise CapabilityCardTrustError(
                f"cannot open capability-card history database {self.path}: {exc}"
            ) from exc

    def _assess(
        self,
        connection: sqlite3.Connection,
        *,
        agent: str,
        key_id: str,
        sequence: int,
        route_capabilities: frozenset[str],
        card_digest: str,
        expires_at: float,
        now: float,
    ) -> CapabilityCardHistoryResult:
        """Apply one lifecycle transition inside an immediate transaction."""
        connection.execute(
            "DELETE FROM capability_card_history WHERE expires_at + ? < ?",
            (self.retention_seconds, now),
        )
        row = connection.execute(
            "SELECT sequence, route_capabilities, card_digest, expires_at, observed_at "
            "FROM capability_card_history WHERE agent = ? AND key_id = ?",
            (agent, key_id),
        ).fetchone()
        previous = _entry_from_row(row, binding=(agent, key_id)) if row is not None else None
        if previous is not None and sequence <= previous.sequence:
            return CapabilityCardHistoryResult.SEQUENCE_MISMATCH
        if previous is None:
            count = int(
                connection.execute("SELECT COUNT(*) FROM capability_card_history").fetchone()[0]
            )
            if count >= self.max_entries:
                return CapabilityCardHistoryResult.HISTORY_FULL

        result = CapabilityCardHistoryResult.ACCEPTED
        remembered_capabilities = route_capabilities
        remembered_expiry = expires_at
        if previous is not None and not previous.route_capabilities.issubset(route_capabilities):
            result = CapabilityCardHistoryResult.CAPABILITY_DOWNGRADE
            remembered_capabilities = previous.route_capabilities
            remembered_expiry = max(previous.expires_at, expires_at)
        encoded_capabilities = _encode_capabilities(remembered_capabilities)
        connection.execute(
            """INSERT INTO capability_card_history (
                    agent, key_id, sequence, route_capabilities,
                    card_digest, expires_at, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent, key_id) DO UPDATE SET
                    sequence = excluded.sequence,
                    route_capabilities = excluded.route_capabilities,
                    card_digest = excluded.card_digest,
                    expires_at = excluded.expires_at,
                    observed_at = excluded.observed_at""",
            (
                agent,
                key_id,
                sequence,
                encoded_capabilities,
                card_digest,
                remembered_expiry,
                now,
            ),
        )
        return result


def _validated_input(
    *,
    agent: str,
    key_id: str,
    sequence: int,
    route_capabilities: frozenset[str],
    card_digest: str,
    expires_at: float,
    now: float,
) -> _HistoryTransition:
    """Validate values before they reach SQLite bindings."""
    normalized_agent = _bounded_text(agent, "agent")
    normalized_key = _bounded_text(key_id, "key id")
    normalized_digest = _bounded_text(card_digest, "card digest")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise CapabilityCardTrustError("capability-card history sequence must be positive")
    if not isinstance(route_capabilities, frozenset) or any(
        not isinstance(value, str) or not value for value in route_capabilities
    ):
        raise CapabilityCardTrustError(
            "capability-card history route capabilities must be non-empty strings"
        )
    expiry = float(expires_at)
    observed = float(now)
    if not _finite(expiry) or not _finite(observed):
        raise CapabilityCardTrustError("capability-card history timestamps must be finite")
    _encode_capabilities(route_capabilities)
    return _HistoryTransition(
        agent=normalized_agent,
        key_id=normalized_key,
        sequence=sequence,
        route_capabilities=route_capabilities,
        card_digest=normalized_digest,
        expires_at=expiry,
        now=observed,
    )


def _bounded_text(value: str, label: str) -> str:
    """Return one non-empty bounded UTF-8 field."""
    if not isinstance(value, str) or not value:
        raise CapabilityCardTrustError(f"capability-card history {label} must be non-empty")
    if len(value.encode("utf-8")) > MAX_CAPABILITY_CARD_HISTORY_FIELD_BYTES:
        raise CapabilityCardTrustError(f"capability-card history {label} is too large")
    return value


def _encode_capabilities(values: frozenset[str]) -> str:
    """Return a bounded canonical JSON capability set."""
    encoded = json.dumps(sorted(values), ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_CAPABILITY_CARD_HISTORY_CAPABILITIES_BYTES:
        raise CapabilityCardTrustError("capability-card history capability floor is too large")
    return encoded


def _decode_capabilities(value: object, *, binding: tuple[str, str]) -> frozenset[str]:
    """Parse and validate one stored canonical capability set."""
    if not isinstance(value, str):
        raise CapabilityCardTrustError(f"history row {binding!r} has non-text capabilities")
    if len(value.encode("utf-8")) > MAX_CAPABILITY_CARD_HISTORY_CAPABILITIES_BYTES:
        raise CapabilityCardTrustError(f"history row {binding!r} capability floor is too large")
    try:
        parsed: object = json.loads(value)
    except json.JSONDecodeError as exc:
        raise CapabilityCardTrustError(
            f"history row {binding!r} has invalid capability JSON"
        ) from exc
    if not isinstance(parsed, list) or any(
        not isinstance(item, str) or not item for item in parsed
    ):
        raise CapabilityCardTrustError(
            f"history row {binding!r} capabilities must be non-empty strings"
        )
    if parsed != sorted(set(parsed)):
        raise CapabilityCardTrustError(
            f"history row {binding!r} capabilities are not canonical and unique"
        )
    return frozenset(parsed)


def _entry_from_row(
    row: tuple[object, ...], *, binding: tuple[str, str]
) -> CapabilityCardHistoryEntry:
    """Validate and return one stored history entry."""
    sequence, capabilities, digest, expires_at, observed_at = row
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise CapabilityCardTrustError(f"history row {binding!r} has invalid sequence")
    normalized_digest = _bounded_text(digest, "card digest") if isinstance(digest, str) else ""
    if not normalized_digest:
        raise CapabilityCardTrustError(f"history row {binding!r} has invalid card digest")
    if isinstance(expires_at, bool) or not isinstance(expires_at, int | float):
        raise CapabilityCardTrustError(f"history row {binding!r} has invalid expiry")
    if isinstance(observed_at, bool) or not isinstance(observed_at, int | float):
        raise CapabilityCardTrustError(f"history row {binding!r} has invalid observation time")
    expiry = float(expires_at)
    observed = float(observed_at)
    if not _finite(expiry) or not _finite(observed):
        raise CapabilityCardTrustError(f"history row {binding!r} has non-finite timestamps")
    return CapabilityCardHistoryEntry(
        sequence=sequence,
        route_capabilities=_decode_capabilities(capabilities, binding=binding),
        card_digest=normalized_digest,
        expires_at=expiry,
        observed_at=observed,
    )


def _prepare_history_file(path: Path) -> bool:
    """Create a fresh owner-only file or validate an existing state file."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                CAPABILITY_CARD_HISTORY_FILE_MODE,
            )
        except OSError as exc:
            raise CapabilityCardTrustError(
                f"cannot create capability-card history database {path}: {exc}"
            ) from exc
        os.close(descriptor)
        return True
    except OSError as exc:
        raise CapabilityCardTrustError(
            f"cannot inspect capability-card history database {path}: {exc}"
        ) from exc
    _validate_history_file(path, info)
    return False


def _validate_history_file(path: Path, info: os.stat_result | None = None) -> None:
    """Require a regular, non-symlink, owner-controlled writable database."""
    snapshot = path.lstat() if info is None else info
    if not stat.S_ISREG(snapshot.st_mode):
        raise CapabilityCardTrustError(
            f"capability-card history database must be a regular non-symlink file: {path}"
        )
    if os.name == "posix":
        if snapshot.st_uid != os.geteuid():
            raise CapabilityCardTrustError(
                f"capability-card history database must be owned by the current user: {path}"
            )
        if stat.S_IMODE(snapshot.st_mode) != CAPABILITY_CARD_HISTORY_FILE_MODE:
            raise CapabilityCardTrustError(
                f"capability-card history database must be owner-only (chmod 600): {path}"
            )


def _connect(path: Path) -> sqlite3.Connection:
    """Open a validated history database with bounded lock waiting."""
    _validate_history_file(path)
    connection = sqlite3.connect(
        str(path),
        timeout=_BUSY_TIMEOUT_MILLISECONDS / 1000.0,
        isolation_level=None,
    )
    try:
        connection.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MILLISECONDS}")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        journal_mode = str(connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0]).lower()
        if journal_mode != "delete":
            raise CapabilityCardTrustError(
                f"capability-card history database refused DELETE journalling: {path}"
            )
        connection.execute("PRAGMA synchronous=FULL")
    except BaseException:
        connection.close()
        raise
    return connection


def _ensure_schema(connection: sqlite3.Connection, *, created: bool) -> None:
    """Create schema version one or reject an ambiguous/unknown database."""
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (_TABLE,),
    ).fetchone()
    if created:
        if version != 0 or exists is not None:
            raise CapabilityCardTrustError("new capability-card history database is not empty")
        connection.execute(
            """CREATE TABLE capability_card_history (
                agent TEXT NOT NULL,
                key_id TEXT NOT NULL,
                sequence INTEGER NOT NULL CHECK(sequence > 0),
                route_capabilities TEXT NOT NULL,
                card_digest TEXT NOT NULL,
                expires_at REAL NOT NULL,
                observed_at REAL NOT NULL,
                PRIMARY KEY(agent, key_id)
            )"""
        )
        connection.execute("PRAGMA user_version=1")
    elif version != CAPABILITY_CARD_HISTORY_SCHEMA_VERSION or exists is None:
        raise CapabilityCardTrustError(
            "capability-card history database has an unknown or missing schema"
        )
    columns = tuple(
        (str(row[1]), str(row[2]).upper(), int(row[3]), int(row[5]))
        for row in connection.execute("PRAGMA table_info(capability_card_history)")
    )
    if columns != _EXPECTED_COLUMNS:
        raise CapabilityCardTrustError("capability-card history database schema does not match v1")
    integrity = connection.execute("PRAGMA quick_check").fetchone()
    if integrity is None or integrity[0] != "ok":
        raise CapabilityCardTrustError("capability-card history database failed quick_check")


def _validate_rows(connection: sqlite3.Connection, *, max_entries: int) -> None:
    """Reject corrupt, oversized, or non-canonical retained rows at startup."""
    rows = connection.execute(
        "SELECT agent, key_id, sequence, route_capabilities, card_digest, "
        "expires_at, observed_at FROM capability_card_history ORDER BY agent, key_id"
    ).fetchall()
    if len(rows) > max_entries:
        raise CapabilityCardTrustError(
            "capability-card history database exceeds the configured binding capacity"
        )
    for row in rows:
        agent = _bounded_text(row[0], "agent") if isinstance(row[0], str) else ""
        key_id = _bounded_text(row[1], "key id") if isinstance(row[1], str) else ""
        if not agent or not key_id:
            raise CapabilityCardTrustError("capability-card history row has an invalid binding")
        _entry_from_row(tuple(row[2:]), binding=(agent, key_id))


def _remove_new_database(path: Path) -> None:
    """Remove only files created by a failed first-time initialisation."""
    for candidate in (path, path.with_name(path.name + "-journal")):
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            _LOGGER.warning("cannot remove failed capability-card history file %s", candidate)


def _finite(value: float) -> bool:
    """Return whether ``value`` is neither NaN nor infinity."""
    return value == value and value not in (float("inf"), float("-inf"))
