# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the seat holder: one live holder per seat
"""The seat holder, on a real in-process hub and real child processes.

A seat must never run twice. These tests show that the holder registers its
dedicated name without a takeover, starts the agent only after the hub accepted
it, never displaces a live holder, ends the agent when it dies itself, and
survives a hub restart unless another holder took the name.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import io
import os
import re
import signal
import stat
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Coroutine, Sequence
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import close_agents, connect_agent, free_port, running_hub
from synapse_channel import cli_seat_hold, seat_hold, seat_waiter
from synapse_channel.client.agent_dispatch import MessageCallback
from synapse_channel.core.hub import SynapseHub
from synapse_channel.seat_hold import (
    AGENT_START_FAILED_EXIT_CODE,
    HUB_UNAVAILABLE_EXIT_CODE,
    RESTORE_GRACE_SECONDS,
    SEAT_LIVE_EXIT_CODE,
    UNSUPPORTED_PLATFORM_EXIT_CODE,
    ChildOps,
    ChildSetupError,
    HoldTiming,
    SeatHolder,
    SignalHandler,
    child_setup,
    default_marker_dir,
    hold_seat,
    marker_path,
    seat_name,
    spawn_agent,
    supervise,
    unsupported_platform,
    write_marker,
)
from synapse_channel.seat_waiter import WakeTarget

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="the seat holder's guarantee is Linux-only"
)

FAST = HoldTiming(
    ready_timeout=3.0, reconnect_base=0.05, reconnect_max=0.2, ghost_window=1.0, stop_grace=3.0
)
"""Short waits, so a reconnect and a ghost window fit in a test."""

NONCE = "nonce-0001"


def _marker(directory: Path, identity: str = "T/seat") -> str:
    return marker_path(directory, identity, NONCE).read_text(encoding="utf-8").strip()


def _python(code: str) -> list[str]:
    return [sys.executable, "-c", code]


@contextlib.asynccontextmanager
async def _hub_on(port: int) -> AsyncIterator[str]:
    """Serve a fresh hub on a fixed port, so a test can restart it on the same address."""
    hub = SynapseHub(hub_id="syn-test")
    task = asyncio.create_task(hub.serve("127.0.0.1", port))
    try:
        host, bound = await hub.wait_until_serving()
        yield f"ws://{host}:{bound}"
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def test_names_markers_and_their_directory(tmp_path: Path) -> None:
    assert seat_name("P/claude-1") == "P/claude-1-seat"
    assert marker_path(tmp_path, "P/claude-1", NONCE) == tmp_path / f"P_claude-1.{NONCE}"
    assert default_marker_dir({"XDG_RUNTIME_DIR": "/run/user/7"}) == Path(
        "/run/user/7/synapse-seat-hold"
    )
    assert default_marker_dir({"HOME": str(tmp_path)}).name == "synapse-seat-hold"

    target = tmp_path / "markers" / "one"
    write_marker(target, "ready X-seat n")
    write_marker(target, "refused X-seat n")
    assert target.read_text(encoding="utf-8") == "refused X-seat n\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
    assert [p.name for p in target.parent.iterdir()] == ["one"]


def test_a_platform_without_a_parent_death_signal_is_refused_by_name(tmp_path: Path) -> None:
    assert unsupported_platform("linux") is None
    refusal = unsupported_platform("darwin")
    assert refusal is not None and "darwin" in refusal and "parent-death" in refusal

    async def never(*_args: object) -> Any:
        raise AssertionError("no agent may start")

    code = asyncio.run(
        hold_seat(
            "T/seat",
            NONCE,
            ["true"],
            uri="ws://unused",
            marker_dir=tmp_path,
            factory=_factory(),
            spawner=never,
            platform="darwin",
        )
    )
    assert code == UNSUPPORTED_PLATFORM_EXIT_CODE == 78
    assert _marker(tmp_path) == f"unsupported T/seat-seat {NONCE}"


def test_a_bad_nonce_is_refused_before_anything_happens(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="nonce"):
        asyncio.run(hold_seat("T/seat", "x/y", ["true"], uri="ws://unused", marker_dir=tmp_path))


class _Ops:
    """Records the child's set-up calls; each step can be made to fail."""

    def __init__(self, *, tty: bool, death: int = 0, parent: int = 41, tc_error: bool = False):
        self.calls: list[str] = []
        self._tty, self._death, self._parent, self._tc_error = tty, death, parent, tc_error
        self.ops = ChildOps(
            setpgid=self._setpgid,
            isatty=self._isatty,
            getpgrp=self._getpgrp,
            tcsetpgrp=self._tcsetpgrp,
            set_sigttou=self._sigttou,
            set_death_signal=self._death_signal,
            getppid=self._getppid,
        )

    def _setpgid(self, pid: int, pgid: int) -> None:
        self.calls.append(f"setpgid {pid} {pgid}")

    def _isatty(self, fd: int) -> bool:
        return self._tty

    def _getpgrp(self) -> int:
        return 99

    def _tcsetpgrp(self, fd: int, pgrp: int) -> None:
        self.calls.append(f"tcsetpgrp {fd} {pgrp}")
        if self._tc_error:
            raise OSError("not a controlling terminal")

    def _sigttou(self, handler: SignalHandler) -> SignalHandler:
        self.calls.append(f"sigttou {handler}")
        return signal.SIG_DFL

    def _death_signal(self, signum: int) -> int:
        self.calls.append(f"death {signum}")
        return self._death

    def _getppid(self) -> int:
        return self._parent


@pytest.mark.parametrize("tc_error", [False, True])
def test_the_child_takes_its_own_group_the_terminal_and_a_parent_death_signal(
    tc_error: bool,
) -> None:
    recorder = _Ops(tty=True, tc_error=tc_error)
    child_setup(41, recorder.ops)()
    assert recorder.calls == [
        "setpgid 0 0",
        f"sigttou {signal.SIG_IGN}",
        "tcsetpgrp 0 99",
        f"sigttou {signal.SIG_DFL}",
        f"death {int(signal.SIGTERM)}",
    ]


@pytest.mark.parametrize(
    ("death", "parent", "reason"), [(-1, 41, "parent-death"), (0, 1, "holder ended")]
)
def test_a_child_whose_holder_is_gone_or_unsignalled_never_runs_the_agent(
    death: int, parent: int, reason: str
) -> None:
    recorder = _Ops(tty=False, death=death, parent=parent)
    with pytest.raises(ChildSetupError, match=reason):
        child_setup(41, recorder.ops)()
    assert recorder.calls[0] == "setpgid 0 0"
    assert not any(call.startswith("tcsetpgrp") for call in recorder.calls)


async def test_a_real_child_of_another_holder_is_never_executed() -> None:
    with pytest.raises(subprocess.SubprocessError):
        await spawn_agent(["true"], 1)  # pid 1 is not this test's process: the check refuses


async def test_an_agent_that_cannot_start_ends_the_seat_with_a_failed_marker(
    tmp_path: Path,
) -> None:
    async with running_hub() as (_hub, uri):
        code = await hold_seat(
            "T/seat",
            NONCE,
            [str(tmp_path / "no-such-agent")],
            uri=uri,
            marker_dir=tmp_path,
            timing=FAST,
        )
    assert code == AGENT_START_FAILED_EXIT_CODE == 70
    assert _marker(tmp_path) == f"failed T/seat-seat {NONCE}"


async def test_the_agent_starts_only_after_the_seat_is_held(tmp_path: Path) -> None:
    at_spawn: list[str] = []

    async def spawn_after_reading(argv: Sequence[str], holder_pid: int) -> Any:
        at_spawn.append(_marker(tmp_path))
        return await spawn_agent(argv, holder_pid)

    async with running_hub() as (_hub, uri):
        marker = marker_path(tmp_path, "T/seat", NONCE)
        seen = tmp_path / "seen.txt"
        code = await hold_seat(
            "T/seat",
            NONCE,
            _python(
                f"import pathlib,sys; pathlib.Path({str(seen)!r}).write_text("
                f"pathlib.Path({str(marker)!r}).read_text()); sys.exit(3)"
            ),
            uri=uri,
            marker_dir=tmp_path,
            spawner=spawn_after_reading,
            timing=FAST,
        )
    assert code == 3
    # The hold is on record before the agent is started ...
    assert at_spawn == [f"held T/seat-seat {NONCE}"]
    # ... the agent finds a marker for this start (``ready`` may already be written) ...
    assert seen.read_text(encoding="utf-8").split()[1:] == ["T/seat-seat", NONCE]
    # ... and the final word says it started.
    assert _marker(tmp_path) == f"ready T/seat-seat {NONCE}"


async def test_a_live_holder_is_never_displaced_and_nothing_starts(tmp_path: Path) -> None:
    started: list[Sequence[str]] = []

    async def record(argv: Sequence[str], _pid: int) -> Any:
        started.append(argv)
        raise AssertionError("no agent may start")

    async with running_hub() as (_hub, uri):
        live = await connect_agent("T/seat-seat", uri)
        try:
            code = await hold_seat(
                "T/seat", NONCE, ["true"], uri=uri, marker_dir=tmp_path, spawner=record, timing=FAST
            )
            assert live.agent.running  # the live holder kept its name
        finally:
            await close_agents(live)
    assert code == SEAT_LIVE_EXIT_CODE == 75
    assert started == []
    assert _marker(tmp_path) == f"refused T/seat-seat {NONCE}"


async def test_an_unreachable_hub_starts_nothing(tmp_path: Path) -> None:
    code = await hold_seat(
        "T/seat",
        NONCE,
        ["true"],
        uri=f"ws://127.0.0.1:{free_port()}",
        marker_dir=tmp_path,
        timing=FAST,
    )
    assert code == HUB_UNAVAILABLE_EXIT_CODE == 69
    assert _marker(tmp_path) == f"unavailable T/seat-seat {NONCE}"


async def test_a_stop_ends_the_agents_whole_process_group(tmp_path: Path) -> None:
    pids = tmp_path / "pids.txt"
    code = (
        "import os, subprocess, time, pathlib\n"
        "grand = subprocess.Popen(['sleep', '60'])\n"
        f"pathlib.Path({str(pids)!r}).write_text(f'{{os.getpid()}} {{grand.pid}}')\n"
        "time.sleep(60)\n"
    )
    async with running_hub() as (_hub, uri):
        holder = SeatHolder("T/seat", uri=uri, token=None, factory=_factory())
        assert await holder.acquire(timeout=FAST.ready_timeout) == "held"
        child = await spawn_agent(_python(code), os.getpid())
        stop = asyncio.Event()
        try:
            await _until(pids.exists)
            agent_pid, grandchild_pid = (int(p) for p in pids.read_text().split())
            asyncio.get_running_loop().call_later(0.1, stop.set)
            status = await supervise(holder, child, stop=stop, timing=FAST)
        finally:
            await holder.release()
    assert status == 128 + signal.SIGTERM
    await _until(lambda: not _alive(grandchild_pid))
    assert not _alive(agent_pid)


async def test_the_agents_own_exit_ends_its_leftover_group(tmp_path: Path) -> None:
    pids = tmp_path / "pids.txt"
    code = (
        "import os, subprocess, pathlib\n"
        "grand = subprocess.Popen(['sleep', '60'])\n"
        f"pathlib.Path({str(pids)!r}).write_text(str(grand.pid))\n"
    )
    async with running_hub() as (_hub, uri):
        holder = SeatHolder("T/seat", uri=uri, token=None, factory=_factory())
        assert await holder.acquire(timeout=FAST.ready_timeout) == "held"
        child = await spawn_agent(_python(code), os.getpid())
        try:
            status = await supervise(holder, child, stop=asyncio.Event(), timing=FAST)
        finally:
            await holder.release()
    assert status == 0
    grandchild = int(pids.read_text())
    await _until(lambda: not _alive(grandchild))


async def test_a_hub_restart_keeps_the_seat_and_registers_again_without_takeover(
    tmp_path: Path,
) -> None:
    port = free_port()
    async with _hub_on(port) as uri:
        holder = SeatHolder("T/seat", uri=uri, token=None, factory=_factory())
        assert await holder.acquire(timeout=FAST.ready_timeout) == "held"
    child = await spawn_agent(["sleep", "30"], os.getpid())
    stop = asyncio.Event()
    async with _hub_on(port) as uri:
        run = asyncio.create_task(supervise(holder, child, stop=stop, timing=FAST))
        observer = await connect_agent("OBSERVER", uri)
        try:
            await _until(lambda: not holder.lost, timeout=10.0)
            assert child.returncode is None  # the agent never noticed
        finally:
            stop.set()
            await run
            await holder.release()
            await close_agents(observer)


async def test_a_name_still_held_after_the_ghost_window_ends_the_seat(tmp_path: Path) -> None:
    port = free_port()
    async with _hub_on(port) as uri:
        holder = SeatHolder("T/seat", uri=uri, token=None, factory=_factory())
        assert await holder.acquire(timeout=FAST.ready_timeout) == "held"
    child = await spawn_agent(["sleep", "30"], os.getpid())
    async with _hub_on(port) as uri:
        # Another live holder took the name while this one was away.
        other = await connect_agent("T/seat-seat", uri)
        try:
            status = await asyncio.wait_for(
                supervise(holder, child, stop=asyncio.Event(), timing=FAST), timeout=20.0
            )
        finally:
            if child.returncode is None:
                await seat_hold.stop_agent(child, grace=1.0)
            await holder.release()
            await close_agents(other)
    assert status == SEAT_LIVE_EXIT_CODE
    assert child.returncode is not None


def test_a_holder_killed_outright_takes_its_agent_with_it(tmp_path: Path) -> None:
    """The fence's last guard: no agent outlives its holder, even after SIGKILL."""
    pid_file = tmp_path / "agent.pid"

    async def scenario() -> None:
        async with running_hub() as (_hub, uri):
            holder = await asyncio.create_subprocess_exec(
                *_synapse(),
                "seat-hold",
                "--identity",
                "T/seat",
                "--nonce",
                NONCE,
                "--marker-dir",
                str(tmp_path),
                "--uri",
                uri,
                "--",
                *_python(
                    f"import os,pathlib,time; pathlib.Path({str(pid_file)!r})"
                    ".write_text(str(os.getpid())); time.sleep(60)"
                ),
            )
            try:
                await _until(pid_file.exists, timeout=15.0)
                agent = int(pid_file.read_text())
                holder.kill()
                await holder.wait()
                await _until(lambda: not _alive(agent), timeout=5.0)
            finally:
                if holder.returncode is None:
                    holder.kill()
                    await holder.wait()

    asyncio.run(scenario())


def test_the_command_needs_an_agent_and_reads_its_token_from_a_file(tmp_path: Path) -> None:
    captured: list[Coroutine[Any, Any, int]] = []

    def runner(coroutine: Coroutine[Any, Any, int]) -> int:
        captured.append(coroutine)
        coroutine.close()
        return 0

    base = {"identity": "T/seat", "nonce": NONCE, "marker_dir": None, "uri": "ws://h"}
    empty = argparse.Namespace(**base, token_file=None, command=["--"])
    assert cli_seat_hold._cmd_seat_hold(empty, runner=runner) == cli_seat_hold.USAGE_EXIT_CODE
    token = tmp_path / "token"
    token.write_text("secret\n", encoding="utf-8")
    full = argparse.Namespace(
        **{**base, "marker_dir": str(tmp_path)}, token_file=str(token), command=["--", "true"]
    )
    assert cli_seat_hold._cmd_seat_hold(full, runner=runner) == 0
    assert len(captured) == 1


def test_the_restore_grace_covers_a_holders_reconnect() -> None:
    assert RESTORE_GRACE_SECONDS == seat_hold.RECONNECT_MAX_SECONDS + seat_hold.GHOST_WINDOW_SECONDS


# --- helpers ----------------------------------------------------------------------


def _factory() -> Any:
    from synapse_channel.client.agent import SynapseAgent

    return SynapseAgent


def _synapse() -> list[str]:
    return [sys.executable, "-c", "from synapse_channel.cli import main; raise SystemExit(main())"]


def _alive(pid: int) -> bool:
    try:
        stat_line = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return stat_line.rsplit(")", 1)[1].split()[0] != "Z"


async def _until(predicate: Any, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.05)


def test_the_real_child_operations_work_in_this_process() -> None:
    assert seat_hold.child_ops().set_death_signal(0) == 0  # 0 clears the parent-death signal
    previous = seat_hold._set_sigttou(signal.SIG_DFL)
    seat_hold._set_sigttou(previous)


@pytest.mark.parametrize("tty", [True, False])
def test_the_terminal_returns_to_the_holder_only_on_a_terminal(tty: bool) -> None:
    recorder = _Ops(tty=tty)
    seat_hold.reclaim_terminal(recorder.ops)
    expected = [f"sigttou {signal.SIG_IGN}", "tcsetpgrp 0 99", f"sigttou {signal.SIG_DFL}"]
    assert recorder.calls == (expected if tty else [])


async def test_releasing_a_holder_that_holds_nothing_is_harmless() -> None:
    holder = SeatHolder("T/seat", uri="ws://unused", token=None, factory=_factory())
    await holder.release()
    assert holder.lost


async def test_an_agent_that_ignores_sigterm_is_killed_after_the_grace() -> None:
    child = await spawn_agent(
        _python(
            "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
        ),
        os.getpid(),
    )
    await asyncio.sleep(0.5)  # let it install the handler
    await seat_hold.stop_agent(child, grace=0.3)
    assert child.returncode == -signal.SIGKILL


def test_the_parser_takes_the_agent_command_after_the_options() -> None:
    parser = argparse.ArgumentParser()
    cli_seat_hold.add_parsers(parser.add_subparsers())
    args = parser.parse_args(
        ["seat-hold", "--identity", "T/seat", "--nonce", NONCE, "--", "codex", "resume", "x"]
    )
    assert args.func is cli_seat_hold._cmd_seat_hold
    assert args.command[-3:] == ["codex", "resume", "x"]
    captured: list[Coroutine[Any, Any, int]] = []

    def runner(coroutine: Coroutine[Any, Any, int]) -> int:
        captured.append(coroutine)
        coroutine.close()
        return 0

    bare = argparse.Namespace(
        identity="T/seat",
        nonce=NONCE,
        marker_dir=None,
        uri="ws://h",
        token_file=None,
        command=["true"],
    )
    assert cli_seat_hold._cmd_seat_hold(bare, runner=runner) == 0 and len(captured) == 1


class _Libc:
    """A loaded C library double: records each ``prctl`` call."""

    def __init__(self, calls: list[tuple[int, ...]]) -> None:
        self.calls = calls

    def prctl(self, *args: int) -> int:
        self.calls.append(args)
        return 0


def test_the_library_is_loaded_in_the_parent_and_never_in_the_child_setup() -> None:
    """Loading a library between fork and exec could deadlock; the child only calls."""
    loads: list[object] = []
    calls: list[tuple[int, ...]] = []

    def loader(name: object, use_errno: bool = False) -> _Libc:
        loads.append(name)
        return _Libc(calls)

    recorder = _Ops(tty=False)
    ops = dataclasses.replace(
        recorder.ops, set_death_signal=seat_hold.child_ops(loader).set_death_signal
    )
    assert loads == [None]
    setup = child_setup(41, ops)
    setup()
    assert loads == [None]
    assert calls == [(seat_hold.PR_SET_PDEATHSIG, int(signal.SIGTERM), 0, 0, 0)]


def test_a_set_up_made_without_operations_resolves_them_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loads: list[object] = []

    def loader(name: object, use_errno: bool = False) -> _Libc:
        loads.append(name)
        return _Libc([])

    monkeypatch.setattr("ctypes.CDLL", loader)
    child_setup(41)  # made in the parent, as spawn_agent makes it; never run here
    assert loads == [None]


def test_an_unreadable_token_file_is_a_named_refusal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "absent-token"
    args = argparse.Namespace(
        identity="T/seat",
        nonce=NONCE,
        marker_dir=None,
        uri="ws://h",
        token_file=str(missing),
        command=["--", "true"],
    )

    def never(coroutine: Coroutine[Any, Any, int]) -> int:
        coroutine.close()
        raise AssertionError("nothing may run")

    assert cli_seat_hold._cmd_seat_hold(args, runner=never) == cli_seat_hold.USAGE_EXIT_CODE == 2
    out = capsys.readouterr().out.strip()
    assert (
        out == f"synapse seat-hold: cannot read the token file {missing}: No such file or directory"
    )


async def test_a_failed_start_replaces_held_with_failed(tmp_path: Path) -> None:
    """``held`` is not final: a spawn that fails turns it into ``failed``."""
    seen: list[str] = []

    async def failing(argv: Sequence[str], _pid: int) -> Any:
        seen.append(_marker(tmp_path))
        raise OSError("exec format error")

    async with running_hub() as (_hub, uri):
        code = await hold_seat(
            "T/seat", NONCE, ["agent"], uri=uri, marker_dir=tmp_path, spawner=failing, timing=FAST
        )
    assert seen == [f"held T/seat-seat {NONCE}"]
    assert code == AGENT_START_FAILED_EXIT_CODE
    assert _marker(tmp_path) == f"failed T/seat-seat {NONCE}"


class _HangingAgent:
    """A client whose hub never answers; it records whether its attempt was closed."""

    closed: list[str] = []

    def __init__(self, name: str, *_args: object, **_kwargs: object) -> None:
        self.name = name
        self.running = True
        self.last_close_code: int | None = None
        self.last_close_reason = ""
        self.callback: MessageCallback | None = None

    async def connect(self) -> None:
        try:
            await asyncio.sleep(3600)
        finally:
            _HangingAgent.closed.append(self.name)

    async def wait_until_ready(self, timeout: float = 5.0) -> bool:
        await asyncio.sleep(timeout)
        return False

    async def request_who(self, hub: str | None = None) -> None:
        return None


async def test_a_stop_during_a_reconnect_does_not_wait_for_the_registration() -> None:
    _HangingAgent.closed.clear()
    holder = SeatHolder("T/seat", uri="ws://unused", token=None, factory=_HangingAgent)
    child = await spawn_agent(["sleep", "30"], os.getpid())
    stop = asyncio.Event()
    slow = dataclasses.replace(FAST, ready_timeout=60.0)
    asyncio.get_running_loop().call_later(0.3, stop.set)
    started = time.monotonic()
    status = await asyncio.wait_for(supervise(holder, child, stop=stop, timing=slow), timeout=20.0)
    assert time.monotonic() - started < 10.0
    assert status == 128 + signal.SIGTERM
    assert _HangingAgent.closed == ["T/seat-seat"]


# --- the holder's own pane waiter --------------------------------------------------


async def test_a_holder_with_a_wake_target_runs_its_waiter_while_the_agent_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[tuple[str, object]] = []
    online: list[list[str]] = []
    hub_ref: list[SynapseHub] = []

    class _Seat:
        def close(self) -> None:
            online.append(sorted(hub_ref[0].online_agents()))

    def fake_open(target: WakeTarget, **options: Any) -> _Seat:
        events.append(("open", (target, options["agent_argv"], options["log_path"])))
        return _Seat()

    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(seat_waiter, "open_seat_waiter", fake_open)
    target = WakeTarget("synapse-T_seat")
    agent = _python("raise SystemExit(4)")
    async with running_hub() as (hub, uri):
        hub_ref.append(hub)
        code = await hold_seat(
            "T/seat", NONCE, agent, uri=uri, marker_dir=tmp_path, timing=FAST, wake=target
        )
    assert code == 4
    assert events == [("open", (target, agent, tmp_path / "T_seat.log"))]
    # The waiter closed while the seat was still held, before the holder let go.
    assert len(online) == 1 and "T/seat-seat" in online[0]
    assert isinstance(sys.stdout, seat_waiter.OutputRouter)  # installed at the holder's start


async def test_a_holder_without_a_wake_target_runs_no_waiter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def never(*_args: Any, **_options: Any) -> object:
        raise AssertionError("no waiter without a wake target")

    plain = io.StringIO()
    monkeypatch.setattr(sys, "stdout", plain)
    monkeypatch.setattr(seat_waiter, "open_seat_waiter", never)
    async with running_hub() as (_hub, uri):
        code = await hold_seat(
            "T/seat", NONCE, _python("pass"), uri=uri, marker_dir=tmp_path, timing=FAST
        )
    assert code == 0 and sys.stdout is plain


async def test_a_start_without_a_nonce_makes_a_fresh_one_each_time(tmp_path: Path) -> None:
    async with running_hub() as (_hub, uri):
        for _ in range(2):
            code = await hold_seat(
                "T/seat", None, _python("pass"), uri=uri, marker_dir=tmp_path, timing=FAST
            )
            assert code == 0
    names = sorted(path.name for path in tmp_path.glob("T_seat.*"))
    assert len(names) == 2 and all(re.fullmatch(r"T_seat\.[0-9a-f]{16}", name) for name in names)


def test_the_command_line_passes_the_wake_target_and_an_optional_nonce(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict[str, Any]] = []

    async def fake_hold(identity: str, nonce: str | None, argv: list[str], **options: Any) -> int:
        seen.append({"nonce": nonce, **options})
        return 0

    monkeypatch.setattr(cli_seat_hold, "hold_seat", fake_hold)
    parser = argparse.ArgumentParser()
    cli_seat_hold.add_parsers(parser.add_subparsers())
    woken = parser.parse_args(
        [
            "seat-hold",
            "--identity",
            "T/seat",
            "--wake-session",
            "synapse-T_seat",
            "--tmux-bin",
            "/x/tmux",
            "--pane-probe-interval",
            "2.5",
            "--",
            "claude",
        ]
    )
    plain = parser.parse_args(["seat-hold", "--identity", "T/seat", "--", "claude"])
    assert cli_seat_hold._cmd_seat_hold(woken, runner=asyncio.run) == 0
    assert cli_seat_hold._cmd_seat_hold(plain, runner=asyncio.run) == 0
    assert seen[0]["nonce"] is None
    assert seen[0]["wake"] == WakeTarget("synapse-T_seat", "/x/tmux", 2.5)
    assert seen[1]["wake"] is None
