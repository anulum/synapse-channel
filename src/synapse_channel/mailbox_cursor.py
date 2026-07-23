# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — persist a mailbox waiter's since_seq cursor across re-arms
"""Persist a mailbox waiter's ``since_seq`` cursor so a re-arm resumes, not re-replays.

A persistent waiter re-arms as a fresh process after every wake, so the in-memory
mailbox cursor it advanced during one wait is gone by the next. Without a durable
cursor the next process would declare ``since_seq: 0`` and be replayed the entire
retained directed backlog again — waking immediately on stale messages, re-arming,
and waking again: a self-inflicted wake storm. This module keeps the cursor in a
tiny per-identity file (mirroring the relay's resumable byte cursor) so each re-arm
picks up where the last left off and is replayed only what genuinely arrived since.

The file holds a single integer. A missing, corrupt, or unreadable file reads as
``0`` (replay the whole retained window) rather than an error, because a lost cursor
degrades to the old catch-up-everything behaviour, never a crash — and the client
dedups the replay by ``seq`` regardless.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from urllib.parse import quote

CURSOR_FILE_MODE = 0o600


def cursor_path(identity: str, *, base: Path | None = None) -> Path:
    """Return the cursor file path for ``identity``, one file per identity.

    The identity is URL-quoted with no safe characters, so a ``<project>/<id>`` name
    becomes a single flat filename (its slash is escaped) rather than a nested
    directory a stray identity could climb out of.

    Parameters
    ----------
    identity : str
        The bare identity whose backlog cursor this is (the waiter's ``for`` name).
    base : pathlib.Path or None, optional
        Directory to hold cursor files. ``None`` uses ``~/synapse/mailbox-cursor``,
        a sibling of the hub's durable feed and database.

    Returns
    -------
    pathlib.Path
        The path to this identity's cursor file.
    """
    directory = base if base is not None else Path.home() / "synapse" / "mailbox-cursor"
    return directory / quote(identity, safe="")


def load_cursor(path: str | Path) -> int:
    """Read a persisted ``since_seq`` cursor, returning ``0`` when absent or unreadable.

    Parameters
    ----------
    path : str or pathlib.Path
        File holding a single integer cursor value.

    Returns
    -------
    int
        The stored cursor (clamped non-negative), or ``0`` on a missing, corrupt, or
        unreadable file.
    """
    marker = Path(path)
    if not marker.exists():
        return 0
    try:
        return max(0, int(marker.read_text(encoding="utf-8").strip()))
    except (ValueError, OSError):
        return 0


def save_cursor(path: str | Path, seq: int) -> None:
    """Persist a ``since_seq`` cursor (clamped non-negative), creating parents.

    The write is atomic and owner-only: the value is written to a temporary file
    in the destination directory (created ``0o600`` by :func:`tempfile.mkstemp`),
    flushed to disk, then renamed onto the cursor path with :func:`os.replace`. A
    crash or a concurrent reader therefore never observes a half-written cursor — a
    torn value would read back as a smaller (or corrupt) ``since_seq`` and replay a
    slice of the backlog again, the very wake storm the persisted cursor exists to
    prevent. The temporary file is removed if the replace never happens.

    Parameters
    ----------
    path : str or pathlib.Path
        Destination cursor file.
    seq : int
        Cursor value to store; negative values are clamped to ``0``.
    """
    from synapse_channel.core.secure_path import apply_owner_only_file

    marker = Path(path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=marker.parent, prefix=f"{marker.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(str(max(int(seq), 0)))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, marker)
        apply_owner_only_file(marker)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
