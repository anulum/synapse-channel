# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — wake adapter that queues the fixed prompt through the Codex daemon
"""Wake a Codex session through its own queue.

The client offers ``codex queue --thread <session> --message <text>``: its
local daemon puts a message into a running session. This adapter runs that
command with the relay's fixed prompt as the text.

The command takes no key by which a repeat could be recognised. So the
adapter is strict about what it calls what:

``accepted``
    Exit status 0 and the line ``Queued message <id> for thread <session>``
    for exactly the session asked. It shows that the daemon queued the
    message, nothing more.
``refused``
    The command was not started, or it ended with a non-zero status and one
    of the configured refusal texts on its error output. The one observed so
    far is the daemon's answer for a session it does not know.
``uncertain``
    Everything else: no answer within the time limit, a zero status without
    the line, a non-zero status with an unknown text. The message may be
    queued.

The session is named by its identifier alone, and only when it is a UUID; a
session *name* is never passed. The command runs from a fixed absolute path,
with an argument vector and no shell, in a process group of its own that is
ended when the time limit passes.
"""

from __future__ import annotations

import contextlib
import os
import re
import signal
import socket
import subprocess  # nosec B404
import uuid
from pathlib import Path

from synapse_channel.wake_adapters import (
    ACCEPTED,
    REFUSED,
    UNCERTAIN,
    Availability,
    WakeOutcome,
    WakeTarget,
)

KIND = "native_queue"
"""The adapter's name in marks and receipts."""

PROVES = "queued_by_native_daemon"
"""What an accepted wake of this adapter shows."""

DEFAULT_REFUSALS = ("no rollout found",)
"""Error texts that mean the daemon did not queue the message."""

_QUEUED = re.compile(r"Queued message (\S+) for thread (\S+)")
_MAX_DETAIL = 200


def canonical_session(value: str) -> str | None:
    """Return ``value`` when it is a UUID in its canonical lower-case form, else None.

    Other spellings of a UUID are not accepted: the identifier is passed on
    exactly as the registry holds it, or not at all.
    """
    try:
        parsed = str(uuid.UUID(value))
    except ValueError:
        return None
    return parsed if parsed == value else None


class CodexQueueAdapter:
    """Wake a seat by queueing the fixed prompt into its Codex session."""

    def __init__(
        self,
        executable: Path,
        *,
        host: str | None = None,
        refusals: tuple[str, ...] = DEFAULT_REFUSALS,
    ) -> None:
        self._executable = executable
        self._host = socket.gethostname() if host is None else host
        self._refusals = refusals

    @property
    def kind(self) -> str:
        """Return ``native_queue``."""
        return KIND

    def available(self, target: WakeTarget) -> Availability:
        """Say whether the exact session can be asked from this host."""
        if canonical_session(target.native_session) is None:
            return Availability(False, "the seat has no native session identifier that is a UUID")
        if target.execution_host != self._host:
            return Availability(False, "the session runs on another host")
        program = self._executable
        if not program.is_absolute() or not program.is_file() or not os.access(program, os.X_OK):
            return Availability(False, "the client's executable is not at its fixed path")
        return Availability(True)

    def wake(
        self, target: WakeTarget, wake_id: int, prompt: str, deadline_seconds: float
    ) -> WakeOutcome:
        """Queue the prompt once and say what the command reported."""
        del wake_id
        session = canonical_session(target.native_session)
        if session is None:
            return WakeOutcome(REFUSED, "", "not started: the session identifier is not a UUID")
        argv = [str(self._executable), "queue", "--thread", session, "--message", prompt]
        try:
            child = subprocess.Popen(  # noqa: S603  # nosec B603 - fixed path, argument vector
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        except OSError as exc:
            return WakeOutcome(REFUSED, "", f"not started: {type(exc).__name__}")
        try:
            output, errors = child.communicate(timeout=deadline_seconds)
        except subprocess.TimeoutExpired:
            _end(child)
            return WakeOutcome(UNCERTAIN, "", "no answer within the time limit; it may be queued")
        return self._judge(child.returncode, output, errors, session)

    def _judge(self, status: int, output: str, errors: str, session: str) -> WakeOutcome:
        if status == 0:
            match = _QUEUED.search(output)
            if match is not None and match.group(2) == session:
                return WakeOutcome(
                    ACCEPTED, PROVES, f"queued message {match.group(1)}"[:_MAX_DETAIL]
                )
            return WakeOutcome(
                UNCERTAIN, "", "exit status 0 without the queued line for the session"
            )
        if any(text in errors for text in self._refusals):
            return WakeOutcome(REFUSED, "", f"the daemon refused (exit status {status})")
        return WakeOutcome(UNCERTAIN, "", f"exit status {status} with an unknown error text")


def _end(child: subprocess.Popen[str]) -> None:
    """End the child and what it started, and wait for it."""
    if os.name == "posix":
        # The group may already be gone; that is the wanted state.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(child.pid, signal.SIGKILL)
    else:
        child.kill()
    child.communicate()
