# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable marks of wake attempts and their outcomes
"""Remember, on disk, which wake was attempted for which message.

A wake is an effect outside the product: text typed into a terminal, a
message queued by a vendor's daemon. It cannot be taken back and, when its
result is lost, it must not be repeated. So before an adapter is asked, an
**attempt** mark is written and synced; after it answered, an **outcome**
mark. A process that dies in between leaves an attempt without an outcome,
and that reads as *uncertain*: the wake may have happened.

Layout below the marks directory::

    <seat key>/.lock                                held while a wake of the seat is at work
    <seat key>/<wake id>/<nn>-<adapter>.attempt.json
    <seat key>/<wake id>/<nn>-<adapter>.outcome.json

``wake id`` is the hub's sequence number of the message. ``nn`` is the order
of the attempt. Every mark is created by a hard link from a finished
temporary file, which never replaces an existing name: a mark, once written,
is not rewritten.

One wake of a seat is at work at a time. The seat's lock is held from the
reading of the marks, through the adapter's call, to the outcome mark; a
second relay for the same seat waits for it and then reads marks that are
complete. The lock covers the effect on purpose: that is what makes "at most
once" true for two processes.

Marks are kept until :func:`prune` removes the oldest of a seat. A message
whose marks were removed could be woken for again if the hub delivered it
again, so prune keeps the newest ones and is given a generous number.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from synapse_channel.advisory_lock import locked
from synapse_channel.core.errors import SynapseError
from synapse_channel.core.private_dir import ensure_private_dir
from synapse_channel.wake_adapters import KIND_PATTERN, STATES, UNCERTAIN, WakeOutcome

SCHEMA = "wake-mark-v1"
"""Schema name written into every mark."""

OPEN = "attempt"
"""State of an attempt that has no outcome mark."""

_UNSAFE = re.compile(r"[^0-9A-Za-z._-]+")
_MARK_NAME = re.compile(r"(\d{2})-([a-z][a-z0-9_]{0,31})\.(attempt|outcome)\.json")
_MAX_TEXT = 300


class WakeMarkError(SynapseError, ValueError):
    """A mark was asked for with a value that cannot name one."""

    code = "wake_mark"


@dataclass(frozen=True, slots=True)
class Mark:
    """One attempt and, when it is known, what came of it."""

    order: int
    adapter: str
    state: str
    proves: str = ""
    detail: str = ""
    at: str = ""


def default_marks_dir() -> Path:
    """Return the marks directory under the Synapse data directory."""
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "synapse" / "wake-marks"


def seat_key(seat: str) -> str:
    """Return the directory name of a seat: readable, and distinct per seat."""
    digest = hashlib.sha256(seat.encode("utf-8", errors="surrogatepass")).hexdigest()[:16]
    return f"{_UNSAFE.sub('-', seat)[:60] or 'seat'}__{digest}"


def _wake_dir(directory: Path, seat: str, wake_id: int) -> Path:
    if isinstance(wake_id, bool) or not isinstance(wake_id, int) or wake_id < 0:
        raise WakeMarkError("a wake id is the hub's sequence number: a whole number, not negative")
    if not seat.strip():
        raise WakeMarkError("a wake mark needs a seat")
    return directory / seat_key(seat) / str(wake_id)


def _sync_directory_posix(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


# A directory cannot be opened for syncing outside POSIX.
_sync_directory = _sync_directory_posix if os.name == "posix" else (lambda directory: None)


def _create(path: Path, content: dict[str, object]) -> None:
    """Create the mark ``path``; an existing mark is an error, never replaced."""
    temporary = path.parent / f".tmp-{os.getpid()}-{secrets.token_hex(6)}"
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(content, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    _sync_directory(path.parent)


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@contextmanager
def seat_lock(directory: Path, seat: str) -> Iterator[None]:
    """Hold the lock of one seat's wakes, waiting for it when it is taken."""
    if not seat.strip():
        raise WakeMarkError("a wake mark needs a seat")
    path = directory / seat_key(seat) / ".lock"
    with locked(path, blocking=True, purpose="wake marks directory"):
        yield


def begin(directory: Path, seat: str, wake_id: int, adapter: str, *, now: datetime) -> int:
    """Write the attempt mark of the next attempt and return its order.

    Call it before the adapter is asked, with :func:`seat_lock` held.

    Raises
    ------
    WakeMarkError
        The adapter's kind cannot be part of a mark's name.
    FileExistsError
        The mark exists: another attempt took this order.
    """
    if KIND_PATTERN.fullmatch(adapter) is None:
        raise WakeMarkError("an adapter kind is lower-case letters, digits and underscores")
    base = _wake_dir(directory, seat, wake_id)
    ensure_private_dir(base, parents=True, purpose="wake marks directory")
    order = len(read(directory, seat, wake_id)) + 1
    content = {"schema": SCHEMA, "seat": seat, "wake_id": wake_id, "adapter": adapter}
    _create(base / f"{order:02d}-{adapter}.attempt.json", {**content, "at": _stamp(now)})
    return order


def finish(
    directory: Path,
    seat: str,
    wake_id: int,
    adapter: str,
    order: int,
    outcome: WakeOutcome,
    *,
    now: datetime,
) -> None:
    """Write the outcome mark of an attempt; a state that is not known is uncertain."""
    base = _wake_dir(directory, seat, wake_id)
    state = outcome.state if outcome.state in STATES else UNCERTAIN
    _create(
        base / f"{order:02d}-{adapter}.outcome.json",
        {
            "schema": SCHEMA,
            "state": state,
            "proves": outcome.proves[:_MAX_TEXT],
            "detail": outcome.detail[:_MAX_TEXT],
            "at": _stamp(now),
        },
    )


def _outcome(path: Path) -> dict[str, object]:
    """Return an outcome mark's content; one that cannot be read says uncertain."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"state": UNCERTAIN, "detail": "the outcome mark cannot be read"}
    if not isinstance(data, dict) or data.get("state") not in STATES:
        return {"state": UNCERTAIN, "detail": "the outcome mark is not a known state"}
    return data


def read(directory: Path, seat: str, wake_id: int) -> tuple[Mark, ...]:
    """Return the attempts made for one message, in order.

    An attempt without an outcome mark has the state :data:`OPEN`.
    """
    base = _wake_dir(directory, seat, wake_id)
    if not base.is_dir():
        return ()
    names = {path.name for path in base.iterdir()}
    marks = []
    for name in sorted(names):
        match = _MARK_NAME.fullmatch(name)
        if match is None or match.group(3) != "attempt":
            continue
        order, adapter = int(match.group(1)), match.group(2)
        outcome_name = f"{order:02d}-{adapter}.outcome.json"
        if outcome_name not in names:
            marks.append(Mark(order, adapter, OPEN))
            continue
        data = _outcome(base / outcome_name)
        marks.append(
            Mark(
                order,
                adapter,
                str(data["state"]),
                str(data.get("proves") or ""),
                str(data.get("detail") or ""),
                str(data.get("at") or ""),
            )
        )
    return tuple(marks)


def prune(directory: Path, seat: str, *, keep_newest: int) -> int:
    """Remove the marks of all but the ``keep_newest`` highest wake ids of a seat.

    Done under the seat's lock, so no wake is at work meanwhile. Returns how
    many messages' marks were removed.
    """
    base = directory / seat_key(seat)
    if not base.is_dir():
        return 0
    with seat_lock(directory, seat):
        ids = sorted(int(path.name) for path in base.iterdir() if path.name.isdigit())
        old = ids[: max(len(ids) - keep_newest, 0)]
        for wake_id in old:
            shutil.rmtree(base / str(wake_id))
    return len(old)
