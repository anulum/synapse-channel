# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — whether each hub following a token set has it in force
"""Publish and read whether each hub that follows a token set has it in force.

A hub keeps the last set it accepted when the file on disk fails validation,
so an operator who edited the file needs to know whether the hub took the
change. Each hub writes a small owner-only state file into the set's state
directory (``.<name>.lockdir``), named after its process id and start time so
several hubs never share one. The file holds the state (``applied`` or
``stale``), when it began, a reason code (``missing``, ``unsafe`` or
``invalid``) and a digest of the file status the hub last saw; it never holds
file content or a message. It also publishes the hub's token policy and how
many tokens in force that policy refused at the last change, so a tool that
writes the set can respect it.

Whether the hub behind a state file still runs is decided by a lock the hub
holds on ``hub-<pid>-<start>.lock`` for as long as it serves, not by the
process id, which another process may reuse. A reader that can take that
lock, or finds no lock file, knows the hub is gone and reports ``unknown``.
A running hub whose published digest differs from the file's status now has
not looked at the change yet; the reader reports it ``pending``. A hub checks
the file every second, and a clean stop removes its state file.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from synapse_channel.advisory_lock import locked
from synapse_channel.core.private_dir import PrivateDirError
from synapse_channel.core.secret_files import SecretFileError, read_secret_file
from synapse_channel.core.secure_path import apply_owner_only_file
from synapse_channel.core.token_set import TokenSetAuthenticator, state_directory, status_digest

STATE_VERSION = 1
"""Version of the hub state document."""

_STATE_LIMIT = 4096
_PURPOSE = "token-set state directory"
_STATES = frozenset({"applied", "stale"})
_CODES = frozenset({"missing", "unsafe", "invalid"})
_LOG = logging.getLogger(__name__)


def _instant(moment: float) -> str:
    """Return POSIX seconds as ``YYYY-MM-DDTHH:MM:SSZ``."""
    return datetime.fromtimestamp(moment, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class HubStateWriter:
    """Write this hub's state for one token set, and hold its liveness lock.

    Parameters
    ----------
    authenticator : TokenSetAuthenticator
        The hub's token set; observe it with this writer.
    pid : int, optional
        Process id in the file name; the current process by default.
    clock : Callable[[], float], optional
        Source of POSIX time for the start and the transitions.

    Raises
    ------
    PrivateDirError
        If the state directory cannot be created owner-only.
    RuntimeError
        If another writer already holds this hub's lock.
    """

    def __init__(
        self,
        authenticator: TokenSetAuthenticator,
        *,
        pid: int | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._authenticator = authenticator
        self._clock = clock
        self._pid = os.getpid() if pid is None else pid
        self._started = int(clock())
        directory = state_directory(authenticator.path)
        stem = f"hub-{self._pid}-{self._started}"
        self.path = directory / f"{stem}.json"
        self._lock = directory / f"{stem}.lock"
        self._stack = ExitStack()
        if not self._stack.enter_context(locked(self._lock, blocking=False, purpose=_PURPOSE)):
            self._stack.close()
            raise RuntimeError(f"another writer holds {self._lock}")

    def set_applied(self, path: Path, token_ids: tuple[str, ...]) -> None:
        """Record that the set on disk is in force."""
        self._write("applied", None)

    def set_rejected(self, path: Path, reason: str) -> None:
        """Record that the set on disk is not in force, by reason code only."""
        self._write("stale", self._authenticator.stale_code)

    def _write(self, state: str, code: str | None) -> None:
        """Replace the state file in one rename; log a write that fails."""
        document = {
            "version": STATE_VERSION,
            "pid": self._pid,
            "started": _instant(self._started),
            "state": state,
            "since": _instant(self._clock()),
            "reason_code": code,
            "signature": self._authenticator.signature_digest,
            "policy": self._authenticator.policy,
            "policy_refused": len(self._authenticator.policy_refused()),
        }
        try:
            descriptor, temporary = tempfile.mkstemp(
                dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp"
            )
        except OSError as exc:
            _LOG.warning("cannot publish the token-set hub state %s: %s", self.path, exc)
            return
        staged = Path(temporary)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(document) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            apply_owner_only_file(staged)
            os.replace(staged, self.path)
        except OSError as exc:
            staged.unlink(missing_ok=True)
            _LOG.warning("cannot publish the token-set hub state %s: %s", self.path, exc)

    def close(self) -> None:
        """Remove this hub's state and release its lock."""
        self.path.unlink(missing_ok=True)
        self._stack.close()
        self._lock.unlink(missing_ok=True)


@dataclass(frozen=True)
class HubState:
    """What one state file says, and whether its hub still runs.

    Attributes
    ----------
    name : str
        The state file name.
    running : bool
        Whether a hub still holds the file's lock.
    state : str
        ``applied`` or ``stale`` for a running hub that has seen the file on
        disk; ``pending`` for a running hub that has not seen its latest
        change; ``unknown`` for a hub that is gone; ``unreadable`` for a file
        that is not a valid state document.
    since, started : str or None
        When the state began and when the hub started.
    pid : int or None
        Process id recorded by the hub.
    reason_code : str or None
        Why a stale hub did not apply the file.
    signature : str or None
        Digest of the file status the hub last saw.
    require_expiry : bool
        Whether the hub admits only tokens with an end of validity.
    max_token_days : int or None
        Longest remaining validity the hub admits, in days.
    policy_refused : int
        Tokens in force that the hub's policy refused at its last change.
    """

    name: str
    running: bool
    state: str
    since: str | None = None
    started: str | None = None
    pid: int | None = None
    reason_code: str | None = None
    signature: str | None = None
    require_expiry: bool = False
    max_token_days: int | None = None
    policy_refused: int = 0


def _document(status: Path) -> dict[str, Any] | None:
    """Return a valid state document, or ``None``."""
    try:
        document = json.loads(
            read_secret_file(
                status, flag="token-set hub state", require_single_link=True, limit=_STATE_LIMIT
            )
        )
    except (SecretFileError, json.JSONDecodeError):
        return None
    if (
        not isinstance(document, dict)
        or document.get("version") != STATE_VERSION
        or document.get("state") not in _STATES
        or type(document.get("pid")) is not int
        or not isinstance(document.get("since"), str)
        or not isinstance(document.get("started"), str)
        or document.get("reason_code") not in _CODES | {None}
        or not isinstance(document.get("signature"), str)
        or type(document.get("policy_refused")) is not int
        or not _valid_policy(document.get("policy"))
    ):
        return None
    return document


def _valid_policy(policy: object) -> bool:
    """Whether ``policy`` is a published token policy."""
    if not isinstance(policy, dict) or set(policy) != {"require_expiry", "max_token_days"}:
        return False
    days = policy["max_token_days"]
    return type(policy["require_expiry"]) is bool and (
        days is None or (type(days) is int and days >= 1)
    )


def _running(lock: Path) -> bool:
    """Whether a hub holds ``lock``; a missing lock or taking it proves the hub is gone."""
    if not os.path.lexists(lock):
        return False
    with locked(lock, blocking=False, purpose=_PURPOSE) as free:
        return not free


def read_hub_states(path: Path) -> list[HubState]:
    """Return the state of every hub that published one for the set at ``path``.

    Parameters
    ----------
    path : pathlib.Path
        The token-set file.

    Returns
    -------
    list of HubState
        One entry per state file, in name order. A hub that is gone is
        reported ``unknown`` whatever its file says.
    """
    directory = state_directory(path)
    if not directory.is_dir():
        return []
    current = status_digest(path)
    states = []
    for status in sorted(directory.glob("hub-*.json")):
        running = _running(status.with_suffix(".lock"))
        document = _document(status)
        if document is None:
            states.append(HubState(status.name, running, "unreadable"))
            continue
        if not running:
            state = "unknown"
        elif document["signature"] != current:
            state = "pending"
        else:
            state = document["state"]
        states.append(
            HubState(
                status.name,
                running,
                state,
                since=document["since"],
                started=document["started"],
                pid=document["pid"],
                reason_code=document["reason_code"],
                signature=document["signature"],
                require_expiry=document["policy"]["require_expiry"],
                max_token_days=document["policy"]["max_token_days"],
                policy_refused=document["policy_refused"],
            )
        )
    return states


def publish_hub_state(authenticator: TokenSetAuthenticator) -> HubStateWriter | None:
    """Start publishing this hub's state for its token set.

    A hub that cannot publish still serves; the reason goes to the log.

    Returns
    -------
    HubStateWriter or None
        The writer, already observing the set, or ``None`` when publishing
        is not possible.
    """
    try:
        writer = HubStateWriter(authenticator)
    except (OSError, RuntimeError, PrivateDirError) as exc:
        _LOG.warning("token-set hub state not published: %s", exc)
        return None
    authenticator.observe(writer)
    return writer
