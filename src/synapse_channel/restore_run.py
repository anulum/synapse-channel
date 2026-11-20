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

Before each start the run waits for the hub's grace, then for headroom, then
asks the hub once more, so the start goes on a fresh reading of both.

**An interrupted run tells the truth.** The first ``SIGINT`` or ``SIGTERM``
starts no further seat: a seat being started is finished within its usual
bound, its real outcome is recorded, and the run exits
:data:`INTERRUPTED_EXIT_CODE`. A second signal, or that bound, ends the child's
process group and records ``interrupted``. Neither leaves a process behind or
a start unrecorded.

Only one run holds ``restore.lock`` in the ledger directory at a time.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import time
from collections.abc import Awaitable, Callable, Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, TypeVar

from synapse_channel.advisory_lock import locked
from synapse_channel.core.errors import SynapseError
from synapse_channel.restore_grace import HubGraceError
from synapse_channel.restore_pacing import Limits, Reading, wait_for_headroom
from synapse_channel.restore_plan import START, Entry, last_rows
from synapse_channel.seat_hold import (
    AGENT_START_FAILED_EXIT_CODE,
    SEAT_LIVE_EXIT_CODE,
)
from synapse_channel.seat_restore import ENV_DROPPED as SEAT_ENV_DROPPED
from synapse_channel.seat_restore import ENV_PASSED as SEAT_ENV_PASSED
from synapse_channel.seat_restore import ENV_PATHS as SEAT_ENV_PATHS
from synapse_channel.seat_restore import MARKER_WAIT_SECONDS, allowed_environ
from synapse_channel.session_ledger import (
    COLLECTOR_LOCK_WAIT_SECONDS,
    LedgerError,
    Row,
    append_event,
    fold,
    read_events,
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

CHILD_NOT_STARTED_EXIT_CODE = 127
"""A child that could not be started at all; the run stops."""

HOST_UNREADABLE_EXIT_CODE = 3
"""A snapshot before a start could not read the host or record it; the run stops."""

INTERRUPTED_EXIT_CODE = 130
"""The run was interrupted by a signal (``128 + SIGINT``); no further seat was started."""

FRESH_ANSWER_SECONDS = 10.0
"""The hub's last answer before a start is fresh when it took no longer than this.

A longer one means the connection dropped and the grace started again, so the
headroom reading is taken again before the start.
"""

CLEARANCE_ROUNDS = 3
"""How often headroom is read again because the hub's connection dropped, before the run stops."""

LOCK_NAME = "restore.lock"

ENV_PASSED = (*SEAT_ENV_PASSED, "SYNAPSE_TOKEN")
"""Names passed to a child as they are: the seat's list (the same list the restored pane's
``env -i`` uses) and the hub token, which the child hands to the holder in a private file."""

ENV_PATHS = SEAT_ENV_PATHS
"""Directories passed only when absolute and outside every git work tree and coordination tree."""

ENV_DROPPED = SEAT_ENV_DROPPED
"""Names that redirect Synapse state; always dropped, and named in the plan."""


class RestoreBusyError(SynapseError):
    """Another restore run holds the lock."""

    code = "restore_busy"


@contextlib.contextmanager
def restore_lock(directory: Path) -> Iterator[None]:
    """Hold the restore lock for the whole run, or raise :class:`RestoreBusyError` at once.

    The lock is the shared advisory lock, so this module imports on Windows too.
    """
    with locked(directory / LOCK_NAME, blocking=False, purpose="session ledger") as held:
        if not held:
            raise RestoreBusyError("another restore run holds the lock")
        yield


def child_environ(
    environ: Mapping[str, str], entry: Entry, token: str | None = None
) -> tuple[dict[str, str], list[str]]:
    """Return a seat's child environment, and the names dropped from it with the reason.

    The hub token, when the run has one, reaches the child in ``SYNAPSE_TOKEN``,
    never on its command line. Values are never returned for printing; names are.
    The list is the seat's own (:func:`~synapse_channel.seat_restore.allowed_environ`),
    so the child and the pane it restores pass the same names.
    """
    env, dropped = allowed_environ(environ)
    if "SYNAPSE_TOKEN" in environ:
        env["SYNAPSE_TOKEN"] = environ["SYNAPSE_TOKEN"]
    if token:
        env["SYNAPSE_TOKEN"] = token
    project = entry.project or entry.identity.partition("/")[0]
    env.update({"SYN_IDENTITY": entry.identity, "SYN_PROJECT": project})
    return env, dropped


def child_argv(entry: Entry, *, synapse_bin: str, uri: str, tmux_bin: str) -> list[str]:
    """Return the child command that restores one seat without attaching to it.

    Every value is joined to its option with ``=``, so a value read from the
    ledger can never be taken for an option of its own.
    """
    return [
        synapse_bin,
        "worker-session",
        f"--identity={entry.identity}",
        f"--uri={uri}",
        f"--tmux-bin={tmux_bin}",
        f"--synapse-bin={synapse_bin}",
        f"--tmux-session={entry.session}",
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
    process group is then ended, ``SIGTERM`` first. The group is ended the same
    way when the wait is cancelled. A child that cannot be started at all gives
    :data:`CHILD_NOT_STARTED_EXIT_CODE`.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            env=dict(env),
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as exc:
        return CHILD_NOT_STARTED_EXIT_CODE, f"the child could not be started: {exc.strerror or exc}"
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout)
    except TimeoutError:
        await _end_group(process)
        return None, f"no answer within {timeout:g} s; its process group was ended"
    except asyncio.CancelledError:
        await _end_group(process)  # a cancelled start never leaves its child behind
        raise
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
    cwd_exists: Callable[[str], bool] = os.path.isdir,
) -> str | None:
    """Return why a planned seat must not start now, from a fresh snapshot, or ``None``."""
    if entry.cwd is None or not cwd_exists(entry.cwd):
        return "its working directory is gone"
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

_T = TypeVar("_T")


class Interrupt:
    """The signals a run notes: the first stops new starts, the second ends the one in flight."""

    def __init__(self) -> None:
        self.requested = asyncio.Event()
        self.forced = asyncio.Event()

    def note(self) -> None:
        """Note one ``SIGINT`` or ``SIGTERM``."""
        if self.requested.is_set():
            self.forced.set()
        self.requested.set()

    @contextlib.contextmanager
    def installed(
        self,
        loop: asyncio.AbstractEventLoop,
        signals: Sequence[signal.Signals] = (signal.SIGINT, signal.SIGTERM),
    ) -> Iterator[Interrupt]:
        """Route ``signals`` on ``loop`` to :meth:`note` until the block ends."""
        for number in signals:
            loop.add_signal_handler(number, self.note)
        try:
            yield self
        finally:
            for number in signals:
                loop.remove_signal_handler(number)


async def _unless(event: asyncio.Event, awaitable: Awaitable[_T]) -> tuple[bool, _T | None]:
    """Await ``awaitable`` unless ``event`` is set first; then cancel it.

    Return ``(False, result)`` when it finished, ``(True, None)`` when the event
    came first. Its own exception is raised as it is.
    """
    task = asyncio.ensure_future(awaitable)
    if event.is_set():
        task.cancel()
    stop = asyncio.ensure_future(event.wait())
    try:
        await asyncio.wait({task, stop}, return_when=asyncio.FIRST_COMPLETED)
        if task.done() and not task.cancelled():
            return False, task.result()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return True, None
    finally:
        stop.cancel()
        await asyncio.gather(stop, return_exceptions=True)


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
    interrupt: Interrupt = field(default_factory=Interrupt)


async def start_seat(entry: Entry, run: Run) -> Outcome:
    """Start one seat through its child, and return what came of it.

    A second signal while the child runs ends its process group, and so does
    the child's bound once a first signal came: the outcome is ``interrupted``.
    """
    env, _dropped = child_environ(run.environ, entry, run.token)
    argv = child_argv(entry, synapse_bin=run.synapse_bin, uri=run.uri, tmux_bin=run.tmux_bin)
    before = frozenset(fold(read_events(run.directory)))
    forced, answer = await _unless(
        run.interrupt.forced,
        run.spawn(argv, env=env, cwd=entry.cwd, timeout=run.child_timeout),
    )
    if forced or answer is None:
        detail = "a second signal ended its process group"
        return Outcome(entry.identity, entry.row_id, "interrupted", detail=detail)
    code, output = answer
    state = outcome_state(code)
    if state == "timeout" and run.interrupt.requested.is_set():
        state = "interrupted"
    new_row = (
        await find_new_row(run.directory, entry.identity, before, clock=run.clock, sleep=run.sleep)
        if code == 0
        else None
    )
    detail = output.splitlines()[-1] if output else ""
    return Outcome(entry.identity, entry.row_id, state, code, detail, new_row)


async def clearance(run: Run, last_start: float | None) -> str | None:
    """Wait for the hub's grace, then headroom, then the hub's answer; return a shortfall.

    The hub is asked last, so a start goes on a fresh answer. When that answer
    took long, the connection dropped and its grace started again, so headroom
    is read again: at most :data:`CLEARANCE_ROUNDS` times.
    """
    await run.grace.ready()
    for _round in range(CLEARANCE_ROUNDS):
        short = await wait_for_headroom(
            run.limits, read=run.read, clock=run.clock, sleep=run.sleep, last_start=last_start
        )
        if short is not None:
            return short
        asked = run.clock()
        await run.grace.ready()
        if run.clock() - asked <= FRESH_ANSWER_SECONDS:
            return None
    return "the hub's connection kept dropping while the host was read"


async def execute(entries: Sequence[Entry], run: Run) -> tuple[int, list[Outcome]]:
    """Start every entry that starts, one at a time; return the run's exit code and outcomes."""
    outcomes: list[Outcome] = []
    last_start: float | None = None
    stopping = run.interrupt.requested
    try:
        for entry in (item for item in entries if item.state == START):
            interrupted, short = await _unless(stopping, clearance(run, last_start))
            if interrupted:
                break
            if short is not None:
                run.report(f"no headroom on this host in time ({short}); the run stops")
                return NO_HEADROOM_EXIT_CODE, outcomes
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
            if stopping.is_set():
                break
            if outcome.state not in GOES_ON:
                return outcome.code or TIMEOUT_EXIT_CODE, outcomes
    except HubGraceError as stop:
        run.report(f"{stop}; the run stops")
        return stop.exit_code, outcomes
    except (CollectError, LedgerError, OSError, ValueError) as exc:
        run.report(f"the host could not be read or recorded ({exc}); the run stops")
        return HOST_UNREADABLE_EXIT_CODE, outcomes
    finally:
        await run.grace.close()
    if stopping.is_set():
        run.report("interrupted: no further seat is started; the run stops")
        return INTERRUPTED_EXIT_CODE, outcomes
    return 0, outcomes


def _line(outcome: Outcome) -> str:
    code = "-" if outcome.code is None else str(outcome.code)
    new = f" new row {outcome.new_row_id}" if outcome.new_row_id else ""
    detail = f": {outcome.detail}" if outcome.detail else ""
    return f"{outcome.identity}  {outcome.state} ({code}){new}{detail}"
