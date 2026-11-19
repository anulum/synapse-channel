# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — deployment and database-migration result checks
"""Read a health report or generation chain + service start record, and a database schema.

:func:`verify_deployment` supports two modes:

* Legacy report mode checks what a JSON health report says. That is independent
  evidence only when the deployed service, not the deploying worker, wrote the
  report.
* Chained mode reads an owner-only ``synapse-generation.v1`` chain directory and
  the deployed service's atomic ``synapse-service-start.v1`` start record,
  distinguishing an unpublished or sealed slot, a slot claimed by another
  attempt, a published slot that has not yet converged, an unrecognised start
  record, and a converged deployment.

:func:`verify_migration` opens a SQLite database read-only, looks for one
migration identity, and compares a defined digest of ``sqlite_master``. Neither
function writes, retries, or repairs.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from synapse_channel.core.generation_chain import (
    EMPTY_CHAIN_ARTEFACT,
    KIND_SEALED,
    MAX_GENERATION,
    GenerationChainError,
    GenerationRecord,
    chain_head,
    read_generation,
)
from synapse_channel.result_verification import ResultVerification

START_RECORD_SCHEMA: Final = "synapse-service-start.v1"
"""Schema identifier stored in every service start record."""

_START_RECORD_KEYS: Final = frozenset({"artefact", "generation", "pid", "schema", "started_at"})
_START_RECORD_MAX: Final = 4096
_ATTEMPT_ID: Final = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_REVISION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}")
_MIGRATION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
_REPORT_MAX = 65536
_NOFOLLOW: int | None = getattr(os, "O_NOFOLLOW", None)
_NONBLOCK: int | None = getattr(os, "O_NONBLOCK", None)
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

    Opens ``path`` with ``O_NOFOLLOW | O_NONBLOCK`` so a FIFO or device node
    cannot block the verifier before ``fstat`` refuses non-regular inodes, then
    reads in a loop up to ``limit + 1`` bytes or EOF so short reads do not
    truncate valid files and post-``fstat`` growth past ``limit`` is refused.
    The descriptor is always closed.

    Raises
    ------
    PermissionError
        If this platform lacks ``O_NOFOLLOW`` or ``O_NONBLOCK``.
    IsADirectoryError
        If the opened inode is not a regular file.
    ValueError
        If the file contains more than ``limit`` bytes.
    """
    if _NOFOLLOW is None or _NONBLOCK is None:
        raise PermissionError(
            "this platform cannot open a file without blocking or following a final link"
        )
    descriptor = os.open(path, os.O_RDONLY | _NOFOLLOW | _NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise IsADirectoryError(str(path))
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining > 0:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    finally:
        os.close(descriptor)
    raw = b"".join(chunks)
    if len(raw) > limit:
        raise ValueError("report exceeds the read limit")
    digest = hashlib.sha256(raw).hexdigest()
    return raw, digest, str(info.st_mtime_ns)


def _positive_json_int(value: object, *, maximum: int | None = None) -> int:
    """Require an exact positive integer (rejecting booleans) within ``maximum``."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("expected a positive integer")
    if maximum is not None and value > maximum:
        raise ValueError("integer exceeds upper bound")
    return value


def _require_str(value: object, label: str) -> str:
    """Require a ``str`` value for ``label``."""
    if not isinstance(value, str):
        raise ValueError(f"start record {label} must be text")
    return value


@dataclass(frozen=True, slots=True)
class ServiceStartRecord:
    """Immutable ``synapse-service-start.v1`` record written by a deployed service.

    Attributes
    ----------
    generation : int
        Chain generation ``>= 1`` loaded by the service.
    artefact : str
        Lower-case 64-hex SHA-256 checked by the service after loading.
    pid : int
        Positive process identifier of the deployed service.
    started_at : str
        Non-empty printable ASCII UTC timestamp text recorded at service startup.
    schema : str
        Fixed ``"synapse-service-start.v1"`` schema tag.
    """

    generation: int
    artefact: str
    pid: int
    started_at: str
    schema: str = START_RECORD_SCHEMA

    def __post_init__(self) -> None:
        """Validate schema, generation range, 64-hex artefact, pid, and ASCII timestamp."""
        if _require_str(self.schema, "schema") != START_RECORD_SCHEMA:
            raise ValueError("unsupported service start record schema")
        _positive_json_int(self.generation, maximum=MAX_GENERATION)
        if not _HEX64.fullmatch(_require_str(self.artefact, "artefact")):
            raise ValueError("start record artefact must be 64 lower-case hex digits")
        _positive_json_int(self.pid)
        started = _require_str(self.started_at, "started_at")
        if not started.strip() or any(ord(ch) < 0x20 or ord(ch) >= 0x7F for ch in started):
            raise ValueError("start record started_at must be non-empty printable ASCII text")

    def to_dict(self) -> dict[str, int | str]:
        """Return the exact five-key dictionary representation."""
        return {
            "artefact": self.artefact,
            "generation": self.generation,
            "pid": self.pid,
            "schema": self.schema,
            "started_at": self.started_at,
        }

    def canonical_bytes(self) -> bytes:
        """Return sorted-key compact ASCII JSON bytes with no trailing whitespace."""
        return json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")


@dataclass(frozen=True, slots=True)
class _ChainedDeploymentSpec:
    """Validated inputs for generation-chained deployment verification."""

    revision: str
    chain: Path
    start_record: Path
    base_generation: int
    base_artefact_sha256: str
    artefact_sha256: str
    attempt_id: str


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Construct a JSON object while rejecting duplicate member names."""
    out: dict[str, object] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate key in service start record")
        out[key] = value
    return out


def _decode_start_record(raw: bytes) -> ServiceStartRecord:
    """Strictly decode ``raw`` and enforce exact keys and canonical ASCII JSON bytes."""
    text = raw.decode("ascii")
    parsed = json.loads(text, object_pairs_hook=_no_duplicate_keys)
    if not isinstance(parsed, dict) or set(parsed) != _START_RECORD_KEYS:
        raise ValueError("start record keys do not match synapse-service-start.v1")
    record = ServiceStartRecord(
        generation=_positive_json_int(parsed["generation"], maximum=MAX_GENERATION),
        artefact=_require_str(parsed["artefact"], "artefact"),
        pid=_positive_json_int(parsed["pid"]),
        started_at=_require_str(parsed["started_at"], "started_at"),
        schema=_require_str(parsed["schema"], "schema"),
    )
    if raw != record.canonical_bytes():
        raise ValueError("start record is not in canonical JSON encoding")
    return record


def _validate_chained_spec(
    revision: str,
    chain: Path,
    start_record: Path,
    base_generation: int,
    base_artefact_sha256: str,
    artefact_sha256: str,
    attempt_id: str,
) -> _ChainedDeploymentSpec:
    """Validate chained deployment arguments before touching the filesystem."""
    if (
        isinstance(base_generation, bool)
        or not isinstance(base_generation, int)
        or base_generation < 0
        or base_generation >= MAX_GENERATION
    ):
        raise ValueError("base_generation must be an integer in [0, 2**64 - 2]")
    if not _HEX64.fullmatch(base_artefact_sha256):
        raise ValueError("base_artefact_sha256 must be 64 lower-case hex digits")
    if not _HEX64.fullmatch(artefact_sha256):
        raise ValueError("artefact_sha256 must be 64 lower-case hex digits")
    if not _ATTEMPT_ID.fullmatch(attempt_id):
        raise ValueError("attempt_id has the wrong form")
    return _ChainedDeploymentSpec(
        revision=revision,
        chain=chain,
        start_record=start_record,
        base_generation=base_generation,
        base_artefact_sha256=base_artefact_sha256,
        artefact_sha256=artefact_sha256,
        attempt_id=attempt_id,
    )


def _read_chain_slots(
    chain: Path, base_generation: int
) -> tuple[int, GenerationRecord | None, GenerationRecord | None]:
    """Read the chain head, optional pre-state slot, and target slot ``base + 1``."""
    head = chain_head(chain)
    base_slot = read_generation(chain, base_generation) if base_generation > 0 else None
    target_slot = read_generation(chain, base_generation + 1)
    return head, base_slot, target_slot


def _check_chain_prestate(
    base_generation: int,
    base_artefact_sha256: str,
    base_slot: GenerationRecord | None,
    evidence: dict[str, str],
) -> bool:
    """Check that ``base_generation`` matches ``base_artefact_sha256`` (zeros at base 0)."""
    if base_generation == 0:
        return base_artefact_sha256 == EMPTY_CHAIN_ARTEFACT
    if base_slot is None:
        return False
    evidence["observed_base_artefact_sha256"] = base_slot.artefact
    return base_slot.artefact == base_artefact_sha256


def _verify_start_convergence(
    start_record: Path,
    target_generation: int,
    artefact_sha256: str,
    evidence: dict[str, str],
) -> tuple[str, str]:
    """Read ``start_record`` strictly and return ``(result, observation)`` for an applied slot."""
    try:
        raw, digest, mtime_ns = _read_regular(start_record, _START_RECORD_MAX)
    except FileNotFoundError:
        return "not_checked", "published_not_converged"
    except (OSError, ValueError):
        return "not_checked", "start_record_not_recognised"
    evidence["start_record_sha256"] = digest
    evidence["start_record_mtime_ns"] = mtime_ns
    try:
        started = _decode_start_record(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return "not_checked", "start_record_not_recognised"
    evidence["observed_start_generation"] = str(started.generation)
    evidence["observed_start_artefact_sha256"] = started.artefact
    evidence["observed_start_pid"] = str(started.pid)
    evidence["observed_started_at"] = started.started_at
    if started.generation < target_generation:
        return "not_checked", "published_not_converged"
    if started.generation > target_generation:
        return "not_checked", "superseded_before_convergence_seen"
    if started.artefact != artefact_sha256:
        return "not_verified", "converged_artefact_differs"
    return "verified", "published_and_converged"


def _build_chained_evidence(spec: _ChainedDeploymentSpec) -> dict[str, str]:
    """Build the initial evidence dictionary for a chained deployment check."""
    return {
        "chain": str(spec.chain),
        "start_record": str(spec.start_record),
        "requested_revision": spec.revision,
        "attempt_id": spec.attempt_id,
        "base_generation": str(spec.base_generation),
        "target_generation": str(spec.base_generation + 1),
        "requested_base_artefact_sha256": spec.base_artefact_sha256,
        "requested_artefact_sha256": spec.artefact_sha256,
    }


def _evaluate_target_slot(
    slot: GenerationRecord | None,
    spec: _ChainedDeploymentSpec,
    evidence: dict[str, str],
) -> tuple[str, str, str] | None:
    """Populate slot evidence and return an early ``(effect, result, observation)``, or ``None``."""
    if slot is None:
        return "absent", "not_verified", "not_published"
    evidence["slot_sha256"] = hashlib.sha256(slot.canonical_bytes()).hexdigest()
    evidence["slot_kind"] = slot.kind
    evidence["slot_attempt"] = slot.attempt
    evidence["slot_artefact_sha256"] = slot.artefact
    if slot.kind == KIND_SEALED:
        return "absent", "not_verified", "sealed_before_publication"
    if slot.attempt != spec.attempt_id:
        return "absent", "not_verified", "slot_taken_by_another_attempt"
    if slot.artefact != spec.artefact_sha256:
        return "observed", "not_verified", "artefact_differs"
    return None


def _verify_chained_deployment(
    spec: _ChainedDeploymentSpec,
    *,
    operation_id: str,
    communication: str,
) -> ResultVerification:
    """Verify a generation-chained deployment from the chain and service start record."""
    evidence = _build_chained_evidence(spec)

    def verdict(effect: str, result: str, observation: str) -> ResultVerification:
        return _record(
            operation_id, "deployment", communication, effect, result, observation, evidence
        )

    try:
        head, base_slot, slot = _read_chain_slots(spec.chain, spec.base_generation)
    except (GenerationChainError, OSError):
        return verdict("unknown", "not_checked", "chain_unreadable")
    evidence["chain_head"] = str(head)
    if not _check_chain_prestate(
        spec.base_generation, spec.base_artefact_sha256, base_slot, evidence
    ):
        return verdict("observed", "not_verified", "base_differs")
    slot_verdict = _evaluate_target_slot(slot, spec, evidence)
    if slot_verdict is not None:
        return verdict(*slot_verdict)
    result, observation = _verify_start_convergence(
        spec.start_record, spec.base_generation + 1, spec.artefact_sha256, evidence
    )
    return verdict("observed", result, observation)


def _verify_report_deployment(
    report: Path,
    revision: str,
    *,
    operation_id: str,
    communication: str,
) -> ResultVerification:
    """Verify a legacy deployment health report naming ``revision`` and ``health == 'ok'``."""
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


def verify_deployment(
    report: Path | None = None,
    revision: str = "",
    *,
    operation_id: str,
    communication: str = "not_supplied",
    chain: Path | None = None,
    start_record: Path | None = None,
    base_generation: int | None = None,
    base_artefact_sha256: str | None = None,
    artefact_sha256: str | None = None,
    attempt_id: str | None = None,
) -> ResultVerification:
    """Verify a deployment from a legacy health report or from a generation chain + start record.

    Parameters
    ----------
    report : pathlib.Path or None, optional
        Legacy JSON health report with string fields ``revision`` and ``health``.
        Mutually exclusive with the generation-chain keyword arguments.
    revision : str
        Declared revision token without whitespace.
    operation_id : str
        Operation being verified.
    communication : str, optional
        Boundary state from an outcome report, or ``not_supplied``.
    chain : pathlib.Path or None, optional
        Owner-only ``synapse-generation.v1`` chain directory (chained mode).
    start_record : pathlib.Path or None, optional
        Path to the deployed service's ``synapse-service-start.v1`` file (chained mode).
    base_generation : int or None, optional
        Declared pre-state generation ``>= 0`` (chained mode).
    base_artefact_sha256 : str or None, optional
        Declared 64-hex pre-state artefact digest (64 zeros at ``base_generation == 0``).
    artefact_sha256 : str or None, optional
        Declared 64-hex target artefact digest at ``base_generation + 1``.
    attempt_id : str or None, optional
        Attempt identifier expected to have published ``base_generation + 1``.

    Returns
    -------
    ResultVerification
        ``verified`` only when the legacy report names ``revision`` with ``health == 'ok'``,
        or when chained slot ``base_generation + 1`` was ``applied`` by ``attempt_id`` with
        ``artefact_sha256`` (with matching pre-state) and ``start_record`` converged on
        ``base_generation + 1`` and ``artefact_sha256``.

    Raises
    ------
    ValueError
        If ``revision`` is malformed, both modes are mixed, or chained arguments are
        incomplete or syntactically invalid.
    """
    if not _REVISION.fullmatch(revision):
        raise ValueError("the revision is a plain token without whitespace")
    chained = (
        chain,
        start_record,
        base_generation,
        base_artefact_sha256,
        artefact_sha256,
        attempt_id,
    )
    if report is not None:
        if any(item is not None for item in chained):
            raise ValueError("give either report or the generation-chain arguments, not both")
        return _verify_report_deployment(
            report, revision, operation_id=operation_id, communication=communication
        )
    if (
        chain is None
        or start_record is None
        or base_generation is None
        or base_artefact_sha256 is None
        or artefact_sha256 is None
        or attempt_id is None
    ):
        raise ValueError(
            "give report, or chain, start_record, base_generation, base_artefact_sha256, "
            "artefact_sha256 and attempt_id together"
        )
    spec = _validate_chained_spec(
        revision,
        chain,
        start_record,
        base_generation,
        base_artefact_sha256,
        artefact_sha256,
        attempt_id,
    )
    return _verify_chained_deployment(spec, operation_id=operation_id, communication=communication)


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
        f"SELECT 1 FROM {migrations_table} WHERE {migration_column} = ? LIMIT 1",  # nosec B608 # _IDENTIFIER-validated names
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
