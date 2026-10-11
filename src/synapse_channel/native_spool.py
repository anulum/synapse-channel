# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — write-ahead spool for records of native messages
"""Keep a native-message record on disk until the hub has taken it.

A hook that records a message must never lose it and must never block the
send it follows. So the record is written to a spool file and flushed to disk
**before** the hub is contacted, and the file moves to ``recorded/`` only
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
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "native-spool-entry-v1"
"""Schema name written into every spool entry."""

DEFAULT_MAX_ATTEMPTS = 20
"""Transport failures after which a pending entry becomes failed."""

MAX_CONSECUTIVE_FAILURES = 2
"""Transport failures in a row after which one flush stops: the hub is down."""

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


def _write_atomic(path: Path, data: bytes) -> None:
    """Write ``data`` through a flushed owner-only temporary file and a rename."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.parent / f".tmp-{os.getpid()}-{secrets.token_hex(6)}"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _dump(entry: dict[str, Any]) -> bytes:
    return (json.dumps(entry, ensure_ascii=False, indent=1) + "\n").encode(
        "utf-8", errors="surrogatepass"
    )


def find_entry(directory: Path, stem: str) -> Path | None:
    """Return the entry of ``stem`` in whichever state it is, or None."""
    for state in _STATES:
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

    Returns
    -------
    tuple of (pathlib.Path, bool)
        The entry's path, and whether this call created it. A call without an
        identifier always creates a new entry, because nothing identifies it.
    """
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stem = entry_stem(native_session, call_id or secrets.token_hex(12))
    if call_id:
        existing = find_entry(directory, stem)
        if existing is not None:
            return existing, False
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
    _write_atomic(path, _dump(entry))
    return path, True


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
    entries = [path for path in base.glob("*.json") if path.is_file()]
    return sorted(entries, key=lambda path: (path.stat().st_mtime_ns, path.name))


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


def _move(path: Path, entry: dict[str, Any], destination: Path) -> None:
    """Write the entry at its new place, then remove the old file."""
    _write_atomic(destination, _dump(entry))
    path.unlink(missing_ok=True)


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
        flush.
    """
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
            _move(path, entry, directory / "recorded" / moment.strftime("%Y-%m-%d") / path.name)
            recorded += 1
            consecutive = 0
            continue
        if result.final or len(entry["attempts"]) >= max_attempts:
            entry["state"] = "failed"
            _move(path, entry, directory / "failed" / path.name)
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


def retry_failed(directory: Path) -> int:
    """Move every failed entry back to pending with its attempts cleared.

    The earlier attempts are kept under ``earlier_attempts``; nothing is lost.
    """
    base = directory / "failed"
    moved = 0
    if not base.is_dir():
        return 0
    for path in sorted(base.glob("*.json")):
        entry = _load(path)
        if entry is None:
            continue
        entry.setdefault("earlier_attempts", []).extend(entry["attempts"])
        entry["attempts"] = []
        entry["state"] = "pending"
        _move(path, entry, directory / "pending" / path.name)
        moved += 1
    return moved


def counts(directory: Path) -> dict[str, int]:
    """Return how many entries each state holds."""
    result = {}
    for state in (*_STATES, "unparsed"):
        base = directory / state
        result[state] = sum(1 for path in base.rglob("*") if path.is_file()) if base.is_dir() else 0
    return result
