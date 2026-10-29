# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable start and outcome records of catalogue operation runs
"""Record, on disk, that a catalogue operation started and what came of it.

Layout below the journal directory::

    <operation id>/start.json     written and synced before the component is invoked
    <operation id>/outcome.json   written after it answered, if it answered

A record is created by a hard link from a finished, synced temporary file, so
an existing record is never replaced. After the link the record's directory is
synced, and then the entry of every directory on its path that this user owns
is synced into its parent, whichever runner created it. So a start record
and the path to it survive a machine stop before the component is invoked.

A start without an outcome means the run was interrupted after it may have
had its effect: the outcome is unknown and the operation is never run again
under the same id.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from pathlib import Path
from typing import Any

SCHEMA = "synapse-operation-run-v1"
"""Schema name written into every record."""

STATES = frozenset({"not_started", "confirmed_done", "confirmed_failed", "unknown"})
"""Outcome states a run can report."""

_OPERATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class RunJournalError(ValueError):
    """A record cannot be named, written or read as a run record."""


def run_directory(journal: Path, operation_id: str) -> Path:
    """Return the directory of one operation id, refusing an id that is not a plain name.

    Raises
    ------
    RunJournalError
        If ``operation_id`` is empty, too long or not ``[A-Za-z0-9._-]``.
    """
    if not isinstance(operation_id, str) or _OPERATION_ID.fullmatch(operation_id) is None:
        raise RunJournalError("an operation id is 1 to 128 of [A-Za-z0-9._-], starting alnum")
    return journal / operation_id


def _sync_directory(path: Path) -> None:
    """Flush ``path``'s directory entries to disk."""
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sync_ancestry(directory: Path) -> None:
    """Sync the entry of ``directory`` and of each ancestor this user owns into its parent.

    A directory another runner has just created may already exist before that
    runner has synced its parent. Seeing it is not enough, so every run syncs
    the whole chain it relies on, whoever created each link. The climb stops
    above the first directory that belongs to another user: no runner of this
    user created that one.
    """
    owner = os.geteuid()
    current = directory
    while os.lstat(current).st_uid == owner and current.parent != current:
        _sync_directory(current.parent)
        current = current.parent


def write_record(directory: Path, name: str, content: dict[str, Any]) -> None:
    """Create ``directory/name`` once, synced; an existing record is an error.

    After the link the record's directory is synced, then its ancestry
    (:func:`_sync_ancestry`).

    Raises
    ------
    FileExistsError
        If the record already exists.
    OSError
        If a directory or the record cannot be created or synced.
    """
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = directory / f".tmp-{os.getpid()}-{secrets.token_hex(6)}"
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"schema": SCHEMA, **content}, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, directory / name)
    finally:
        temporary.unlink(missing_ok=True)
    _sync_directory(directory)
    _sync_ancestry(directory)


def read_record(directory: Path, name: str) -> dict[str, Any] | None:
    """Return one record, or ``None`` when it does not exist.

    Raises
    ------
    RunJournalError
        If the record exists but is not a run record of this schema.
    """
    path = directory / name
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise RunJournalError(f"{name} of {directory.name} cannot be read") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise RunJournalError(f"{name} of {directory.name} is not a {SCHEMA} record")
    return data


def run_state(journal: Path, operation_id: str) -> tuple[str, dict[str, Any] | None]:
    """Return what the journal says about one operation id.

    Returns
    -------
    tuple[str, dict or None]
        ``("absent", None)`` when nothing started; ``("unknown", start)`` when
        a start has no outcome; otherwise the outcome's state and the outcome.

    Raises
    ------
    RunJournalError
        If a record is damaged or an outcome names an unknown state.
    """
    directory = run_directory(journal, operation_id)
    start = read_record(directory, "start.json")
    outcome = read_record(directory, "outcome.json")
    if start is None and outcome is None:
        return "absent", None
    if outcome is None:
        return "unknown", start
    if outcome.get("state") not in STATES or start is None:
        raise RunJournalError(f"the outcome of {operation_id} is not a known run outcome")
    return str(outcome["state"]), outcome
