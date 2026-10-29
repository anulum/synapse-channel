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
of the attempt, written with two digits up to 99 and with as many as it needs
beyond; marks are read in the order of that number. The seat's and the
message's directories are created with the parent of each new directory
synced, before any adapter is asked. Every mark is created by a hard link from a finished
temporary file, which never replaces an existing name: a mark, once written,
is not rewritten.

One wake of a seat is at work at a time. The seat's lock is held from the
reading of the marks, through the adapter's call, to the outcome mark; a
second relay for the same seat waits for it and then reads marks that are
complete. The lock covers the effect on purpose: that is what makes "at most
once" true for two processes.

Marks are kept until :func:`prune` removes the oldest of a seat. A message
whose marks were removed could be woken for again if the hub delivered it
again, so prune keeps the newest ones and is given a generous number. Prune
never removes a wake whose uncertainty an operator has not yet resolved.

An operator resolves an uncertain wake with :func:`resolve`, which writes
``<wake id>/resolution.json`` once: the operator says whether the seat read
the message or the wake is abandoned. A resolution never runs an adapter
again; it only ends the question.
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
from synapse_channel.durable_dir import private_dir, sync_directory
from synapse_channel.wake_adapters import KIND_PATTERN, STATES, UNCERTAIN, WakeOutcome

SCHEMA = "wake-mark-v1"
"""Schema name written into every mark."""

OPEN = "attempt"
"""State of an attempt that has no outcome mark."""

RESOLUTION_SCHEMA = "wake-resolution-v1"
"""Schema name written into a resolution mark."""

RESOLUTIONS = frozenset({"read", "abandoned"})
"""What an operator can decide about an uncertain wake."""

_RESOLUTION_NAME = "resolution.json"

_UNSAFE = re.compile(r"[^0-9A-Za-z._-]+")
_MARK_NAME = re.compile(r"(\d{2,})-([a-z][a-z0-9_]{0,31})\.(attempt|outcome)\.json")
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
    sync_directory(path.parent)


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
    private_dir(base, purpose="wake marks directory")
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
    attempts = []
    for name in names:
        match = _MARK_NAME.fullmatch(name)
        if match is not None and match.group(3) == "attempt":
            attempts.append((int(match.group(1)), match.group(2)))
    marks = []
    # By number, not by name: the order is written with two digits up to 99
    # and with more beyond, and names would sort "100" before "99".
    for order, adapter in sorted(attempts):
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


def is_uncertain(marks: tuple[Mark, ...]) -> bool:
    """Return whether these marks leave the wake uncertain: open or answered uncertain."""
    return any(mark.state in {OPEN, UNCERTAIN} for mark in marks)


def resolution(directory: Path, seat: str, wake_id: int) -> dict[str, str] | None:
    """Return the operator's resolution of one wake, or ``None`` when there is none.

    Raises
    ------
    WakeMarkError
        A resolution mark exists but is not one this module wrote for exactly
        this seat and wake: a copied or misplaced resolution never closes
        another wake's question.
    """
    path = _wake_dir(directory, seat, wake_id) / _RESOLUTION_NAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise WakeMarkError("the resolution mark cannot be read") from exc
    keys = {"schema", "seat", "wake_id", "decision", "operator", "at"}
    if (
        not isinstance(data, dict)
        or set(data) != keys
        or data["schema"] != RESOLUTION_SCHEMA
        or not all(isinstance(data[key], str) for key in keys - {"wake_id"})
        or type(data["wake_id"]) is not int
        or data["decision"] not in RESOLUTIONS
    ):
        raise WakeMarkError("the resolution mark is not a known resolution")
    if (data["seat"], data["wake_id"]) != (seat, wake_id):
        raise WakeMarkError(
            f"the resolution mark beside wake {wake_id} of {seat} resolves another wake"
        )
    return {key: str(value) for key, value in data.items()}


def resolve(
    directory: Path, seat: str, wake_id: int, decision: str, operator: str, *, now: datetime
) -> None:
    """Record, once, an operator's decision about one uncertain wake.

    Parameters
    ----------
    directory : pathlib.Path
        Directory of the durable marks.
    seat : str
        The exact seat.
    wake_id : int
        The hub's sequence number of the message.
    decision : str
        ``read`` when the operator saw that the seat read the message,
        ``abandoned`` when the wake is given up.
    operator : str
        Who decides; kept in the mark.
    now : datetime
        Time of the decision.

    Raises
    ------
    WakeMarkError
        The decision or operator is not valid, or the wake is not uncertain.
    FileExistsError
        The wake was already resolved.
    """
    if decision not in RESOLUTIONS:
        raise WakeMarkError(f"a resolution is one of {sorted(RESOLUTIONS)}")
    if not operator.strip() or len(operator) > _MAX_TEXT or any(ord(c) < 32 for c in operator):
        raise WakeMarkError("a resolution names its operator in printable text")
    with seat_lock(directory, seat):
        if not is_uncertain(read(directory, seat, wake_id)):
            raise WakeMarkError("only an uncertain wake is resolved")
        _create(
            _wake_dir(directory, seat, wake_id) / _RESOLUTION_NAME,
            {
                "schema": RESOLUTION_SCHEMA,
                "seat": seat,
                "wake_id": wake_id,
                "decision": decision,
                "operator": operator,
                "at": _stamp(now),
            },
        )


def attempt_time(directory: Path, seat: str, wake_id: int, mark: Mark) -> str:
    """Return when an attempt began, from its attempt mark; empty when it cannot be read."""
    path = _wake_dir(directory, seat, wake_id) / f"{mark.order:02d}-{mark.adapter}.attempt.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(data.get("at") or "") if isinstance(data, dict) else ""


def seats(directory: Path) -> tuple[str, ...]:
    """Return every seat that has marks, read from the seat name in its attempt marks."""
    found: set[str] = set()
    if not directory.is_dir():
        return ()
    for attempt in sorted(directory.glob("*/*/*.attempt.json")):
        try:
            data = json.loads(attempt.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        seat = data.get("seat") if isinstance(data, dict) else None
        if isinstance(seat, str) and attempt.parent.parent.name == seat_key(seat):
            found.add(seat)
    return tuple(sorted(found))


def wake_ids(directory: Path, seat: str) -> tuple[int, ...]:
    """Return a seat's wake ids that have marks, lowest first."""
    base = directory / seat_key(seat)
    if not base.is_dir():
        return ()
    return tuple(sorted(int(path.name) for path in base.iterdir() if path.name.isdigit()))


def prune(directory: Path, seat: str, *, keep_newest: int) -> int:
    """Remove the marks of all but the ``keep_newest`` highest wake ids of a seat.

    Done under the seat's lock, so no wake is at work meanwhile. A wake that is
    uncertain and not resolved is kept whatever its age: removing it would let
    the message be woken for again. Returns how many messages' marks were
    removed.
    """
    base = directory / seat_key(seat)
    if not base.is_dir():
        return 0
    with seat_lock(directory, seat):
        ids = wake_ids(directory, seat)
        old = [
            wake_id
            for wake_id in ids[: max(len(ids) - keep_newest, 0)]
            if not is_uncertain(read(directory, seat, wake_id))
            or resolution(directory, seat, wake_id) is not None
        ]
        for wake_id in old:
            shutil.rmtree(base / str(wake_id))
    return len(old)
