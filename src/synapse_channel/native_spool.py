# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — write-ahead spool for records of native messages
"""Keep a native-message record on disk until the hub has taken it.

A hook that records a message runs after the send has happened and must not
lose the record while the hub is away. So the record is written to a spool
file and flushed to disk **before** the hub is contacted, and the file moves to ``recorded/`` only
**after** the hub answered that it stored the record.

Layout below the spool directory::

    pending/<entry>.json              written, not yet on the hub
    recorded/<UTC date>/<entry>.json  the hub answered "recorded"
    failed/<entry>.json               refused by the hub, or out of attempts
    skipped/<UTC date>/<entry>.json   a call with nothing to record
    unparsed/<entry>.bin              hook input that was not JSON

One hook call makes one entry. The entry's name comes from the native session
and the tool call identifier, so the same call delivered twice finds its entry
and makes no second one, whichever state that entry is in.

Retry is bounded. A transport failure (hub unreachable, no answer, a hub too
old for the record verb, a sender not yet registered) counts one attempt and
leaves the entry pending; after ``max_attempts`` it moves to ``failed/``. A
refusal by the hub is final at once. A flush stops after two failures in a
row that reached for the hub; a failure decided before the hub was contacted
counts an attempt of its entry and does not hold the entries behind it.
Nothing in ``failed/`` is retried unless asked for.

Several hook processes may run at once. Three things keep them apart:

* A pending entry is created by a hard link from a finished temporary file.
  A link never replaces an existing name, so a second creator of the same
  entry cannot overwrite the first one's record; it learns that the entry
  exists.
* Every change of what exists is made under one short lock
  (``.spool.lock``): a creator's check of the other states together with its
  creation, and each move of an entry from one state to another. So a
  creator never sees an entry between its two places and never recreates an
  entry that has just been recorded. The lock is waited for; it is never held
  across a call to the hub.
* One flush runs at a time (``.flush.lock``, never waited for). A second
  flush, or a retry of failed entries during a flush, does nothing and says
  so; the entries wait for the running flush or the next one.

The locks are advisory file locks: ``flock`` on POSIX, a byte-range lock
through ``msvcrt`` on Windows. Every directory of the spool is created and
checked as owner-only by :func:`~synapse_channel.core.private_dir.ensure_private_dir`
(modes on POSIX, the access list on Windows); files are created with mode
0600 inside it.

What is synced, on POSIX: each file before it gets its name; the directory
of an entry after the entry is created and after each half of a move; and
the parent of every directory this module had to create. No power-loss test
has been made; this is a statement of the syncs requested, not of a
guarantee measured.

A creator waits for the creation lock without a deadline. The lock is held
only while files are written and renamed, never across a call to the hub,
so the wait is as long as those writes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from synapse_channel.durable_dir import private_dir, sync_directory

SCHEMA = "native-spool-entry-v1"
"""Schema name written into every spool entry."""

DEFAULT_MAX_ATTEMPTS = 20
"""Transport failures after which a pending entry becomes failed."""

MAX_CONSECUTIVE_FAILURES = 2
"""Transport failures in a row after which one flush stops: the hub is down."""

SPOOL_LOCK = ".spool.lock"
"""Lock file held, briefly, for every creation and every move of an entry."""

FLUSH_LOCK = ".flush.lock"
"""Lock file held by the one running flush."""

_UNSAFE = re.compile(r"[^0-9A-Za-z._-]+")
_STATES = ("pending", "recorded", "failed", "skipped")


@dataclass(frozen=True, slots=True)
class SendResult:
    """What one attempt to put a record on the hub gave.

    ``recorded`` is true only for the hub's own confirmation. ``final`` is true
    when the hub refused the record itself, so that another attempt cannot
    succeed. ``sent_record`` is the record as it was sent when the sender
    completed it, for example with a seat registered after the spooling.
    ``hub_asked`` is false when the attempt ended before the hub was
    contacted, for example for a session that is not registered yet; such a
    failure says nothing about the hub and does not stop a flush.
    """

    recorded: bool
    final: bool
    detail: str
    audit_seq: int | None = None
    sent_record: dict[str, Any] | None = None
    hub_asked: bool = True


@dataclass(frozen=True, slots=True)
class FlushReport:
    """Counts of one flush."""

    recorded: int = 0
    failed: int = 0
    left_pending: int = 0
    stopped_early: bool = False
    busy: bool = False
    """True when another flush was running and this one tried nothing."""


Sender = Callable[[dict[str, Any]], SendResult]


def default_spool_dir() -> Path:
    """Return the spool directory under the Synapse data directory."""
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "synapse" / "native-spool"


def entry_stem(native_session: str, call_id: str) -> str:
    """Return the file stem of the entry of one tool call.

    The stem depends only on the native session and the call identifier, so a
    hook call delivered twice maps to the same entry.
    """
    digest = hashlib.sha256(f"{native_session}\x00{call_id}".encode()).hexdigest()[:24]
    label = _UNSAFE.sub("-", call_id)[:48] or "call"
    return f"{label}__{digest}"


def _private_dir(path: Path) -> None:
    """Create ``path`` owner-only and sync the parent of each directory made."""
    private_dir(path, purpose="native message spool")


_sync_directory = sync_directory


@contextmanager
def _temporary(directory: Path, data: bytes) -> Iterator[Path]:
    """Yield a new flushed owner-only file holding ``data``; remove it afterwards.

    The caller renames or links it to its final name inside the block. Whatever
    happens, no temporary file is left behind.
    """
    _private_dir(directory)
    temporary = directory / f".tmp-{os.getpid()}-{secrets.token_hex(6)}"
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        yield temporary
    finally:
        temporary.unlink(missing_ok=True)


def _write_atomic(path: Path, data: bytes) -> None:
    """Put ``data`` at ``path`` through a temporary file, a rename and a sync."""
    with _temporary(path.parent, data) as temporary:
        temporary.replace(path)
    _sync_directory(path.parent)


def _create_new(path: Path, data: bytes) -> bool:
    """Create ``path`` with ``data`` unless it exists; say whether it was created.

    The data goes to a flushed owner-only temporary file, which is then hard
    linked to ``path``. A link fails when the name exists, so an existing file
    is never replaced, and a reader never sees a partly written one.
    """
    with _temporary(path.parent, data) as temporary:
        try:
            os.link(temporary, path)
        except FileExistsError:
            if not path.is_file():
                raise
            return False
    _sync_directory(path.parent)
    return True


if sys.platform == "win32":
    import errno
    import msvcrt

    _HELD_BY_ANOTHER = (errno.EACCES, errno.EDEADLK)
    """What the C runtime reports for a region locked by someone else."""

    def _take(descriptor: int, blocking: bool) -> bool:
        """Lock the first byte; a blocking request asks again until it has it.

        Only a region held by someone else is waited for or reported as busy.
        Any other error of the descriptor or the file system is raised.
        """
        while True:
            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                return True
            except OSError as exc:
                if exc.errno not in _HELD_BY_ANOTHER:
                    raise
                if not blocking:
                    return False
                time.sleep(0.02)

    def _release(descriptor: int) -> None:
        """Unlock the first byte."""
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _take(descriptor: int, blocking: bool) -> bool:
        """Take an exclusive ``flock``; a blocking request waits for it."""
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            return False
        return True

    def _release(descriptor: int) -> None:
        """Give the ``flock`` back."""
        fcntl.flock(descriptor, fcntl.LOCK_UN)


@contextmanager
def _lock(directory: Path, name: str, *, blocking: bool) -> Iterator[bool]:
    """Hold an advisory lock on a file of the spool directory.

    Yields whether the lock is held; a blocking request always holds it. The
    lock ends with the block, and with the process if it dies.
    """
    _private_dir(directory)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(directory / name, flags, 0o600)
    try:
        held = _take(descriptor, blocking)
        try:
            yield held
        finally:
            if held:
                _release(descriptor)
    finally:
        os.close(descriptor)


def _dump(entry: dict[str, Any]) -> bytes:
    return (json.dumps(entry, ensure_ascii=False, indent=1) + "\n").encode(
        "utf-8", errors="surrogatepass"
    )


def find_entry(directory: Path, stem: str, states: tuple[str, ...] = _STATES) -> Path | None:
    """Return the entry of ``stem`` in whichever of ``states`` it is, or None."""
    for state in states:
        base = directory / state
        if not base.is_dir():
            continue
        direct = base / f"{stem}.json"
        if direct.is_file():
            return direct
        for dated in sorted(base.glob(f"*/{stem}.json")):
            return dated
    return None


def spool(
    directory: Path,
    *,
    native_session: str,
    call_id: str,
    record: dict[str, Any],
    now: datetime | None = None,
) -> tuple[Path, bool]:
    """Write one record as a pending entry, unless its call already has an entry.

    The entry is never written over an existing one: of several processes
    that spool the same call at once, one creates the entry and the others
    are told that it exists.

    Returns
    -------
    tuple of (pathlib.Path, bool)
        The entry's path, and whether this call created it. A call without an
        identifier always creates a new entry, because nothing identifies it.
    """
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stem = entry_stem(native_session, call_id or secrets.token_hex(12))
    path = directory / "pending" / f"{stem}.json"
    entry = {
        "schema": SCHEMA,
        "state": "pending",
        "spooled_at": moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "native_session": native_session,
        "call_id": call_id,
        "attempts": [],
        "record": record,
    }
    # The link in ``_create_new`` is what protects an existing pending entry;
    # the lock makes the check of the other states and the creation one step
    # against every move.
    with _lock(directory, SPOOL_LOCK, blocking=True):
        settled = find_entry(directory, stem, _STATES[1:])
        if settled is not None:
            return settled, False
        return path, _create_new(path, _dump(entry))


def set_aside(
    directory: Path, kind: str, name: str, content: bytes, now: datetime | None = None
) -> Path:
    """Keep hook input that gives no record: ``skipped`` (dated) or ``unparsed``."""
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = moment.strftime("%Y%m%dT%H%M%S%fZ")
    safe = _UNSAFE.sub("-", name)[:48] or "input"
    if kind == "skipped":
        path = directory / "skipped" / moment.strftime("%Y-%m-%d") / f"{stamp}_{safe}.json"
    else:
        path = directory / "unparsed" / f"{stamp}_{safe}_{secrets.token_hex(4)}.bin"
    _write_atomic(path, content)
    return path


def pending_entries(directory: Path) -> list[Path]:
    """Return the pending entries, oldest first by spool time in the name's file."""
    base = directory / "pending"
    if not base.is_dir():
        return []
    entries = []
    for path in base.glob("*.json"):
        try:
            info = path.stat()
        except FileNotFoundError:
            # Moved away by a running flush between the listing and the look.
            # Any other error is the directory's own problem and is raised.
            continue
        if stat.S_ISREG(info.st_mode):
            entries.append((info.st_mtime_ns, path.name, path))
    return [path for _, _, path in sorted(entries)]


def _load(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="surrogatepass"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return None
    if not isinstance(data.get("record"), dict) or not isinstance(data.get("attempts"), list):
        return None
    return data


def _move(directory: Path, path: Path, entry: dict[str, Any], destination: Path) -> None:
    """Write the entry at its new place durably, then remove the old file.

    Both steps happen under the creation lock, so no creator sees the entry
    in neither place or recreates it in between.
    """
    with _lock(directory, SPOOL_LOCK, blocking=True):
        _write_atomic(destination, _dump(entry))
        path.unlink(missing_ok=True)
        _sync_directory(path.parent)


def flush(
    directory: Path,
    send: Sender,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    limit: int = 25,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> FlushReport:
    """Try to put pending entries on the hub, oldest first.

    Parameters
    ----------
    directory : pathlib.Path
        Spool directory.
    send : callable
        Puts one record on the hub and says what happened.
    max_attempts : int
        Transport failures after which an entry becomes failed.
    limit : int
        Most entries tried in one flush.
    now : callable
        Source of the current UTC time.

    Returns
    -------
    FlushReport
        Counts. ``stopped_early`` is true when two attempts in a row reached
        for the hub and failed in transport, and the rest was left for a later
        flush. ``busy`` is true when another flush was running and nothing was
        tried.
    """
    with _lock(directory, FLUSH_LOCK, blocking=False) as held:
        if not held:
            return FlushReport(left_pending=len(pending_entries(directory)), busy=True)
        return _flush_held(directory, send, max_attempts=max_attempts, limit=limit, now=now)


def _flush_held(
    directory: Path,
    send: Sender,
    *,
    max_attempts: int,
    limit: int,
    now: Callable[[], datetime],
) -> FlushReport:
    """Try the pending entries; the caller holds the flush lock."""
    recorded = failed = consecutive = 0
    stopped = False
    entries = pending_entries(directory)
    for path in entries[:limit]:
        entry = _load(path)
        if entry is None:
            continue
        result = send(entry["record"])
        if result.sent_record is not None:
            entry["record"] = result.sent_record
        moment = now().astimezone(timezone.utc)
        entry["attempts"].append(
            {
                "at": moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                "recorded": result.recorded,
                "final": result.final,
                "detail": result.detail[:300],
            }
        )
        if result.recorded:
            entry["state"] = "recorded"
            entry["audit_seq"] = result.audit_seq
            _move(
                directory,
                path,
                entry,
                directory / "recorded" / moment.strftime("%Y-%m-%d") / path.name,
            )
            recorded += 1
            consecutive = 0
            continue
        if result.final or len(entry["attempts"]) >= max_attempts:
            entry["state"] = "failed"
            _move(directory, path, entry, directory / "failed" / path.name)
            failed += 1
        else:
            _write_atomic(path, _dump(entry))
        if result.final:
            consecutive = 0
            continue
        consecutive += int(result.hub_asked)
        if consecutive >= MAX_CONSECUTIVE_FAILURES:
            stopped = True
            break
    return FlushReport(
        recorded=recorded,
        failed=failed,
        left_pending=len(pending_entries(directory)),
        stopped_early=stopped,
    )


def retry_failed(directory: Path) -> int | None:
    """Move every failed entry back to pending with its attempts cleared.

    The earlier attempts are kept under ``earlier_attempts``; nothing is lost.

    Returns
    -------
    int or None
        How many entries were moved; None when a flush is running, and
        nothing was touched.
    """
    with _lock(directory, FLUSH_LOCK, blocking=False) as held:
        if not held:
            return None
        moved = 0
        for path in sorted((directory / "failed").glob("*.json")):
            entry = _load(path)
            if entry is None:
                continue
            entry.setdefault("earlier_attempts", []).extend(entry["attempts"])
            entry["attempts"] = []
            entry["state"] = "pending"
            _move(directory, path, entry, directory / "pending" / path.name)
            moved += 1
        return moved


def counts(directory: Path) -> dict[str, int]:
    """Return how many entries each state holds."""
    result = {}
    for state in (*_STATES, "unparsed"):
        base = directory / state
        result[state] = sum(1 for path in base.rglob("*") if path.is_file()) if base.is_dir() else 0
    return result
