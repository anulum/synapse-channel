# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — local register of which seat a vendor's native session is
"""Which seat a vendor's native session belongs to, kept on this machine.

A vendor's own channel names sessions in its own way: by a session identifier
on the sending side and by a session name on the addressed side. A record of a
native message needs seats. This register maps one to the other.

It is local state, not hub state. Every registration is a new file; nothing is
overwritten, so the register is also its own history. The newest registration
of a session identifier, or of a session name, is the one in force.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from synapse_channel.core.errors import SynapseError

SCHEMA = "native-seat-registration-v1"
"""Schema name written into every registration file."""

_MAX_CHARS = 256
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_UNSAFE = re.compile(r"[^0-9A-Za-z._-]+")


class SeatRegisterError(SynapseError, ValueError):
    """A registration value is unusable."""

    code = "native_seat_register"


@dataclass(frozen=True, slots=True)
class Registration:
    """One statement that a native session is a seat."""

    native_session: str
    seat: str
    session_name: str | None
    registered_at: str


def default_register_dir() -> Path:
    """Return the register directory under the Synapse data directory."""
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "synapse" / "native-seats"


def _clean(value: str, field: str) -> str:
    text = value.strip()
    if not text or len(text) > _MAX_CHARS or _CONTROL.search(text) is not None:
        raise SeatRegisterError(
            f"{field} must be 1 to {_MAX_CHARS} characters without control characters"
        )
    return text


def register(
    directory: Path,
    *,
    native_session: str,
    seat: str,
    session_name: str | None = None,
    now: datetime | None = None,
) -> Path:
    """Write one registration as a new owner-only file and return its path.

    Raises
    ------
    SeatRegisterError
        A value is empty, too long or holds a control character.
    """
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    entry = Registration(
        native_session=_clean(native_session, "native session"),
        seat=_clean(seat, "seat"),
        session_name=_clean(session_name, "session name") if session_name else None,
        registered_at=moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    )
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    stem = f"{_UNSAFE.sub('-', entry.native_session)[:80]}__{moment.strftime('%Y%m%dT%H%M%S%fZ')}"
    path = directory / f"{stem}__{secrets.token_hex(4)}.json"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump({"schema": SCHEMA, **asdict(entry)}, handle, ensure_ascii=False, indent=1)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    return path


def load(directory: Path) -> tuple[Registration, ...]:
    """Return every readable registration, oldest first.

    A file that is not a registration of the known schema is left out; it is
    never repaired or removed.
    """
    found: list[tuple[str, str, Registration]] = []
    if not directory.is_dir():
        return ()
    for path in directory.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or data.get("schema") != SCHEMA:
            continue
        session, seat, at = data.get("native_session"), data.get("seat"), data.get("registered_at")
        name = data.get("session_name")
        if not all(isinstance(value, str) and value for value in (session, seat, at)):
            continue
        entry = Registration(
            native_session=str(session),
            seat=str(seat),
            session_name=name if isinstance(name, str) and name else None,
            registered_at=str(at),
        )
        found.append((entry.registered_at, path.name, entry))
    # The file name breaks a tie of two registrations made in the same microsecond.
    return tuple(entry for _at, _name, entry in sorted(found, key=lambda item: item[:2]))


def seat_of_session(
    registrations: tuple[Registration, ...], native_session: str
) -> Registration | None:
    """Return the newest registration of a native session identifier."""
    matches = [item for item in registrations if item.native_session == native_session]
    return matches[-1] if matches else None


def seat_of_name(registrations: tuple[Registration, ...], session_name: str) -> Registration | None:
    """Return the newest registration that carries a session name."""
    matches = [item for item in registrations if item.session_name == session_name]
    return matches[-1] if matches else None
