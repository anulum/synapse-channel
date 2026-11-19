# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the restore run: start the planned seats one at a time
"""Start the seats a restore plan names, one at a time, and record each outcome.

Each seat is started by a child ``synapse worker-session --restore --no-attach``
in the row's working directory, so a seat restored by the planner and one
restored by hand take the same path, and one hung or failing start cannot harm
the planner. The child gets an environment built from a fixed list of names,
with ``SYN_IDENTITY`` and ``SYN_PROJECT`` set from the row; ``SYN_HOME``, and any
path that lies in a git work tree or a coordination tree, are dropped. A child
that gives no answer within :data:`CHILD_TIMEOUT_SECONDS` has its process group
ended.

The child's exit code decides the run:

* ``0``: started; the old row's ``observe`` names the new row once seat-hold's
  start for it is seen;
* ``75``: the seat is live, and the run goes on;
* ``70``: the agent could not start; recorded, and the run goes on;
* anything else, or no answer: the run stops.

Only one run holds ``restore.lock`` in the ledger directory at a time.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import os
import signal
import time
from collections.abc import Awaitable, Callable, Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from synapse_channel.core.errors import SynapseError
from synapse_channel.restore_grace import HubGraceError
from synapse_channel.restore_pacing import Limits, Reading, wait_for_headroom
from synapse_channel.restore_plan import START, Entry, last_rows
from synapse_channel.seat_hold import (
    AGENT_START_FAILED_EXIT_CODE,
    SEAT_LIVE_EXIT_CODE,
)
from synapse_channel.seat_restore import MARKER_WAIT_SECONDS
from synapse_channel.session_ledger import (
    COLLECTOR_LOCK_WAIT_SECONDS,
    LedgerError,
    Row,
    append_event,
    fold,
    read_events,
    shared_tree,
)
from synapse_channel.session_ledger_collect import CollectError

CHILD_MARGIN_SECONDS = 25.0
"""Time beyond the holder's final word for the child's own start and its tmux calls."""

CHILD_TIMEOUT_SECONDS = MARKER_WAIT_SECONDS + CHILD_MARGIN_SECONDS
"""How long one seat's child may take before its process group is ended."""

END_GRACE_SECONDS = 5.0
"""How long a child has to end after ``SIGTERM`` before ``SIGKILL``."""

NEW_ROW_WAIT_SECONDS = 10.0
"""How long to look for seat-hold's start of the new row after a started seat."""

NO_HEADROOM_EXIT_CODE = 75
"""The host had no headroom in time: try again later."""

TIMEOUT_EXIT_CODE = 1
"""A child gave no answer in time; its seat's state is unknown, so the run stops."""

HOST_UNREADABLE_EXIT_CODE = 3
"""A snapshot before a start could not read the host or record it; the run stops."""

LOCK_NAME = "restore.lock"

ENV_PASSED = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "LC_CTYPE",
    "LC_MESSAGES",
    "TERM",
    "TZ",
    "SYNAPSE_TOKEN",
)
"""Names passed to a child as they are; nothing outside this list and the paths passes."""

ENV_PATHS = (
    "XDG_RUNTIME_DIR",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
    "XDG_CACHE_HOME",
    "TMPDIR",
    "TMUX_TMPDIR",
)
"""Directories passed only when absolute and outside every git work tree and coordination tree."""

ENV_DROPPED = ("SYN_HOME",)
"""Names that redirect Synapse state; always dropped, and named in the plan."""


class RestoreBusyError(SynapseError):
    """Another restore run holds the lock."""

    code = "restore_busy"


@contextlib.contextmanager
def restore_lock(directory: Path) -> Iterator[None]:
    """Hold the restore lock for the whole run, or raise :class:`RestoreBusyError` at once."""
    fd = os.open(directory / LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RestoreBusyError("another restore run holds the lock") from None
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def child_environ(
    environ: Mapping[str, str], entry: Entry, token: str | None = None
) -> tuple[dict[str, str], list[str]]:
    """Return a seat's child environment, and the names dropped from it with the reason.

    The hub token, when the run has one, reaches the child in ``SYNAPSE_TOKEN``,
    never on its command line. Values are never returned for printing; names are.
    """
    env = {name: environ[name] for name in ENV_PASSED if name in environ}
    dropped = [f"{name} (redirects Synapse state)" for name in ENV_DROPPED if name in environ]
    for name in ENV_PATHS:
        value = environ.get(name, "")
        if not value:
            continue
        path = Path(value)
        if not path.is_absolute():
            dropped.append(f"{name} (not an absolute path)")
        elif shared_tree(path.resolve()) is not None:
            dropped.append(f"{name} (in a git work tree or a coordination tree)")
        else:
            env[name] = value
    if token:
        env["SYNAPSE_TOKEN"] = token
    project = entry.project or entry.identity.partition("/")[0]
    env.update({"SYN_IDENTITY": entry.identity, "SYN_PROJECT": project})
    return env, dropped


def child_argv(entry: Entry, *, synapse_bin: str, uri: str, tmux_bin: str) -> list[str]:
    """Return the child command that restores one seat without attaching to it."""
    return [
        synapse_bin,
        "worker-session",
        "--identity",
        entry.identity,
        "--uri",
        uri,
        "--tmux-bin",
        tmux_bin,
        "--synapse-bin",
        synapse_bin,
        "--tmux-session",
        entry.session,
        "--restore",
        "--no-attach",
        "--",
        *entry.command,
    ]


async def run_child(
    argv: Sequence[str], *, env: Mapping[str, str], cwd: str, timeout: float
) -> tuple[int | None, str]:
    """Run one child in its own process group; return its exit code and its output.

    The code is ``None`` when the child gave no answer within ``timeout``; its
    process group is then ended, ``SIGTERM`` first.
    """
    process = await asyncio.create_subprocess_exec(
        *argv,
        env=dict(env),
        cwd=cwd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout)
    except TimeoutError:
        await _end_group(process)
        return None, f"no answer within {timeout:g} s; its process group was ended"
    return process.returncode, output.decode("utf-8", errors="replace").strip()


async def _end_group(process: asyncio.subprocess.Process) -> None:
    """End the child's whole process group: ``SIGTERM``, then ``SIGKILL`` for what is left.

    The child may end at once while a process it started ignores ``SIGTERM``;
    the group is killed all the same.
    """
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(process.wait(), END_GRACE_SECONDS)
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    await process.wait()


@dataclass(frozen=True)
class Outcome:
    """What one seat's start came to; ``code`` is the child's exit code, if it ran."""

    identity: str
    row_id: str
    state: str
    code: int | None = None
    detail: str = ""
    new_row_id: str | None = None


def outcome_state(code: int | None) -> str:
    """Return the outcome's name for a child's exit code."""
    if code == 0:
        return "started"
    if code == SEAT_LIVE_EXIT_CODE:
        return "live"
    if code == AGENT_START_FAILED_EXIT_CODE:
        return "failed"
    return "timeout" if code is None else "stopped"


GOES_ON = frozenset({"started", "live", "failed", "changed"})
"""Outcomes after which the run goes on to the next seat."""


def record_outcome(directory: Path, entry: Entry, outcome: Outcome) -> str | None:
    """Append the outcome to the old row as ``observe {restore}``; return a problem, if any."""
    failures = entry.failures + 1 if outcome.state == "failed" else 0
    event = {
        "event": "observe",
        "row_id": entry.row_id,
        "restore": {
            "state": outcome.state,
            "code": outcome.code,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "failures": failures,
            "new_row_id": outcome.new_row_id,
        },
    }
    try:
        append_event(event, directory=directory, wait=COLLECTOR_LOCK_WAIT_SECONDS)
    except LedgerError as exc:
        return str(exc)
    return None


async def find_new_row(
    directory: Path,
    identity: str,
    before: frozenset[str],
    *,
    wait: float = NEW_ROW_WAIT_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> str | None:
    """Return the row seat-hold started for ``identity`` after ``before``, once it is seen."""
    deadline = clock() + wait
    while True:
        for key, row in fold(read_events(directory)).items():
            if (
                key not in before
                and row.start.get("identity") == identity
                and row.start.get("launcher") == "seat-hold"
            ):
                return key
        if clock() >= deadline:
            return None
        await sleep(0.5)


def recheck(
    entry: Entry,
    *,
    snapshot: Callable[[], tuple[Mapping[str, Row], Collection[str]]],
    tmux_taken: Callable[[str], bool],
) -> str | None:
    """Return why a planned seat must not start now, from a fresh snapshot, or ``None``."""
    rows, live = snapshot()
    if entry.identity in live:
        return "the seat runs now"
    last = last_rows(rows.values()).get(entry.identity)
    if last is None or last.row_id != entry.row_id:
        return "the identity has a newer row"
    if tmux_taken(entry.session):
        return f"tmux session {entry.session} exists now"
    return None


class Grace(Protocol):
    """The hub grace a run waits for before each start.

    :class:`synapse_channel.restore_grace.HubGrace` in production.
    """

    async def ready(self) -> None:
        """Return once a start may go, or raise :class:`HubGraceError`."""

    async def close(self) -> None:
        """Close the run's own connection."""


Spawn = Callable[..., Awaitable[tuple[int | None, str]]]
"""Runs one child; :func:`run_child` in production."""


@dataclass
class Run:
    """What an executing restore uses; every boundary is injectable."""

    directory: Path
    environ: Mapping[str, str]
    uri: str
    synapse_bin: str
    tmux_bin: str
    limits: Limits
    grace: Grace
    token: str | None
    read: Callable[[], Reading]
    recheck: Callable[[Entry], str | None]
    report: Callable[[str], None]
    spawn: Spawn = run_child
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    child_timeout: float = CHILD_TIMEOUT_SECONDS


async def start_seat(entry: Entry, run: Run) -> Outcome:
    """Start one seat through its child, and return what came of it."""
    env, _dropped = child_environ(run.environ, entry, run.token)
    argv = child_argv(entry, synapse_bin=run.synapse_bin, uri=run.uri, tmux_bin=run.tmux_bin)
    before = frozenset(fold(read_events(run.directory)))
    code, output = await run.spawn(argv, env=env, cwd=entry.cwd, timeout=run.child_timeout)
    state = outcome_state(code)
    new_row = (
        await find_new_row(run.directory, entry.identity, before, clock=run.clock, sleep=run.sleep)
        if code == 0
        else None
    )
    detail = output.splitlines()[-1] if output else ""
    return Outcome(entry.identity, entry.row_id, state, code, detail, new_row)


async def execute(entries: Sequence[Entry], run: Run) -> tuple[int, list[Outcome]]:
    """Start every entry that starts, one at a time; return the run's exit code and outcomes."""
    outcomes: list[Outcome] = []
    last_start: float | None = None
    try:
        for entry in (item for item in entries if item.state == START):
            short = await wait_for_headroom(
                run.limits, read=run.read, clock=run.clock, sleep=run.sleep, last_start=last_start
            )
            if short is not None:
                run.report(f"no headroom on this host in time ({short}); the run stops")
                return NO_HEADROOM_EXIT_CODE, outcomes
            await run.grace.ready()
            changed = run.recheck(entry)
            last_start = run.clock()
            outcome = (
                Outcome(entry.identity, entry.row_id, "changed", detail=changed)
                if changed
                else await start_seat(entry, run)
            )
            outcomes.append(outcome)
            problem = record_outcome(run.directory, entry, outcome)
            run.report(_line(outcome) + (f" (not recorded: {problem})" if problem else ""))
            if outcome.state not in GOES_ON:
                return outcome.code or TIMEOUT_EXIT_CODE, outcomes
    except HubGraceError as stop:
        run.report(f"{stop}; the run stops")
        return stop.exit_code, outcomes
    except (CollectError, LedgerError) as exc:
        run.report(f"the host could not be read or recorded ({exc}); the run stops")
        return HOST_UNREADABLE_EXIT_CODE, outcomes
    finally:
        await run.grace.close()
    return 0, outcomes


def _line(outcome: Outcome) -> str:
    code = "-" if outcome.code is None else str(outcome.code)
    new = f" new row {outcome.new_row_id}" if outcome.new_row_id else ""
    detail = f": {outcome.detail}" if outcome.detail else ""
    return f"{outcome.identity}  {outcome.state} ({code}){new}{detail}"
