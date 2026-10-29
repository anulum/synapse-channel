# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — deployment and database-migration result checks
"""Read a health report and a database schema. Never deploy or migrate.

:func:`verify_deployment` checks what a JSON report says. That is independent
evidence only when the deployed service, not the deploying worker, wrote the
report. :func:`verify_migration` opens a SQLite database read-only, looks for
one migration identity, and compares a defined digest of ``sqlite_master``.
Neither function writes, retries, or repairs.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
from pathlib import Path

from synapse_channel.result_verification import ResultVerification

_HEX64 = re.compile(r"[0-9a-f]{64}")
_REVISION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}")
_MIGRATION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
_REPORT_MAX = 65536
_NOFOLLOW: int | None = getattr(os, "O_NOFOLLOW", None)
_HEALTHY = "ok"


def _record(
    operation_id: str,
    verifier: str,
    communication: str,
    effect: str,
    result: str,
    observation: str,
    evidence: dict[str, str],
) -> ResultVerification:
    """Build one result record from an observation that has already been made."""
    return ResultVerification(
        operation_id, verifier, communication, effect, result, observation, evidence
    )


class _DatabaseReplaced(OSError):
    """The database inode changed between the no-follow check and the read."""


def _read_regular(path: Path, limit: int) -> tuple[bytes, str, str]:
    """Return a regular file's bytes, their SHA-256, and its mtime in nanoseconds.

    At most ``limit + 1`` bytes are read. A longer file is refused so a file
    that grows after ``fstat`` cannot pass the limit. The descriptor is closed
    if ``fstat`` or the read fails.

    Raises
    ------
    PermissionError
        If this platform cannot open a file without following a final link.
    IsADirectoryError
        If the opened inode is not a regular file.
    ValueError
        If the file contains more than ``limit`` bytes.
    """
    if _NOFOLLOW is None:
        raise PermissionError("this platform cannot open a file without following a final link")
    descriptor = os.open(path, os.O_RDONLY | _NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise IsADirectoryError(str(path))
        raw = os.read(descriptor, limit + 1)
    finally:
        os.close(descriptor)
    if len(raw) > limit:
        raise ValueError("report exceeds the read limit")
    digest = hashlib.sha256(raw).hexdigest()
    return raw, digest, str(info.st_mtime_ns)


def verify_deployment(
    report: Path,
    revision: str,
    *,
    operation_id: str,
    communication: str = "not_supplied",
) -> ResultVerification:
    """Check that ``report`` names ``revision`` and says ``health`` is ``ok``.

    This reads the report. It does not observe the deployed process. The
    report is independent evidence only when the deployed service wrote it,
    not when the deploying worker did. Evidence records ``report_sha256`` and
    ``report_mtime_ns`` of the bytes that were judged.

    Parameters
    ----------
    report : pathlib.Path
        JSON object with string fields ``revision`` and ``health``.
    revision : str
        The revision the report is supposed to name.
    operation_id : str
        The operation being verified.
    communication : str, optional
        The boundary state from the outcome report, or ``not_supplied``.

    Returns
    -------
    ResultVerification
        ``verified`` only when the report's revision matches and ``health``
        is exactly ``ok``. The observation is ``report_names_revision_and_ok``.

    Raises
    ------
    ValueError
        If ``revision`` is not a plain token.
    """
    if not _REVISION.fullmatch(revision):
        raise ValueError("the revision is a plain token without whitespace")
    evidence = {"report": str(report), "requested_revision": revision}

    def record(effect: str, result: str, observation: str) -> ResultVerification:
        return _record(
            operation_id, "deployment", communication, effect, result, observation, evidence
        )

    try:
        raw, digest, mtime_ns = _read_regular(report, _REPORT_MAX)
    except FileNotFoundError:
        return record("absent", "not_verified", "report_absent")
    except ValueError:
        return record("unknown", "not_checked", "report_too_large")
    except OSError:
        return record("unknown", "not_checked", "report_not_a_readable_regular_file")
    evidence["report_sha256"] = digest
    evidence["report_mtime_ns"] = mtime_ns
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return record("unknown", "not_checked", "report_not_recognised")
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        return record("unknown", "not_checked", "report_not_recognised")
    if not isinstance(document, dict):
        return record("unknown", "not_checked", "report_not_recognised")
    observed_revision = document.get("revision")
    health = document.get("health")
    if not isinstance(observed_revision, str) or not isinstance(health, str):
        return record("unknown", "not_checked", "report_not_recognised")
    evidence["observed_revision"] = observed_revision
    evidence["observed_health"] = health
    if observed_revision != revision:
        return record("observed", "not_verified", "revision_differs")
    if health != _HEALTHY:
        return record("observed", "not_verified", "report_names_revision_but_not_ok")
    return record("observed", "verified", "report_names_revision_and_ok")


def _schema_text(connection: sqlite3.Connection) -> str:
    """Return the exact text whose SHA-256 is the schema digest.

    Each sqlite_master row with SQL, excluding internal sqlite_ names, is one
    block of type, a tab, name, a newline, the stored SQL, and a newline,
    ordered by type then name. The SQL text is copied as SQLite stored it.
    Different whitespace is a different digest.
    """
    rows = connection.execute(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()
    return "".join(f"{kind}\t{name}\n{sql}\n" for kind, name, sql in rows)


def _schema_digest(connection: sqlite3.Connection) -> str:
    """Return the SHA-256 of :func:`_schema_text`."""
    return hashlib.sha256(_schema_text(connection).encode("utf-8")).hexdigest()


def _open_readonly(path: Path) -> sqlite3.Connection:
    """Open a regular database file in SQLite read-only mode.

    The no-follow descriptor stays open until SQLite has opened the resolved
    path. If that path is no longer the same inode, the connection is closed
    and :class:`_DatabaseReplaced` is raised. ``resolve`` follows links, so
    this comparison is what stops a swap onto a different file.

    A WAL database in a directory the reader cannot write may fail to open,
    or may miss the latest frames when ``-shm`` cannot be created. That
    failure is ``database_not_readable``, not a verified schema.
    """
    if _NOFOLLOW is None:
        raise PermissionError("this platform cannot open a file without following a final link")
    descriptor = os.open(path, os.O_RDONLY | _NOFOLLOW)
    connection: sqlite3.Connection | None = None
    try:
        held = os.fstat(descriptor)
        if not stat.S_ISREG(held.st_mode):
            raise IsADirectoryError(str(path))
        resolved = path.resolve()
        connection = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True)
        opened = os.stat(resolved)
        if (opened.st_dev, opened.st_ino) != (held.st_dev, held.st_ino):
            raise _DatabaseReplaced("the database changed between the check and the read")
        connection.execute("PRAGMA query_only = ON")
        return connection
    except Exception:
        if connection is not None:
            connection.close()
        raise
    finally:
        os.close(descriptor)


def _applied_migration(
    connection: sqlite3.Connection,
    migrations_table: str,
    migration_column: str,
    migration: str,
    evidence: dict[str, str],
) -> tuple[str, tuple[object, ...] | None]:
    """Read the schema digest and look up one migration identity.

    The digest is stored on ``evidence`` as soon as SQLite returns it, including
    when a later statement fails. Identifiers were matched to a closed pattern.
    A missing column is not read as a double-quoted string literal.

    Parameters
    ----------
    connection : sqlite3.Connection
        Read-only connection already checked against the opened inode.
    migrations_table, migration_column : str
        Plain SQL identifiers of the applied-migration table and its column.
    migration : str
        Migration identity that must be stored in ``migration_column``.
    evidence : dict
        Result evidence. Receives ``observed_schema_sha256``.

    Returns
    -------
    tuple
        An absence observation, empty when the identity was queried, and the
        matching row or ``None`` when that identity is not stored.

    Raises
    ------
    sqlite3.Error
        If SQLite cannot read the schema or the migration table.
    """
    evidence["observed_schema_sha256"] = _schema_digest(connection)
    present = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (migrations_table,),
    ).fetchone()
    if present is None:
        return "migrations_table_absent", None
    described = connection.execute(f"PRAGMA table_info({migrations_table})").fetchall()
    if migration_column not in {str(row[1]) for row in described}:
        return "migration_column_absent", None
    found = connection.execute(
        f"SELECT 1 FROM {migrations_table} WHERE {migration_column} = ? LIMIT 1",  # noqa: S608
        (migration,),
    ).fetchone()
    return "", found


def schema_digest(database: Path) -> str:
    """Return the schema digest of ``database`` and do not write a verdict.

    Parameters
    ----------
    database : pathlib.Path
        SQLite database opened read-only.

    Returns
    -------
    str
        Lower-case hex SHA-256 of the schema text.

    Raises
    ------
    OSError
        If the database cannot be opened without following a final link.
    sqlite3.Error
        If SQLite cannot read the schema.
    """
    connection = _open_readonly(database)
    try:
        return _schema_digest(connection)
    finally:
        connection.close()


def verify_migration(
    database: Path,
    migration: str,
    schema_sha256: str,
    *,
    operation_id: str,
    communication: str = "not_supplied",
    migrations_table: str = "schema_migrations",
    migration_column: str = "version",
) -> ResultVerification:
    """Verify that ``migration`` is recorded and the schema digest matches.

    The database is opened with SQLite ``mode=ro`` after a no-follow check, so
    a symbolic link is not treated as the database and nothing is migrated.
    The held inode must still be the resolved path after connect. The schema
    digest is the SHA-256 of :func:`_schema_text`. A missing migration column
    is ``migration_column_absent``, not an unread database. A WAL file in a
    directory the reader cannot write may be ``database_not_readable``.

    Parameters
    ----------
    database : pathlib.Path
        SQLite database the migration was supposed to change.
    migration : str
        Migration identity that must be stored in ``migration_column``.
    schema_sha256 : str
        Lower-case hex SHA-256 of the requested schema text.
    operation_id : str
        The operation being verified.
    communication : str, optional
        The boundary state from the outcome report, or ``not_supplied``.
    migrations_table, migration_column : str, optional
        Plain SQL identifiers of the applied-migration table and its identity
        column. Defaults are ``schema_migrations`` and ``version``.

    Returns
    -------
    ResultVerification
        ``verified`` only when the identity is present and the schema digest
        matches.

    Raises
    ------
    ValueError
        If the migration identity, digest, or table names are not plain tokens.
    """
    if not _MIGRATION.fullmatch(migration):
        raise ValueError("the migration identity is a plain token")
    if not _HEX64.fullmatch(schema_sha256):
        raise ValueError("the schema digest is 64 lower-case hex digits")
    if not _IDENTIFIER.fullmatch(migrations_table) or not _IDENTIFIER.fullmatch(migration_column):
        raise ValueError("the migrations table and column are plain identifiers")
    evidence = {
        "database": str(database),
        "migration": migration,
        "migrations_table": migrations_table,
        "migration_column": migration_column,
        "requested_schema_sha256": schema_sha256,
    }

    def record(effect: str, result: str, observation: str) -> ResultVerification:
        return _record(
            operation_id,
            "database_migration",
            communication,
            effect,
            result,
            observation,
            evidence,
        )

    found: tuple[object, ...] | None = None
    try:
        connection = _open_readonly(database)
    except FileNotFoundError:
        return record("absent", "not_verified", "database_absent")
    except _DatabaseReplaced:
        return record("unknown", "not_checked", "database_changed_during_open")
    except OSError:
        return record("unknown", "not_checked", "database_not_a_readable_regular_file")
    try:
        try:
            absence, found = _applied_migration(
                connection, migrations_table, migration_column, migration, evidence
            )
            if absence:
                return record("absent", "not_verified", absence)
        except sqlite3.Error:
            return record("unknown", "not_checked", "database_not_readable")
    finally:
        connection.close()
    if found is None:
        return record("absent", "not_verified", "migration_absent")
    if evidence["observed_schema_sha256"] != schema_sha256:
        return record("observed", "not_verified", "schema_differs")
    return record("observed", "verified", "migration_applied_and_schema_matches")
