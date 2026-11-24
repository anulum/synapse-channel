# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the pane waiter run inside the seat holder
"""The seat holder's own pane waiter: output routing, quiet descriptors, the in-process wait.

The in-process wait is checked against the ``synapse wait`` child on the same
behaviour, exit codes 0, 2, 3 and 6, on a real hub. Descriptor switching runs
in a child interpreter, so the test runner's own descriptors are never moved.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel import agent_tmux, cli_messaging_wait, seat_waiter
from synapse_channel.agent_tmux import (
    _wait_command,
    record_waiter_stop,
)
from synapse_channel.agent_tmux_types import AgentTmuxConfig
from synapse_channel.cli_messaging_wait import registered_line
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.pane_bridge_wait import run_probed_wait
from synapse_channel.seat_hold import SeatHolder
from synapse_channel.seat_waiter import (
    WAITER_STOPPED_REFUSAL,
    InProcessWait,
    OutputRouter,
    PaneWaiter,
    QuietTerminal,
    SeatWaiter,
    WakeTarget,
    flush_output,
    install_router,
    open_seat_waiter,
)
from synapse_channel.waiter_identity import pane_waiter_name

IDENTITY = "T/waiter"


def _config(tmp_path: Path, uri: str, **changes: Any) -> AgentTmuxConfig:
    base = AgentTmuxConfig(
        identity=IDENTITY,
        session="synapse-T_waiter",
        cwd=tmp_path,
        agent_command=("claude",),
        uri=uri,
        registry_dir=tmp_path / "registry",
    )
    return replace(base, **changes)


# --- the output router ------------------------------------------------------------


def test_the_router_sends_a_waiter_threads_lines_to_its_capture_and_the_rest_on() -> None:
    target = io.StringIO()
    router = OutputRouter(target)

    def elsewhere() -> None:
        router.write("from another thread\n")

    with router.capture() as captured:
        router.write("from the waiter\n")
        other = threading.Thread(target=elsewhere)
        other.start()
        other.join()
    router.write("after the wait\n")
    router.flush()

    assert captured.getvalue() == "from the waiter\n"
    assert target.getvalue() == "from another thread\nafter the wait\n"
    assert router.isatty() is False


def test_the_router_reports_its_streams_descriptor(tmp_path: Path) -> None:
    with (tmp_path / "out").open("w", encoding="utf-8") as stream:
        assert OutputRouter(stream).fileno() == stream.fileno()


def test_the_router_is_installed_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    first = install_router()
    assert install_router() is first is sys.stdout
    errors = install_router("stderr")
    assert install_router("stderr") is errors is sys.stderr and errors is not first


class _CountingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.flushes = 0

    def emit(self, record: logging.LogRecord) -> None:
        del record

    def flush(self) -> None:
        self.flushes += 1


def test_flushing_reaches_every_logging_handler_and_tolerates_a_closed_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, named = _CountingHandler(), _CountingHandler()
    logging.getLogger().addHandler(root)
    logger = logging.getLogger("synapse.test.seat_waiter")
    logger.addHandler(named)
    closed = io.StringIO()
    closed.close()
    monkeypatch.setattr(sys, "stdout", closed)
    try:
        flush_output()
    finally:
        logging.getLogger().removeHandler(root)
        logger.removeHandler(named)
    assert root.flushes >= 1 and named.flushes >= 1


# --- quiet descriptors, in a child interpreter -------------------------------------

_QUIET_SCRIPT = r"""
import os, pathlib, sys
from synapse_channel.seat_waiter import QuietTerminal, install_router

log, limit, rotate = pathlib.Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3] == "rotate"
stdin_inode = os.fstat(0).st_ino
install_router()
quiet = QuietTerminal(log, limit=limit)
quiet.rotate_if_full()  # not redirected: nothing to do
print("before-the-agent", end="")  # still buffered: only a flush puts it in the pane
quiet.redirect()
quiet.redirect()  # a second call changes nothing
assert quiet.redirected
print("while-the-agent-runs", end="")  # buffered again: only a flush keeps it in the log
print("on-stderr", file=sys.stderr, end="")
if rotate:
    print("x" * 64, flush=True)
    quiet.rotate_if_full()
    print("after-rotation", end="")
quiet.restore()
quiet.restore()
print("after-the-agent")
assert os.fstat(0).st_ino == stdin_inode
"""


def _run_quiet(tmp_path: Path, *, limit: int = 1 << 20, rotate: bool = False) -> tuple[str, Path]:
    pane = tmp_path / "pane"
    log = tmp_path / "seat.log"
    with pane.open("w", encoding="utf-8") as out:
        subprocess.run(
            [
                sys.executable,
                "-c",
                _QUIET_SCRIPT,
                str(log),
                str(limit),
                "rotate" if rotate else "-",
            ],
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=out,
            check=True,
        )
    return pane.read_text(encoding="utf-8"), log


def test_the_holders_output_goes_to_its_log_while_the_agent_runs(tmp_path: Path) -> None:
    pane, log = _run_quiet(tmp_path)
    # Text written just before each switch lands on the side it was written for.
    assert pane == "before-the-agentafter-the-agent\n"
    assert log.read_text(encoding="utf-8") == "while-the-agent-runson-stderr"
    assert stat.S_IMODE(log.stat().st_mode) == 0o600


def test_a_full_log_is_rotated_once(tmp_path: Path) -> None:
    pane, log = _run_quiet(tmp_path, limit=16, rotate=True)
    assert pane == "before-the-agentafter-the-agent\n"
    older = log.with_name(log.name + ".1").read_text(encoding="utf-8")
    # Two streams share the log; their order depends on when each was flushed.
    assert all(part in older for part in ("while-the-agent-runs", "on-stderr", "x" * 64))
    assert log.read_text(encoding="utf-8") == "after-rotation"


# --- the in-process wait against the `synapse wait` child --------------------------


def _synapse_wrapper(tmp_path: Path) -> str:
    wrapper = tmp_path / "synapse"
    wrapper.write_text(
        f"#!/bin/sh\nexec {sys.executable} -c "
        "'from synapse_channel.cli import main; raise SystemExit(main())' \"$@\"\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return str(wrapper)


Executor = Callable[[Sequence[str]], tuple[subprocess.CompletedProcess[str], int]]


def _in_process(probe: Callable[[], int] = lambda: 0) -> tuple[Executor, OutputRouter]:
    router = OutputRouter(io.StringIO())
    run = InProcessWait(router=router, stop=threading.Event())

    def execute(command: Sequence[str]) -> tuple[subprocess.CompletedProcess[str], int]:
        return run(command, env={}, interval=0.2, probe=probe)

    return execute, router


def _child(tmp_path: Path) -> Executor:
    def execute(command: Sequence[str]) -> tuple[subprocess.CompletedProcess[str], int]:
        wrapped = [_synapse_wrapper(tmp_path), *command[1:]]
        return run_probed_wait(wrapped, env=dict(os.environ), interval=0.2, probe=lambda: 0)

    return execute


async def _registered(hub: Any, name: str, *, present: bool = True, within: float = 10.0) -> None:
    deadline = time.monotonic() + within
    while (name in hub.online_agents()) != present:
        assert time.monotonic() < deadline, f"{name} present={not present} after {within} s"
        await asyncio.sleep(0.05)


@pytest.mark.parametrize("kind", ["in-process", "child"])
async def test_both_executors_wake_on_a_directed_message(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with running_hub() as (hub, uri):
        execute, router = _in_process() if kind == "in-process" else (_child(tmp_path), None)
        if router is not None:
            monkeypatch.setattr(sys, "stdout", router)
        command = _wait_command(_config(tmp_path, uri), timeout=30, parent_pid=os.getpid())
        waiting = asyncio.create_task(asyncio.to_thread(execute, command))
        await _registered(hub, pane_waiter_name(IDENTITY))
        sender = await connect_agent("SENDER", uri)
        try:
            await sender.agent.chat("wake up", target=IDENTITY)
            result, refusal = await asyncio.wait_for(waiting, timeout=20)
        finally:
            await close_agents(sender)
    assert (result.returncode, refusal) == (0, 0)
    assert "wake up" in result.stdout


@pytest.mark.parametrize("kind", ["in-process", "child"])
async def test_both_executors_time_out_alike(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with running_hub() as (_hub, uri):
        execute, router = _in_process() if kind == "in-process" else (_child(tmp_path), None)
        if router is not None:
            monkeypatch.setattr(sys, "stdout", router)
        command = _wait_command(_config(tmp_path, uri), timeout=1, parent_pid=os.getpid())
        result, refusal = await asyncio.to_thread(execute, command)
    assert (result.returncode, refusal) == (2, 0)


@pytest.mark.parametrize("kind", ["in-process", "child"])
async def test_both_executors_report_a_dropped_connection_alike(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    execute, router = _in_process() if kind == "in-process" else (_child(tmp_path), None)
    if router is not None:
        monkeypatch.setattr(sys, "stdout", router)
    async with running_hub() as (hub, uri):
        command = _wait_command(_config(tmp_path, uri), timeout=30, parent_pid=os.getpid())
        waiting = asyncio.create_task(asyncio.to_thread(execute, command))
        await _registered(hub, pane_waiter_name(IDENTITY))
    result, refusal = await asyncio.wait_for(waiting, timeout=20)  # the hub has stopped
    assert (result.returncode, refusal) == (3, 0)


@pytest.mark.parametrize("kind", ["in-process", "child"])
async def test_both_executors_refuse_a_live_name_alike(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with running_hub() as (_hub, uri):
        execute, router = _in_process() if kind == "in-process" else (_child(tmp_path), None)
        if router is not None:
            monkeypatch.setattr(sys, "stdout", router)
        live = await connect_agent(pane_waiter_name(IDENTITY), uri)
        try:
            config = _config(tmp_path, uri, first_no_takeover=True)
            command = _wait_command(config, timeout=10, parent_pid=os.getpid())
            result, refusal = await asyncio.to_thread(execute, command)
        finally:
            await close_agents(live)
    assert (result.returncode, refusal) == (6, 0)


async def test_a_refused_probe_ends_the_in_process_wait_and_its_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lost = threading.Event()
    beats: list[float] = []
    router = OutputRouter(io.StringIO())
    run = InProcessWait(router=router, stop=threading.Event())
    monkeypatch.setattr(sys, "stdout", router)
    async with running_hub() as (hub, uri):
        command = _wait_command(_config(tmp_path, uri), timeout=60, parent_pid=os.getpid())
        waiting = asyncio.create_task(
            asyncio.to_thread(
                run,
                command,
                env={},
                interval=0.2,
                probe=lambda: 7 if lost.is_set() else 0,
                heartbeat=lambda: beats.append(time.monotonic()),
            )
        )
        await _registered(hub, pane_waiter_name(IDENTITY))
        lost.set()
        result, refusal = await asyncio.wait_for(waiting, timeout=10)
        await _registered(hub, pane_waiter_name(IDENTITY), present=False)
    assert (result.returncode, result.stdout, refusal) == (2, "", 7)
    assert beats  # the supervisor's heartbeat ran at each probe, as with the child


async def test_a_stop_cancels_the_wait_in_flight_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    router = OutputRouter(io.StringIO())
    monkeypatch.setattr(sys, "stdout", router)
    stop = threading.Event()
    run = InProcessWait(router=router, stop=stop)
    async with running_hub() as (hub, uri):
        command = _wait_command(_config(tmp_path, uri), timeout=60, parent_pid=os.getpid())
        waiting = asyncio.create_task(
            asyncio.to_thread(run, command, env={}, interval=30, probe=lambda: 0)
        )
        await _registered(hub, pane_waiter_name(IDENTITY))
        started = time.monotonic()
        stop.set()
        run.cancel()
        result, refusal = await asyncio.wait_for(waiting, timeout=10)
        assert time.monotonic() - started < 5
        await _registered(hub, pane_waiter_name(IDENTITY), present=False)
    assert (result.returncode, refusal) == (2, WAITER_STOPPED_REFUSAL)
    # Once stopped, a further wait never connects.
    again, refused = run(command, env={}, interval=1, probe=lambda: 0)
    assert (again.returncode, refused) == (2, WAITER_STOPPED_REFUSAL)
    run.cancel()  # nothing in flight: a no-op


async def test_a_stop_noticed_at_a_probe_ends_the_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    router = OutputRouter(io.StringIO())
    monkeypatch.setattr(sys, "stdout", router)
    stop = threading.Event()
    run = InProcessWait(router=router, stop=stop)
    async with running_hub() as (hub, uri):
        command = _wait_command(_config(tmp_path, uri), timeout=60, parent_pid=os.getpid())
        waiting = asyncio.create_task(
            asyncio.to_thread(run, command, env={}, interval=0.2, probe=lambda: 0)
        )
        await _registered(hub, pane_waiter_name(IDENTITY))
        stop.set()  # no cancel: the next probe sees it
        result, refusal = await asyncio.wait_for(waiting, timeout=10)
    assert (result.returncode, refusal) == (2, WAITER_STOPPED_REFUSAL)


def test_each_wait_first_rotates_a_full_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rotations: list[Path] = []
    monkeypatch.setattr(
        QuietTerminal, "rotate_if_full", lambda self: rotations.append(self.log_path)
    )
    stop = threading.Event()
    stop.set()
    quiet = QuietTerminal(tmp_path / "seat.log")
    run = InProcessWait(router=OutputRouter(io.StringIO()), stop=stop, quiet=quiet)
    result, refusal = run(["synapse", "wait"], env={}, interval=1, probe=lambda: 0)
    assert (result.returncode, refusal) == (2, WAITER_STOPPED_REFUSAL)
    assert rotations == [tmp_path / "seat.log"]


def test_a_crashed_in_process_wait_reads_as_a_crashed_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def crash(*_args: Any, **_kwargs: Any) -> int:
        raise RuntimeError("wait crashed")

    monkeypatch.setattr(cli_messaging_wait, "_run_wait_with_reconnect", crash)
    router = OutputRouter(io.StringIO())
    monkeypatch.setattr(sys, "stdout", router)
    run = InProcessWait(router=router, stop=threading.Event())
    command = _wait_command(_config(tmp_path, "ws://127.0.0.1:9"), timeout=5, parent_pid=1)
    result, refusal = run(command, env={}, interval=1, probe=lambda: 0)
    assert (result.returncode, refusal) == (1, 0)
    assert "RuntimeError: wait crashed" in capsys.readouterr().err


async def test_a_stalled_wait_never_delays_the_holders_loop_or_its_seat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wait runs on its own loop in its own thread: the holder's loop keeps time."""

    async def stall(*_args: Any, **_kwargs: Any) -> int:
        time.sleep(2.0)  # a blocking call inside the wait
        return 2

    monkeypatch.setattr(cli_messaging_wait, "_run_wait_with_reconnect", stall)
    router = OutputRouter(io.StringIO())
    monkeypatch.setattr(sys, "stdout", router)
    run = InProcessWait(router=router, stop=threading.Event())
    async with running_hub() as (_hub, uri):
        holder = SeatHolder(IDENTITY, uri=uri, token=None, factory=SynapseAgent)
        assert await holder.acquire(timeout=5.0) == "held"
        try:
            command = _wait_command(_config(tmp_path, uri), timeout=30, parent_pid=os.getpid())
            waiting = asyncio.create_task(
                asyncio.to_thread(run, command, env={}, interval=5, probe=lambda: 0)
            )
            gaps: list[float] = []
            last = time.monotonic()
            while not waiting.done():
                await asyncio.sleep(0.05)
                now = time.monotonic()
                gaps.append(now - last)
                last = now
            result, _refusal = await waiting
            assert result.returncode == 2 and len(gaps) > 10
            assert max(gaps) < 0.5  # the holder's loop never stalled
            second = SeatHolder(IDENTITY, uri=uri, token=None, factory=SynapseAgent)
            assert await second.acquire(timeout=5.0) == "live"
            assert not holder.lost
        finally:
            await holder.release()


def test_the_wait_parser_is_built_once() -> None:
    assert seat_waiter._wait_parser() is seat_waiter._wait_parser()


# --- the waiter thread ---------------------------------------------------------------


class _Records:
    def __init__(self) -> None:
        self.stops: list[tuple[AgentTmuxConfig, str]] = []

    def record(self, config: AgentTmuxConfig, reason: str) -> None:
        self.stops.append((config, reason))


def _waiter(
    tmp_path: Path, wake: Callable[..., int], record: Callable[[AgentTmuxConfig, str], None]
) -> PaneWaiter:
    return PaneWaiter(
        _config(tmp_path, "ws://h"), router=OutputRouter(io.StringIO()), wake=wake, record=record
    )


def _finish(waiter: PaneWaiter) -> None:
    waiter.start()
    waiter._thread.join(10)
    assert not waiter._thread.is_alive()


def test_a_waiter_whose_loop_ends_records_why_and_the_seat_runs_on(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    records = _Records()
    seen: list[object] = []

    def loop(config: AgentTmuxConfig, *, sleeper: Callable[[float], object]) -> int:
        seen.append(agent_tmux._PROBED_WAIT.get())  # waits run in-process here
        return 9

    waiter = _waiter(tmp_path, loop, records.record)
    _finish(waiter)
    assert seen == [waiter._executor]
    assert agent_tmux._PROBED_WAIT.get() is None  # never leaked to this thread
    assert waiter.reason == "the wait loop ended with 9"
    assert records.stops == [(waiter.config, "the wait loop ended with 9")]
    assert (
        "the pane waiter of T/waiter stopped: the wait loop ended with 9" in capsys.readouterr().err
    )


@pytest.mark.parametrize(
    ("fault", "reason"),
    [(RuntimeError("boom"), "RuntimeError: boom"), (SystemExit(2), "SystemExit: 2")],
)
def test_every_fault_of_the_loop_stops_the_waiter_only(
    fault: BaseException, reason: str, tmp_path: Path
) -> None:
    records = _Records()

    def loop(config: AgentTmuxConfig, *, sleeper: Callable[[float], object]) -> int:
        raise fault

    waiter = _waiter(tmp_path, loop, records.record)
    _finish(waiter)
    assert waiter.reason == reason and records.stops == [(waiter.config, reason)]


def test_a_failed_record_stays_inside_the_waiter(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def record(config: AgentTmuxConfig, reason: str) -> None:
        raise OSError("registry is read-only")

    waiter = _waiter(tmp_path, lambda config, *, sleeper: 1, record)
    _finish(waiter)
    assert "the waiter's stop was not recorded: registry is read-only" in capsys.readouterr().err


def test_a_stopped_waiter_ends_at_once_and_records_nothing(tmp_path: Path) -> None:
    records = _Records()

    def loop(config: AgentTmuxConfig, *, sleeper: Callable[[float], object]) -> int:
        sleeper(60)  # the loop's backoff: a stop cuts it short
        return 143

    waiter = _waiter(tmp_path, loop, records.record)
    waiter.start()
    started = time.monotonic()
    assert waiter.stop(timeout=5) is True
    assert time.monotonic() - started < 2
    assert waiter.reason is None and records.stops == []
    assert waiter.stop(timeout=1) is True  # already ended


def test_the_registry_records_a_stopped_waiter(tmp_path: Path) -> None:
    config = _config(tmp_path, "ws://h")
    path = agent_tmux.registry_path(config)
    path.write_text('{"identity": "T/waiter", "pending_wake": false}\n', encoding="utf-8")
    record_waiter_stop(config, "binding refused")
    entry = json.loads(path.read_text(encoding="utf-8"))
    assert entry["identity"] == "T/waiter" and entry["pending_wake"] is False
    assert entry["waiter_stopped"]["reason"] == "binding refused"
    assert entry["waiter_stopped"]["at"].endswith("Z")
    path.write_text("not json", encoding="utf-8")
    record_waiter_stop(config, "again")
    assert json.loads(path.read_text(encoding="utf-8"))["waiter_stopped"]["reason"] == "again"


def test_the_bridge_runs_its_wait_through_the_threads_executor(tmp_path: Path) -> None:
    calls: list[Sequence[str]] = []

    def executor(
        command: Sequence[str],
        *,
        env: Any,
        interval: float,
        probe: Callable[[], int],
        heartbeat: Callable[[], None] | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], int]:
        calls.append(command)
        return subprocess.CompletedProcess(list(command), 2, "", ""), 7

    config = _config(tmp_path, "ws://h", synapse_bin="/nonexistent/synapse")
    with agent_tmux.wait_executor(executor):
        result, refusal = agent_tmux._bridge_wait(config, runner=subprocess.run, heartbeat=None)
    assert (result.returncode, refusal) == (2, 7)
    assert calls and calls[0][1:3] == ["wait", "--name"]
    assert agent_tmux._PROBED_WAIT.get() is None


# --- opening and closing the seat's waiter -----------------------------------------


def test_opening_a_seat_waiter_silences_the_holder_and_starts_a_guarded_waiter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    monkeypatch.setattr(QuietTerminal, "redirect", lambda self: events.append("redirect"))
    monkeypatch.setattr(PaneWaiter, "start", lambda self: events.append("start"))
    monkeypatch.chdir(tmp_path)
    seat = open_seat_waiter(
        WakeTarget("synapse-T_waiter", tmux_bin="/x/tmux", pane_probe_interval=2.5),
        identity=IDENTITY,
        agent_argv=["claude", "--resume", "r"],
        uri="ws://h",
        token="t",
        log_path=tmp_path / "T_waiter.log",
        router=OutputRouter(io.StringIO()),
    )
    config = seat.waiter.config
    assert events == ["redirect", "start"]
    assert config.first_no_takeover is True and config.session == "synapse-T_waiter"
    assert config.agent_command == ("claude", "--resume", "r") and config.cwd == tmp_path
    assert (config.tmux_bin, config.pane_probe_interval, config.token) == ("/x/tmux", 2.5, "t")
    assert seat.quiet.log_path == tmp_path / "T_waiter.log"


def test_closing_gives_the_terminal_back_even_when_the_waiter_will_not_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []

    def stubborn(self: PaneWaiter, timeout: float = 0.0) -> bool:
        events.append("stop")
        raise RuntimeError("will not stop")

    monkeypatch.setattr(PaneWaiter, "stop", stubborn)
    monkeypatch.setattr(QuietTerminal, "restore", lambda self: events.append("restore"))
    waiter = _waiter(tmp_path, lambda config, *, sleeper: 0, _Records().record)
    seat = SeatWaiter(waiter, QuietTerminal(tmp_path / "log"))
    with pytest.raises(RuntimeError):
        seat.close()
    assert events == ["stop", "restore"]


@pytest.mark.parametrize("kind", ["in-process", "child"])
async def test_both_executors_report_a_held_name_with_the_same_line(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A guarded wait proves its hold by one stderr line, whichever executor runs it."""
    execute: Executor = _child(tmp_path)
    if kind == "in-process":
        router, errors = OutputRouter(io.StringIO()), OutputRouter(io.StringIO())
        monkeypatch.setattr(sys, "stdout", router)
        monkeypatch.setattr(sys, "stderr", errors)
        run = InProcessWait(router=router, stop=threading.Event(), errors=errors)

        def in_process(command: Sequence[str]) -> tuple[subprocess.CompletedProcess[str], int]:
            return run(command, env={}, interval=0.2, probe=lambda: 0)

        execute = in_process
    async with running_hub() as (_hub, uri):
        config = _config(tmp_path, uri, first_no_takeover=True)
        command = _wait_command(config, timeout=1, parent_pid=os.getpid())
        result, refusal = await asyncio.to_thread(execute, command)
    assert (result.returncode, refusal) == (2, 0)
    assert result.stderr.splitlines().count(registered_line(pane_waiter_name(IDENTITY))) == 1


def test_a_crashed_waits_trace_goes_to_the_log_not_to_its_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def crash(*_args: Any, **_kwargs: Any) -> int:
        raise RuntimeError("wait crashed")

    monkeypatch.setattr(cli_messaging_wait, "_run_wait_with_reconnect", crash)
    log = io.StringIO()
    router, errors = OutputRouter(io.StringIO()), OutputRouter(log)
    monkeypatch.setattr(sys, "stdout", router)
    monkeypatch.setattr(sys, "stderr", errors)
    run = InProcessWait(router=router, stop=threading.Event(), errors=errors)
    command = _wait_command(_config(tmp_path, "ws://127.0.0.1:9"), timeout=5, parent_pid=1)
    result, refusal = run(command, env={}, interval=1, probe=lambda: 0)
    assert (result.returncode, result.stderr, refusal) == (1, "", 0)
    assert "RuntimeError: wait crashed" in log.getvalue()


def test_a_stopped_waiter_is_also_recorded_in_the_session_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from synapse_channel.session_ledger import LauncherRecord, read_events

    ledger = LauncherRecord("seat-hold", directory=tmp_path / "ledger")
    ledger.row = "b:1:2"
    monkeypatch.setattr(QuietTerminal, "redirect", lambda self: None)
    monkeypatch.setattr(PaneWaiter, "start", lambda self: None)
    monkeypatch.chdir(tmp_path)
    seat = open_seat_waiter(
        WakeTarget("synapse-T_waiter"),
        identity=IDENTITY,
        agent_argv=["claude"],
        uri="ws://h",
        token=None,
        log_path=tmp_path / "T_waiter.log",
        router=OutputRouter(io.StringIO()),
        ledger=ledger,
    )
    config = replace(seat.waiter.config, registry_dir=tmp_path / "registry")
    seat.waiter._record(config, "binding refused")
    (line,) = list(read_events(tmp_path / "ledger"))
    assert (line["event"], line["row_id"]) == ("observe", "b:1:2")
    assert line["waiter"] == {"state": "stopped", "reason": "binding refused"}
    entry = json.loads(agent_tmux.registry_path(config).read_text(encoding="utf-8"))
    assert entry["waiter_stopped"]["reason"] == "binding refused"
    without = open_seat_waiter(
        WakeTarget("synapse-T_waiter"),
        identity=IDENTITY,
        agent_argv=["claude"],
        uri="ws://h",
        token=None,
        log_path=tmp_path / "T_waiter.log",
        router=OutputRouter(io.StringIO()),
    )
    without.waiter._record(config, "no ledger here")  # the registry only
    # Without a ledger only the registry hears of it; the ledger keeps its one line.
    assert len(list(read_events(tmp_path / "ledger"))) == 1
