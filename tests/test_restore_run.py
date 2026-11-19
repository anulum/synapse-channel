# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests: the restore run starts planned seats one at a time
"""The restore run: one planner at a time, each seat's child, its outcome, and when to stop."""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.restore_grace import HubGraceError
from synapse_channel.restore_pacing import GIB, Limits, Reading
from synapse_channel.restore_plan import START, Entry
from synapse_channel.restore_run import (
    CHILD_NOT_STARTED_EXIT_CODE,
    CHILD_TIMEOUT_SECONDS,
    CLEARANCE_ROUNDS,
    ENV_PASSED,
    HOST_UNREADABLE_EXIT_CODE,
    INTERRUPTED_EXIT_CODE,
    NO_HEADROOM_EXIT_CODE,
    TIMEOUT_EXIT_CODE,
    Interrupt,
    Outcome,
    RestoreBusyError,
    Run,
    child_argv,
    child_environ,
    execute,
    find_new_row,
    outcome_state,
    recheck,
    record_outcome,
    restore_lock,
    run_child,
)
from synapse_channel.seat_restore import MARKER_WAIT_SECONDS
from synapse_channel.session_ledger import LedgerError, Row, append_event, fold, read_events
from synapse_channel.session_ledger_collect import CollectError

SID = "11111111-2222-3333-4444-555555555555"
ROOMY = Reading(32 * GIB, 64 * GIB, 0.0, 8, None)


def _entry(identity: str = "P/a", **changes: Any) -> Entry:
    entry = Entry(
        identity=identity,
        row_id=f"boot-prev:{identity}:7",
        state=START,
        vendor="claude",
        project=identity.partition("/")[0],
        command=("claude", "--resume", SID),
        cwd="/work",
        session=f"synapse-{identity.replace('/', '_')}",
    )
    return replace(entry, **changes)


# --- one planner at a time -----------------------------------------------------------


def test_a_second_run_finds_the_lock_held_and_stops_at_once(tmp_path: Path) -> None:
    with restore_lock(tmp_path):
        with pytest.raises(RestoreBusyError, match="another restore run holds the lock") as busy:
            with restore_lock(tmp_path):
                pytest.fail("two runs held the lock")
        assert busy.value.code == "restore_busy"
    with restore_lock(tmp_path):  # released with the first run
        assert (tmp_path / "restore.lock").stat().st_mode & 0o777 == 0o600


# --- the child -----------------------------------------------------------------------


def test_the_child_gets_only_the_listed_names_and_its_rows_identity(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    coordination = tmp_path / ".coordination" / "state"
    environ = {
        "PATH": "/usr/bin",
        "HOME": "/home/u",
        "LANG": "C.UTF-8",
        "SYN_HOME": str(coordination),
        "SYN_IDENTITY": "user/terminal-1",  # the planner's own, replaced by the row's
        "SECRET_API_KEY": "never-passed",
        "TMUX": "/tmp/tmux-1000/default,1,0",
        "XDG_RUNTIME_DIR": "/run/user/1000",
        "XDG_DATA_HOME": str(coordination),
        "XDG_STATE_HOME": str(repo / "state"),
        "XDG_CACHE_HOME": "relative/cache",
        "TMPDIR": "",
    }
    env, dropped = child_environ(environ, _entry("P/a"), "tok")
    assert env == {
        "PATH": "/usr/bin",
        "HOME": "/home/u",
        "LANG": "C.UTF-8",
        "XDG_RUNTIME_DIR": "/run/user/1000",
        "SYNAPSE_TOKEN": "tok",
        "SYN_IDENTITY": "P/a",
        "SYN_PROJECT": "P",
    }
    assert dropped == [
        "SYN_HOME (redirects Synapse state)",
        "XDG_DATA_HOME (in a git work tree or a coordination tree)",
        "XDG_STATE_HOME (in a git work tree or a coordination tree)",
        "XDG_CACHE_HOME (not an absolute path)",
    ]
    assert "SYNAPSE_TOKEN" in ENV_PASSED  # an ambient token passes as it is
    plain, _ = child_environ({"SYNAPSE_TOKEN": "ambient"}, _entry("solo", project=None))
    assert plain == {"SYNAPSE_TOKEN": "ambient", "SYN_IDENTITY": "solo", "SYN_PROJECT": "solo"}


def test_the_child_restores_the_seat_without_attaching_and_never_widens_permissions() -> None:
    argv = child_argv(_entry(), synapse_bin="/bin/synapse", uri="ws://h:1", tmux_bin="tmux")
    assert argv == [
        "/bin/synapse",
        "worker-session",
        "--identity=P/a",
        "--uri=ws://h:1",
        "--tmux-bin=tmux",
        "--synapse-bin=/bin/synapse",
        "--tmux-session=synapse-P_a",
        "--restore",
        "--no-attach",
        "--",
        "claude",
        "--resume",
        SID,
    ]


def test_a_value_from_the_ledger_is_never_read_as_an_option() -> None:
    import argparse

    from synapse_channel import cli_services

    odd = _entry("--uri=ws://elsewhere:1", session="-x")
    argv = child_argv(odd, synapse_bin="synapse", uri="ws://h:1", tmux_bin="tmux")
    parser = argparse.ArgumentParser()
    cli_services.add_parsers(parser.add_subparsers())
    args = parser.parse_args(argv[1:])
    assert (args.identity, args.uri, args.tmux_session) == (
        "--uri=ws://elsewhere:1",
        "ws://h:1",
        "-x",
    )
    assert args.restore and args.no_attach


def test_the_child_timeout_outlasts_the_holders_final_word() -> None:
    assert CHILD_TIMEOUT_SECONDS == MARKER_WAIT_SECONDS + 25.0


async def test_a_child_that_cannot_be_started_is_a_named_code(tmp_path: Path) -> None:
    code, detail = await run_child(
        [sys.executable, "-c", "pass"], env={}, cwd=str(tmp_path / "gone"), timeout=30.0
    )
    assert code == CHILD_NOT_STARTED_EXIT_CODE == 127
    assert detail.startswith("the child could not be started: ")


async def test_the_exit_code_and_output_of_a_child_are_returned(tmp_path: Path) -> None:
    script = (
        "import os, sys; print(os.getcwd(), flush=True); "
        "print(os.environ['ONLY'], file=sys.stderr); sys.exit(70)"
    )
    code, output = await run_child(
        [sys.executable, "-c", script], env={"ONLY": "x"}, cwd=str(tmp_path), timeout=30.0
    )
    assert code == 70 and output.splitlines() == [str(tmp_path), "x"]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
async def test_a_child_that_gives_no_answer_has_its_whole_group_ended(tmp_path: Path) -> None:
    pids = tmp_path / "pids"
    marker = f"restore-run-test-{uuid.uuid4().hex}"  # names this test's processes, and no other
    # The child starts a grandchild that ignores SIGTERM, then hangs.
    script = (
        "import os, signal, subprocess, sys, time\n"
        "grand = subprocess.Popen([sys.executable, '-c', "
        "'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(120)', "
        f"{marker!r}])\n"
        f"open({str(pids)!r}, 'w').write(f'{{os.getpid()}} {{grand.pid}}')\n"
        "time.sleep(120)\n"
    )

    def ours(pid: int) -> bool:
        try:
            return marker.encode() in Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            return False

    def ended(pid: int) -> bool:
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        except OSError:
            return True
        return stat.rsplit(")", 1)[1].split()[0] == "Z"

    began = time.monotonic()
    try:
        code, detail = await run_child(
            [sys.executable, "-c", script, marker],
            env=dict(os.environ),
            cwd=str(tmp_path),
            timeout=2.0,
        )
        assert code is None and "its process group was ended" in detail
        child, grand = (int(part) for part in pids.read_text(encoding="utf-8").split())
        # A SIGKILL takes effect once the process is scheduled; a loaded host needs a moment.
        deadline = time.monotonic() + 10.0
        while not (ended(child) and ended(grand)):
            assert time.monotonic() < deadline, "the group was not ended"
            await asyncio.sleep(0.05)
        assert time.monotonic() - began < 30.0
    finally:
        # Whatever the verdict, nothing this test started outlives it.
        if pids.exists():
            for pid in (int(part) for part in pids.read_text(encoding="utf-8").split()):
                if ours(pid):
                    with contextlib.suppress(ProcessLookupError):
                        os.kill(pid, signal.SIGKILL)


@pytest.mark.parametrize(
    ("code", "state"),
    [
        (0, "started"),
        (75, "live"),
        (70, "failed"),
        (69, "stopped"),
        (78, "stopped"),
        (2, "stopped"),
        (None, "timeout"),
    ],
)
def test_each_exit_code_names_its_outcome(code: int | None, state: str) -> None:
    assert outcome_state(code) == state


# --- what is recorded ----------------------------------------------------------------


def test_an_outcome_is_recorded_on_the_old_row_with_its_failures_in_a_row(tmp_path: Path) -> None:
    entry = _entry(failures=1)
    failed = Outcome("P/a", entry.row_id, "failed", 70)
    assert record_outcome(tmp_path, entry, failed) is None
    started = Outcome("P/a", entry.row_id, "started", 0, new_row_id="boot-now:9:9")
    assert record_outcome(tmp_path, replace(entry, failures=2), started) is None
    rows = fold(read_events(tmp_path))
    restores = [event["restore"] for event in read_events(tmp_path)]
    assert [(r["state"], r["code"], r["failures"], r["new_row_id"]) for r in restores] == [
        ("failed", 70, 2, None),
        ("started", 0, 0, "boot-now:9:9"),
    ]
    assert rows[entry.row_id].observed["restore"]["new_row_id"] == "boot-now:9:9"


def test_an_outcome_that_cannot_be_recorded_is_reported(tmp_path: Path) -> None:
    blocked = tmp_path / "file"
    blocked.write_text("", encoding="utf-8")
    problem = record_outcome(blocked, _entry(), Outcome("P/a", "r", "live", 75))
    assert problem is not None and "could not be written" in problem


async def test_the_new_row_is_the_holders_start_for_that_identity(tmp_path: Path) -> None:
    def start(row: str, identity: str, launcher: str) -> None:
        event = {"event": "start", "row_id": row, "identity": identity, "launcher": launcher}
        append_event(event, directory=tmp_path, boot="b")

    start("old", "P/a", "seat-hold")
    before = frozenset(fold(read_events(tmp_path)))
    start("other", "P/b", "seat-hold")
    start("observed", "P/a", "observed")
    start("new", "P/a", "seat-hold")
    assert await find_new_row(tmp_path, "P/a", before) == "new"


async def test_a_new_row_not_seen_in_time_is_none(tmp_path: Path) -> None:
    clock = _Clock()
    found = await find_new_row(
        tmp_path, "P/a", frozenset(), wait=1.2, clock=clock, sleep=clock.sleep
    )
    assert found is None and clock.slept == [0.5, 0.5, 0.5]


def _rows(*pairs: tuple[str, str]) -> dict[str, Row]:
    return {key: Row(key, start={"identity": identity}) for key, identity in pairs}


@pytest.mark.parametrize(
    ("rows", "live", "taken", "why"),
    [
        ([("r1", "P/a")], set(), set(), None),
        ([("r1", "P/a")], {"P/a"}, set(), "the seat runs now"),
        ([("r1", "P/a"), ("r2", "P/a")], set(), set(), "the identity has a newer row"),
        ([], set(), set(), "the identity has a newer row"),
        ([("r1", "P/a")], set(), {"synapse-P_a"}, "tmux session synapse-P_a exists now"),
    ],
)
def test_a_fresh_snapshot_before_each_start_can_hold_it_back(
    rows: list[tuple[str, str]], live: set[str], taken: set[str], why: str | None
) -> None:
    entry = _entry(row_id="r1")
    found = recheck(
        entry,
        snapshot=lambda: (_rows(*rows), live),
        tmux_taken=taken.__contains__,
        cwd_exists=lambda _cwd: True,
    )
    assert found == why


def test_a_working_directory_gone_since_the_plan_holds_the_start_back(tmp_path: Path) -> None:
    def never(*_args: object) -> Any:
        raise AssertionError("nothing more is read for a seat that cannot start")

    gone = _entry(cwd=str(tmp_path / "gone"))
    assert recheck(gone, snapshot=never, tmux_taken=never) == "its working directory is gone"
    assert recheck(_entry(cwd=None), snapshot=never, tmux_taken=never) == (
        "its working directory is gone"
    )


# --- the run -------------------------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class _Grace:
    def __init__(self, stop_at: int | None = None) -> None:
        self.calls, self.closed, self.stop_at = 0, 0, stop_at

    async def ready(self) -> None:
        self.calls += 1
        if self.calls == self.stop_at:
            raise HubGraceError(69, "the hub changed (hub-b)")

    async def close(self) -> None:
        self.closed += 1


class _Spawn:
    def __init__(self, codes: Mapping[str, int | None], directory: Path) -> None:
        self.codes, self.directory = codes, directory
        self.calls: list[tuple[Sequence[str], dict[str, str], str]] = []

    async def __call__(
        self, argv: Sequence[str], *, env: dict[str, str], cwd: str, timeout: float
    ) -> tuple[int | None, str]:
        identity = env["SYN_IDENTITY"]
        self.calls.append((argv, env, cwd))
        code = self.codes[identity]
        if code == 0:  # the holder records the new row
            event = {
                "event": "start",
                "row_id": f"new-{identity}",
                "identity": identity,
                "launcher": "seat-hold",
            }
            append_event(event, directory=self.directory, boot="b")
        return code, f"worker-session --restore: line one\nlast line for {identity}"


def _run(
    tmp_path: Path,
    codes: Mapping[str, int | None],
    *,
    grace: _Grace | None = None,
    read: Any = None,
    held_back: Mapping[str, str] | None = None,
    limits: Limits | None = None,
) -> tuple[Run, list[str], _Spawn, _Clock, _Grace]:
    lines: list[str] = []
    clock, spawn, held = _Clock(), _Spawn(codes, tmp_path), grace or _Grace()
    run = Run(
        directory=tmp_path,
        environ={"PATH": "/usr/bin"},
        uri="ws://h:1",
        synapse_bin="/bin/synapse",
        tmux_bin="tmux",
        limits=limits or Limits(),
        grace=held,
        token=None,
        read=read or (lambda: ROOMY),
        recheck=lambda entry: (held_back or {}).get(entry.identity),
        report=lines.append,
        spawn=spawn,
        clock=clock,
        sleep=clock.sleep,
    )
    return run, lines, spawn, clock, held


async def test_seats_start_one_at_a_time_and_the_run_goes_on_past_live_and_failed(
    tmp_path: Path,
) -> None:
    entries = [_entry("P/a"), _entry("P/b"), _entry("P/c"), _entry("P/skip", state="ended")]
    run, lines, spawn, clock, grace = _run(tmp_path, {"P/a": 0, "P/b": 75, "P/c": 70})
    code, outcomes = await execute(entries, run)
    assert code == 0
    assert [(o.identity, o.state, o.code, o.new_row_id) for o in outcomes] == [
        ("P/a", "started", 0, "new-P/a"),
        ("P/b", "live", 75, None),
        ("P/c", "failed", 70, None),
    ]
    assert outcomes[0].detail == "last line for P/a"
    assert [call[2] for call in spawn.calls] == ["/work"] * 3
    assert clock.slept == [30.0, 30.0]  # the spacing between starts
    # The hub is asked before the headroom wait and again just before each start.
    assert grace.calls == 6 and grace.closed == 1
    assert lines[0] == "P/a  started (0) new row new-P/a: last line for P/a"
    recorded = [event["restore"]["state"] for event in read_events(tmp_path) if "restore" in event]
    assert recorded == ["started", "live", "failed"]


@pytest.mark.parametrize(
    ("child", "run_code"), [(69, 69), (78, 78), (2, 2), (None, TIMEOUT_EXIT_CODE)]
)
async def test_a_stopping_outcome_stops_the_run_before_the_next_seat(
    tmp_path: Path, child: int | None, run_code: int
) -> None:
    run, _lines, spawn, _clock, grace = _run(tmp_path, {"P/a": child, "P/b": 0})
    code, outcomes = await execute([_entry("P/a"), _entry("P/b")], run)
    assert code == run_code and [o.identity for o in outcomes] == ["P/a"]
    assert len(spawn.calls) == 1 and grace.closed == 1


async def test_a_seat_that_changed_since_the_plan_is_skipped_and_recorded(tmp_path: Path) -> None:
    run, lines, spawn, _clock, _grace = _run(
        tmp_path, {"P/b": 0}, held_back={"P/a": "the seat runs now"}
    )
    code, outcomes = await execute([_entry("P/a"), _entry("P/b")], run)
    assert code == 0 and [o.state for o in outcomes] == ["changed", "started"]
    assert [call[1]["SYN_IDENTITY"] for call in spawn.calls] == ["P/b"]
    assert lines[0] == "P/a  changed (-): the seat runs now"


async def test_no_headroom_in_time_stops_the_run_before_any_start(tmp_path: Path) -> None:
    tight = Reading(GIB, 64 * GIB, 0.0, 8, None)
    run, lines, spawn, _clock, grace = _run(
        tmp_path, {"P/a": 0}, read=lambda: tight, limits=Limits(timeout_seconds=30.0)
    )
    code, outcomes = await execute([_entry("P/a")], run)
    assert (code, outcomes, spawn.calls) == (NO_HEADROOM_EXIT_CODE, [], [])
    assert lines == [
        "no headroom on this host in time (memory available 1.0 GiB is below 9.6 GiB); "
        "the run stops"
    ]
    assert grace.calls == 1 and grace.closed == 1


async def test_the_hub_grace_can_stop_the_run_between_starts(tmp_path: Path) -> None:
    # Each start asks the hub twice, so the third answer is the second seat's first.
    run, lines, spawn, _clock, grace = _run(tmp_path, {"P/a": 0, "P/b": 0}, grace=_Grace(stop_at=3))
    code, outcomes = await execute([_entry("P/a"), _entry("P/b")], run)
    assert code == 69 and [o.identity for o in outcomes] == ["P/a"] and len(spawn.calls) == 1
    assert lines[-1] == "the hub changed (hub-b); the run stops"
    assert grace.closed == 1


@pytest.mark.parametrize(
    "failure",
    [
        CollectError("tmux list-panes failed"),
        LedgerError("locked"),
        OSError("ledger unreadable"),
        ValueError("/proc/meminfo names no MemTotal or MemAvailable"),
    ],
)
async def test_a_snapshot_that_fails_before_a_start_stops_the_run(
    tmp_path: Path, failure: Exception
) -> None:
    run, lines, spawn, _clock, _grace = _run(tmp_path, {"P/a": 0})

    def failing(_entry: Entry) -> str | None:
        raise failure

    run.recheck = failing
    code, _outcomes = await execute([_entry("P/a")], run)
    assert code == HOST_UNREADABLE_EXIT_CODE and spawn.calls == []
    assert lines == [f"the host could not be read or recorded ({failure}); the run stops"]


async def test_a_host_whose_headroom_cannot_be_read_stops_the_run(tmp_path: Path) -> None:
    def unreadable() -> Reading:
        raise ValueError("/proc/meminfo names no MemTotal or MemAvailable")

    run, lines, spawn, _clock, grace = _run(tmp_path, {"P/a": 0}, read=unreadable)
    code, _outcomes = await execute([_entry("P/a")], run)
    assert (code, spawn.calls, grace.calls, grace.closed) == (HOST_UNREADABLE_EXIT_CODE, [], 1, 1)
    assert "names no MemTotal or MemAvailable" in lines[0]


async def test_nothing_to_start_runs_nothing_and_closes_the_connection(tmp_path: Path) -> None:
    run, lines, spawn, _clock, grace = _run(tmp_path, {})
    assert await execute([_entry("P/a", state="live")], run) == (0, [])
    assert (lines, spawn.calls, grace.calls, grace.closed) == ([], [], 0, 1)


async def test_an_outcome_line_says_when_it_could_not_be_recorded(tmp_path: Path) -> None:
    blocked = tmp_path / "file"
    blocked.write_text("", encoding="utf-8")
    run, lines, _spawn, _clock, _grace = _run(tmp_path, {"P/a": 75})
    run.directory = blocked
    code, _outcomes = await execute([_entry("P/a")], run)
    assert code == 0 and lines[0].startswith("P/a  live (75): last line for P/a (not recorded: ")


# --- a fresh reading of the hub and the host before each start --------------------------


class _OrderedGrace(_Grace):
    """Records each answer in ``order``; answers after the first may take ``slow`` seconds."""

    def __init__(self, order: list[str], clock: _Clock, slow: Sequence[float] = ()) -> None:
        super().__init__()
        self.order, self.clock, self.slow = order, clock, list(slow)

    async def ready(self) -> None:
        await super().ready()
        self.order.append("hub")
        if self.calls > 1 and self.slow:
            self.clock.now += self.slow.pop(0)  # a drop: the grace started again


async def test_the_host_is_read_after_the_hubs_grace_and_the_hub_is_asked_last(
    tmp_path: Path,
) -> None:
    order: list[str] = []

    def read() -> Reading:
        order.append("host")
        return ROOMY

    run, _lines, spawn, clock, _grace = _run(tmp_path, {"P/a": 0}, read=read)
    run.grace = _OrderedGrace(order, clock)
    assert (await execute([_entry("P/a")], run))[0] == 0
    assert order == ["hub", "host", "hub"] and len(spawn.calls) == 1


async def test_a_drop_while_the_host_is_read_reads_the_host_again(tmp_path: Path) -> None:
    order: list[str] = []

    def read() -> Reading:
        order.append("host")
        return ROOMY

    run, _lines, spawn, clock, _grace = _run(tmp_path, {"P/a": 0}, read=read)
    run.grace = _OrderedGrace(order, clock, slow=[45.0])
    assert (await execute([_entry("P/a")], run))[0] == 0
    assert order == ["hub", "host", "hub", "host", "hub"] and len(spawn.calls) == 1


async def test_a_hub_that_keeps_dropping_stops_the_run_before_any_start(tmp_path: Path) -> None:
    order: list[str] = []
    run, lines, spawn, clock, _grace = _run(tmp_path, {"P/a": 0})
    run.grace = _OrderedGrace(order, clock, slow=[45.0] * CLEARANCE_ROUNDS)
    assert await execute([_entry("P/a")], run) == (NO_HEADROOM_EXIT_CODE, [])
    assert spawn.calls == [] and order.count("hub") == CLEARANCE_ROUNDS + 1
    assert lines == [
        "no headroom on this host in time (the hub's connection kept dropping while the host "
        "was read); the run stops"
    ]


# --- an interrupted run ------------------------------------------------------------------


def test_a_first_signal_asks_to_stop_and_a_second_forces() -> None:
    interrupt = Interrupt()
    interrupt.note()
    assert interrupt.requested.is_set() and not interrupt.forced.is_set()
    interrupt.note()
    assert interrupt.forced.is_set()


async def test_real_signals_reach_the_interrupt_only_while_it_is_installed() -> None:
    interrupt = Interrupt()
    with interrupt.installed(asyncio.get_running_loop(), signals=(signal.SIGUSR1,)):
        os.kill(os.getpid(), signal.SIGUSR1)
        await asyncio.wait_for(interrupt.requested.wait(), 5.0)
        os.kill(os.getpid(), signal.SIGUSR1)
        await asyncio.wait_for(interrupt.forced.wait(), 5.0)
    assert signal.getsignal(signal.SIGUSR1) == signal.SIG_DFL


async def test_a_first_signal_during_a_start_finishes_that_seat_and_starts_no_other(
    tmp_path: Path,
) -> None:
    run, lines, spawn, _clock, grace = _run(tmp_path, {"P/a": 0, "P/b": 0})
    real = run.spawn

    async def interrupted_meanwhile(*args: Any, **kwargs: Any) -> tuple[int | None, str]:
        run.interrupt.note()  # arrives while the child runs
        return await real(*args, **kwargs)

    run.spawn = interrupted_meanwhile
    code, outcomes = await execute([_entry("P/a"), _entry("P/b")], run)
    assert code == INTERRUPTED_EXIT_CODE
    assert [(o.identity, o.state, o.new_row_id) for o in outcomes] == [
        ("P/a", "started", "new-P/a")
    ]
    assert len(spawn.calls) == 1 and grace.closed == 1
    recorded = [event["restore"]["state"] for event in read_events(tmp_path) if "restore" in event]
    assert recorded == ["started"]
    assert lines[-1] == "interrupted: no further seat is started; the run stops"


async def test_a_signal_before_the_run_starts_nothing_not_even_the_wait(tmp_path: Path) -> None:
    run, lines, spawn, _clock, grace = _run(tmp_path, {"P/a": 0})
    run.interrupt.note()  # noted while the command was still taking its snapshot
    assert await execute([_entry("P/a")], run) == (INTERRUPTED_EXIT_CODE, [])
    # The wait for the hub is never begun, so not even a fast answer can start a seat.
    assert (spawn.calls, grace.calls, grace.closed) == ([], 0, 1)
    assert lines == ["interrupted: no further seat is started; the run stops"]


class _WaitingGrace(_Grace):
    """A grace that never ends, as a hub that keeps dropping would make it."""

    async def ready(self) -> None:
        self.calls += 1
        await asyncio.Event().wait()


async def test_a_first_signal_while_waiting_for_the_hub_starts_nothing(tmp_path: Path) -> None:
    grace = _WaitingGrace()
    run, lines, spawn, _clock, _grace = _run(tmp_path, {"P/a": 0}, grace=grace)
    asyncio.get_running_loop().call_later(0.05, run.interrupt.note)
    assert await asyncio.wait_for(execute([_entry("P/a")], run), 10.0) == (
        INTERRUPTED_EXIT_CODE,
        [],
    )
    assert spawn.calls == [] and grace.closed == 1 and list(read_events(tmp_path)) == []
    assert lines == ["interrupted: no further seat is started; the run stops"]


async def test_the_bound_of_the_child_after_a_first_signal_is_recorded_as_interrupted(
    tmp_path: Path,
) -> None:
    run, _lines, _spawn, _clock, _grace = _run(tmp_path, {"P/a": 0})

    async def no_answer(*_args: Any, **_kwargs: Any) -> tuple[int | None, str]:
        run.interrupt.note()
        return None, "no answer within 60 s; its process group was ended"

    run.spawn = no_answer
    code, outcomes = await execute([_entry("P/a")], run)
    assert code == INTERRUPTED_EXIT_CODE
    assert [(o.state, o.code) for o in outcomes] == [("interrupted", None)]
    recorded = [event["restore"] for event in read_events(tmp_path) if "restore" in event]
    assert [(item["state"], item["code"]) for item in recorded] == [("interrupted", None)]


async def test_a_second_signal_ends_the_seats_whole_group_and_records_interrupted(
    tmp_path: Path,
) -> None:
    pids = tmp_path / "pids"
    marker = f"restore-run-test-{uuid.uuid4().hex}"  # names this test's processes, and no other
    script = (
        "import os, subprocess, sys, time\n"
        "grand = subprocess.Popen([sys.executable, '-c', "
        "'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(120)', "
        f"{marker!r}])\n"
        f"open({str(pids)!r}, 'w').write(f'{{os.getpid()}} {{grand.pid}}')\n"
        "time.sleep(120)\n"
    )
    run, _lines, _spawn, _clock, _grace = _run(tmp_path / "ledger", {"P/a": 0})
    run.directory.mkdir()

    async def hanging_child(*_args: Any, **_kwargs: Any) -> tuple[int | None, str]:
        argv = [sys.executable, "-c", script, marker]
        return await run_child(argv, env=dict(os.environ), cwd=str(tmp_path), timeout=60.0)

    async def two_signals() -> None:
        while not pids.exists() or not pids.read_text(encoding="utf-8").strip():
            await asyncio.sleep(0.05)
        run.interrupt.note()
        run.interrupt.note()

    def alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True

    run.spawn = hanging_child
    signals = asyncio.ensure_future(two_signals())
    try:
        code, outcomes = await asyncio.wait_for(execute([_entry("P/a")], run), 45.0)
        assert code == INTERRUPTED_EXIT_CODE
        assert [(o.state, o.detail) for o in outcomes] == [
            ("interrupted", "a second signal ended its process group")
        ]
        recorded = [e["restore"]["state"] for e in read_events(run.directory) if "restore" in e]
        assert recorded == ["interrupted"]
        group = [int(part) for part in pids.read_text(encoding="utf-8").split()]
        deadline = time.monotonic() + 10.0
        while any(alive(pid) for pid in group):
            assert time.monotonic() < deadline, "the group was not ended"
            await asyncio.sleep(0.05)
    finally:
        signals.cancel()
        if pids.exists():  # whatever the verdict, the child's own group never outlives the test
            leader = int(pids.read_text(encoding="utf-8").split()[0])
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(leader, signal.SIGKILL)
