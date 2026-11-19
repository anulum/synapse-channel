# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — one live holder per seat, held for the agent's whole lifetime
"""Hold a seat's name for its agent's lifetime, and start the agent only once it is held.

A seat must never run twice: two copies of one identity split its messages
and duplicate its claims. The hub already keeps one live connection per name,
so a seat is fenced by holding a dedicated name, ``<identity>-seat``, for as
long as its agent runs:

* **One connection, never a takeover.** The holder registers ``<identity>-seat``
  without asking the hub to evict anyone. When a live connection holds the name,
  the hub refuses it, and the seat does not start: exit
  :data:`SEAT_LIVE_EXIT_CODE`. No ownership-lease token is used, because a token
  file on the same host would let a second copy present it and be admitted.
* **The agent starts after the name is held.** The holder writes a private
  marker naming the fresh nonce it was given (``held``), then starts the agent
  as its own child, and writes the final ``ready`` once the agent has started
  (or ``failed`` when it could not). A caller that waits for a final word learns
  the outcome without guessing.
* **The holder's death ends the agent.** On Linux the agent is started with a
  parent-death signal, and checks right after setting it that the holder is
  still its parent. A holder killed outright therefore never leaves an agent
  running without its fence. The agent runs in its own process group, which the
  holder signals when it ends; the agent's own children are covered on a normal
  exit, but may outlive a holder that was killed outright. They hold no seat
  identity. On other platforms the holder refuses by name, because it cannot
  give this guarantee there.
* **A hub restart does not end the seat.** When the connection drops, the
  holder registers again, still without a takeover, backing off up to
  :data:`RECONNECT_MAX_SECONDS`. A refusal right after the drop is usually this
  holder's own old socket, which the hub reaps within :data:`GHOST_WINDOW_SECONDS`.
  A refusal that persists beyond that window means another live holder: the
  seat ends, fail-closed.

A restore that waits :data:`RESTORE_GRACE_SECONDS` after a hub start gives every
live holder the time to register again before it tries a name.
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import os
import re
import secrets
import signal
import subprocess  # nosec B404 # the agent's own argument vector, never a shell
import sys
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import TYPE_CHECKING, Any, Protocol

from synapse_channel.client.agent_dispatch import MessageCallback
from synapse_channel.connect_failures import closed_after_ready, is_name_conflict_close
from synapse_channel.core.wake_capability import WAKE_PASSIVE
from synapse_channel.reap import private_runtime_parent, safe_key

if TYPE_CHECKING:
    from synapse_channel.seat_waiter import OutputRouter, SeatWaiter, WakeTarget

SEAT_NAME_SUFFIX = "-seat"
"""Suffix of the dedicated name a seat holder registers."""

SEAT_LIVE_EXIT_CODE = 75
"""Exit code when a live connection holds the seat (``EX_TEMPFAIL``): nothing started."""

HUB_UNAVAILABLE_EXIT_CODE = 69
"""Exit code when the hub could not be reached or refused the holder (``EX_UNAVAILABLE``)."""

UNSUPPORTED_PLATFORM_EXIT_CODE = 78
"""Exit code on a platform without a parent-death signal (``EX_CONFIG``): nothing started."""

AGENT_START_FAILED_EXIT_CODE = 70
"""Exit code when the agent could not be started with its fence (``EX_SOFTWARE``)."""

READY_TIMEOUT_SECONDS = 10.0
"""How long one registration waits for the hub's welcome and verdict."""

RECONNECT_BASE_SECONDS = 1.0
"""First wait before registering again after the connection dropped."""

RECONNECT_MAX_SECONDS = 15.0
"""Longest wait between two registrations of a holder whose connection dropped."""

GHOST_WINDOW_SECONDS = 30.0
"""How long a dropped socket may still hold its name: the hub's ping interval plus pong timeout."""

RESTORE_GRACE_SECONDS = RECONNECT_MAX_SECONDS + GHOST_WINDOW_SECONDS
"""How long a restore waits after a hub start, so every live holder registers again first."""

STOP_GRACE_SECONDS = 10.0
"""How long the agent's process group gets after ``SIGTERM`` before ``SIGKILL``."""

PR_SET_PDEATHSIG = 1
"""The Linux ``prctl`` option that delivers a signal to a child when its parent dies."""

NONCE_PATTERN = re.compile(r"[A-Za-z0-9_-]{8,64}\Z")
"""A nonce names the marker file, so it is a short, filename-safe token."""

SignalHandler = Callable[[int, FrameType | None], object] | int | signal.Handlers | None
"""A signal disposition, as :func:`signal.signal` takes and returns it."""


def seat_name(identity: str) -> str:
    """Return the dedicated name the holder of ``identity`` registers."""
    return f"{identity}{SEAT_NAME_SUFFIX}"


def default_marker_dir(environ: Mapping[str, str] | None = None) -> Path:
    """Return the private directory for readiness markers.

    Parameters
    ----------
    environ : Mapping[str, str] or None
        The environment; defaults to :data:`os.environ`.

    Returns
    -------
    Path
        ``$XDG_RUNTIME_DIR/synapse-seat-hold``, or the same leaf under the
        private fallback the shell hooks use.
    """
    env = os.environ if environ is None else environ
    root = env.get("XDG_RUNTIME_DIR", "").strip()
    parent = Path(root) if root else private_runtime_parent(env)
    return parent / "synapse-seat-hold"


def marker_path(directory: Path, identity: str, nonce: str) -> Path:
    """Return the marker file of one start: named by the identity and that start's nonce."""
    return directory / f"{safe_key(identity)}.{nonce}"


def write_marker(path: Path, line: str) -> None:
    """Write ``line`` to ``path`` atomically, readable by its owner only.

    The directory is created ``0700``. The line is written to a new ``0600``
    file beside the target and moved into place, so a reader sees either no
    marker or the whole line.
    """
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def unsupported_platform(platform: str = sys.platform) -> str | None:
    """Return the refusal for a platform without a parent-death signal, else ``None``."""
    if platform.startswith("linux"):
        return None
    return (
        f"synapse seat-hold: refused on {platform}: the agent could outlive its holder "
        "here, because this platform has no parent-death signal for a child process"
    )


class ChildSetupError(RuntimeError):
    """The agent's child could not set up its fence; the agent is never executed."""


@dataclass(frozen=True, slots=True)
class ChildOps:
    """The process operations the agent's child runs before it executes the agent.

    They are a parameter so the child's set-up can be checked in-process; the
    real ones come from :func:`child_ops`.
    """

    setpgid: Callable[[int, int], None]
    isatty: Callable[[int], bool]
    getpgrp: Callable[[], int]
    tcsetpgrp: Callable[[int, int], None]
    set_sigttou: Callable[[SignalHandler], SignalHandler]
    set_death_signal: Callable[[int], int]
    getppid: Callable[[], int]


def resolve_death_signal(loader: Callable[..., Any] | None = None) -> Callable[[int], int]:
    """Resolve ``prctl`` now, in the calling process, and return a setter that only calls it.

    The agent's child runs between fork and exec, where loading a library could
    deadlock on a lock that another thread of the holder held at the fork (an
    executor thread resolving the hub's host name, for one). So the library is
    loaded here, in the parent, and the child only calls the resolved function.
    """
    prctl = (loader or ctypes.CDLL)(None, use_errno=True).prctl

    def set_death_signal(signum: int) -> int:
        """Ask the kernel to send ``signum`` to this process when its parent dies."""
        return int(prctl(PR_SET_PDEATHSIG, signum, 0, 0, 0))

    return set_death_signal


def _set_sigttou(handler: SignalHandler) -> SignalHandler:
    """Set the ``SIGTTOU`` disposition and return the previous one."""
    return signal.signal(signal.SIGTTOU, handler)


def child_ops(loader: Callable[..., Any] | None = None) -> ChildOps:
    """Return the real child operations, with ``prctl`` resolved in this (parent) process."""
    return ChildOps(
        setpgid=os.setpgid,
        isatty=os.isatty,
        getpgrp=os.getpgrp,
        tcsetpgrp=os.tcsetpgrp,
        set_sigttou=_set_sigttou,
        set_death_signal=resolve_death_signal(loader),
        getppid=os.getppid,
    )


def child_setup(holder_pid: int, ops: ChildOps | None = None) -> Callable[[], None]:
    """Return the set-up the agent's child runs between fork and exec.

    The child enters a process group of its own and, on a terminal, takes the
    terminal's foreground so the agent still reads its input. It then asks for
    ``SIGTERM`` when its parent dies, and checks that its parent is still the
    holder: a holder that died between the fork and that request would
    otherwise leave the agent running without a fence. Either failure raises
    :class:`ChildSetupError`, so the agent is never executed; the holder learns
    it as a failed start. The operations are made here, in the parent, so the
    child loads nothing.
    """
    real = ops or child_ops()

    def setup() -> None:
        """Run in the child before it executes the agent."""
        real.setpgid(0, 0)
        if real.isatty(0):
            previous = real.set_sigttou(signal.SIG_IGN)
            with contextlib.suppress(OSError):
                real.tcsetpgrp(0, real.getpgrp())
            real.set_sigttou(previous)
        if real.set_death_signal(signal.SIGTERM) != 0:
            raise ChildSetupError("the kernel refused the parent-death signal")
        if real.getppid() != holder_pid:
            raise ChildSetupError("the holder ended before the agent could be fenced")

    return setup


class _Agent(Protocol):
    """The client surface a holder uses, including what the acceptance check reads."""

    running: bool
    last_close_code: int | None
    last_close_reason: str
    callback: MessageCallback | None

    async def connect(self) -> None:
        """Run one connection until it closes."""

    async def wait_until_ready(self, timeout: float = 5.0) -> bool:
        """Wait for the hub's welcome."""

    async def request_who(self, hub: str | None = None) -> None:
        """Ask the hub for its roster."""


AgentFactory = Callable[..., _Agent]
"""Builds the holder's client; injectable for tests."""


class SeatHolder:
    """One continuous registration of ``<identity>-seat``, never with a takeover."""

    def __init__(
        self, identity: str, *, uri: str, token: str | None, factory: AgentFactory
    ) -> None:
        self.name = seat_name(identity)
        self._uri = uri
        self._token = token
        self._factory = factory
        self._agent: _Agent | None = None
        self.task: asyncio.Task[None] | None = None

    async def acquire(self, timeout: float = READY_TIMEOUT_SECONDS) -> str:
        """Register the name once; return ``"held"``, ``"live"`` or ``"unavailable"``.

        ``"live"`` means a live connection holds the name; ``"unavailable"``
        means the hub could not be reached or refused the holder for another
        reason. Only ``"held"`` leaves a connection open.
        """
        agent = self._factory(
            self.name,
            None,
            uri=self._uri,
            verbose=False,
            token=self._token,
            takeover=False,
            wake_capability=WAKE_PASSIVE,
        )
        task = asyncio.create_task(agent.connect())
        try:
            refused = not await agent.wait_until_ready(timeout=timeout)
            if not refused:
                refused = await closed_after_ready(agent, timeout=timeout)
        except asyncio.CancelledError:
            await _close(agent, task)
            raise
        if refused:
            await _close(agent, task)
            live = is_name_conflict_close(agent.last_close_code, agent.last_close_reason)
            return "live" if live else "unavailable"
        self._agent, self.task = agent, task
        return "held"

    @property
    def lost(self) -> bool:
        """Return whether the held connection has ended."""
        return self.task is None or self.task.done()

    async def release(self) -> None:
        """Close the held connection, if any."""
        if self._agent is not None and self.task is not None:
            await _close(self._agent, self.task)
        self._agent, self.task = None, None


async def _close(agent: _Agent, task: asyncio.Task[None]) -> None:
    """End one connection attempt and wait for it."""
    agent.running = False
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


class _Child(Protocol):
    """The agent process the holder supervises."""

    @property
    def pid(self) -> int:
        """The agent's process id, which also leads its process group."""

    @property
    def returncode(self) -> int | None:
        """The agent's return code once it has ended."""

    async def wait(self) -> int:
        """Wait for the process to end and return its exit status."""


Spawner = Callable[[Sequence[str], int], Awaitable[_Child]]
"""Starts the agent as the holder's child; injectable for tests."""


async def spawn_agent(argv: Sequence[str], holder_pid: int) -> _Child:
    """Start ``argv`` as the holder's child, set up by :func:`child_setup`."""
    return await asyncio.create_subprocess_exec(*argv, preexec_fn=child_setup(holder_pid))


def _signal_group(pid: int, signum: int) -> None:
    """Send ``signum`` to the process group led by ``pid``, if it still exists."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signum)


async def stop_agent(child: _Child, *, grace: float = STOP_GRACE_SECONDS) -> None:
    """End the agent's process group: ``SIGTERM``, then ``SIGKILL`` after ``grace``."""
    _signal_group(child.pid, signal.SIGTERM)
    try:
        await asyncio.wait_for(child.wait(), timeout=grace)
    except TimeoutError:
        _signal_group(child.pid, signal.SIGKILL)
        await child.wait()


def _status(returncode: int) -> int:
    """Map a child's return code to a shell exit status (``128 + signal`` for a signal)."""
    return 128 - returncode if returncode < 0 else returncode


@dataclass(frozen=True, slots=True)
class HoldTiming:
    """The waits a holder uses; shortened in tests."""

    ready_timeout: float = READY_TIMEOUT_SECONDS
    reconnect_base: float = RECONNECT_BASE_SECONDS
    reconnect_max: float = RECONNECT_MAX_SECONDS
    ghost_window: float = GHOST_WINDOW_SECONDS
    stop_grace: float = STOP_GRACE_SECONDS


DEFAULT_TIMING = HoldTiming()
"""The documented waits, used unless a caller passes its own."""


async def _first_of(*awaitables: Awaitable[object], timeout: float | None = None) -> None:
    """Return when the first of ``awaitables`` finishes, or after ``timeout``.

    Only the waits made here from plain coroutines are cancelled afterwards. A
    task or future passed in belongs to the caller (the agent's exit, the held
    connection) and keeps running.
    """
    owned = [item for item in awaitables if not isinstance(item, asyncio.Future)]
    waits = [asyncio.ensure_future(item) for item in awaitables]
    try:
        await asyncio.wait(waits, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
    finally:
        mine = [wait for item, wait in zip(awaitables, waits, strict=True) if item in owned]
        for wait in mine:
            if not wait.done():
                wait.cancel()
        await asyncio.gather(*mine, return_exceptions=True)


async def supervise(
    holder: SeatHolder,
    child: _Child,
    *,
    stop: asyncio.Event,
    timing: HoldTiming = DEFAULT_TIMING,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Keep the seat held while the agent runs; return the seat's exit status.

    The agent's own exit ends the seat with the agent's status. ``stop`` (a
    signal to the holder) ends the agent's process group first. A dropped
    connection is registered again without a takeover; a refusal that persists
    beyond the ghost window ends the agent and returns :data:`SEAT_LIVE_EXIT_CODE`.
    """
    agent_done = asyncio.ensure_future(child.wait())
    lost_since: float | None = None
    delay = timing.reconnect_base
    try:
        while True:
            if agent_done.done():
                _signal_group(child.pid, signal.SIGTERM)
                return _status(agent_done.result())
            if stop.is_set():
                await stop_agent(child, grace=timing.stop_grace)
                return _status(child.returncode if child.returncode is not None else 0)
            held = holder.task
            if held is not None and not held.done():
                await _first_of(agent_done, stop.wait(), held)
                continue
            if lost_since is None:
                lost_since = clock()
            # A stop or the agent's exit must not wait for a registration in flight.
            acquiring = asyncio.ensure_future(holder.acquire(timeout=timing.ready_timeout))
            await _first_of(acquiring, agent_done, stop.wait())
            if not acquiring.done():
                acquiring.cancel()
                await asyncio.gather(acquiring, return_exceptions=True)
                continue
            verdict = acquiring.result()
            if verdict == "held":
                lost_since, delay = None, timing.reconnect_base
                continue
            if verdict == "live" and clock() - lost_since >= timing.ghost_window:
                print(
                    f"synapse seat-hold: {holder.name} is held by another live connection; "
                    "ending this seat so the identity never runs twice"
                )
                await stop_agent(child, grace=timing.stop_grace)
                return SEAT_LIVE_EXIT_CODE
            await _first_of(agent_done, stop.wait(), timeout=delay)
            delay = min(delay * 2, timing.reconnect_max)
    finally:
        agent_done.cancel()  # a no-op once the agent's exit was collected
        await asyncio.gather(agent_done, return_exceptions=True)


def reclaim_terminal(ops: ChildOps | None = None) -> None:
    """Give the terminal's foreground back to the holder after the agent ended."""
    ops = ops or child_ops()
    if not ops.isatty(0):
        return
    previous = ops.set_sigttou(signal.SIG_IGN)
    with contextlib.suppress(OSError):
        ops.tcsetpgrp(0, ops.getpgrp())
    ops.set_sigttou(previous)


def _install_router() -> OutputRouter:
    """Install the waiter's output router once, at the holder's start."""
    from synapse_channel.seat_waiter import install_router

    return install_router()


def _open_waiter(
    wake: WakeTarget,
    router: OutputRouter,
    identity: str,
    argv: Sequence[str],
    uri: str,
    token: str | None,
    directory: Path,
) -> SeatWaiter:
    """Silence the holder's output and start the pane waiter, once the agent runs."""
    from synapse_channel.seat_waiter import open_seat_waiter

    return open_seat_waiter(
        wake,
        identity=identity,
        agent_argv=argv,
        uri=uri,
        token=token,
        log_path=directory / f"{safe_key(identity)}.log",
        router=router,
    )


@dataclass
class _WaiterSlot:
    """The seat's pane waiter, if it has one: prepared at the start, opened once the agent runs."""

    wake: WakeTarget | None
    router: OutputRouter | None = None
    seat: SeatWaiter | None = None

    @classmethod
    def prepare(cls, wake: WakeTarget | None) -> _WaiterSlot:
        """Install the output router at the holder's start when the seat has a waiter."""
        return cls(wake, _install_router() if wake is not None else None)

    def open(
        self, identity: str, argv: Sequence[str], uri: str, token: str | None, directory: Path
    ) -> None:
        """Silence the holder and start the waiter; call it once the agent runs."""
        if self.wake is not None and self.router is not None:
            self.seat = _open_waiter(self.wake, self.router, identity, argv, uri, token, directory)

    async def close(self) -> None:
        """Stop the waiter and give the terminal back, before the seat is released."""
        if self.seat is not None:
            await asyncio.to_thread(self.seat.close)


async def hold_seat(
    identity: str,
    nonce: str | None,
    argv: Sequence[str],
    *,
    uri: str,
    token: str | None = None,
    marker_dir: Path | None = None,
    factory: AgentFactory | None = None,
    spawner: Spawner = spawn_agent,
    platform: str = sys.platform,
    timing: HoldTiming = DEFAULT_TIMING,
    wake: WakeTarget | None = None,
) -> int:
    """Hold ``<identity>-seat``, run ``argv`` as the agent while it is held, and return its status.

    Parameters
    ----------
    identity : str
        The seat's Synapse identity.
    nonce : str or None
        This start's fresh token; the marker file is named by it. ``None``
        makes a fresh one, so a start from a recorded command never reuses one.
    argv : Sequence[str]
        The agent's command.
    uri, token : str
        The hub and its shared-secret token, if it is secured.
    marker_dir : Path or None
        Where the marker is written; :func:`default_marker_dir` by default.
    factory : AgentFactory or None
        Builds the holder's client; :class:`synapse_channel.client.agent.SynapseAgent` by default.
    spawner : Spawner
        Starts the agent; :func:`spawn_agent` by default.
    platform : str
        The platform name checked for a parent-death signal.
    timing : HoldTiming
        The waits; the defaults are the documented constants.
    wake : WakeTarget or None
        The tmux session whose pane this holder also wakes on directed
        messages, with the waiter in its own process
        (:mod:`synapse_channel.seat_waiter`). While the agent runs, the
        holder's own output then goes to ``<identity>.log`` beside the marker.

    Returns
    -------
    int
        The agent's exit status; :data:`SEAT_LIVE_EXIT_CODE` when the seat is
        live elsewhere; :data:`HUB_UNAVAILABLE_EXIT_CODE` when the hub could not
        be reached; :data:`UNSUPPORTED_PLATFORM_EXIT_CODE` off Linux;
        :data:`AGENT_START_FAILED_EXIT_CODE` when the agent could not be started
        with its fence. The marker says ``held`` while the agent is being
        started, then one final word: ``ready``, ``refused``, ``unavailable``,
        ``unsupported`` or ``failed``.
    """
    nonce = nonce or secrets.token_hex(8)
    if not NONCE_PATTERN.match(nonce):
        raise ValueError("nonce must be 8 to 64 letters, digits, '_' or '-'")
    if factory is None:
        from synapse_channel.client.agent import SynapseAgent

        factory = SynapseAgent
    holder = SeatHolder(identity, uri=uri, token=token, factory=factory)
    directory = marker_dir or default_marker_dir()
    marker = marker_path(directory, identity, nonce)
    slot = _WaiterSlot.prepare(wake)
    refusal = unsupported_platform(platform)
    if refusal is not None:
        print(refusal)
        write_marker(marker, f"unsupported {holder.name} {nonce}")
        return UNSUPPORTED_PLATFORM_EXIT_CODE
    verdict = await holder.acquire(timeout=timing.ready_timeout)
    if verdict != "held":
        live = verdict == "live"
        print(
            f"synapse seat-hold: {holder.name} "
            + ("is held by a live connection; nothing started" if live else "could not be held")
        )
        write_marker(marker, f"{'refused' if live else 'unavailable'} {holder.name} {nonce}")
        return SEAT_LIVE_EXIT_CODE if live else HUB_UNAVAILABLE_EXIT_CODE
    write_marker(marker, f"held {holder.name} {nonce}")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    handled = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
    for signum in handled:
        loop.add_signal_handler(signum, stop.set)
    try:
        try:
            child = await spawner(argv, os.getpid())
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"synapse seat-hold: the agent could not be started with its fence: {exc}")
            write_marker(marker, f"failed {holder.name} {nonce}")
            return AGENT_START_FAILED_EXIT_CODE
        write_marker(marker, f"ready {holder.name} {nonce}")
        slot.open(identity, argv, uri, token, directory)
        try:
            return await supervise(holder, child, stop=stop, timing=timing)
        finally:
            await slot.close()
    finally:
        for signum in handled:
            loop.remove_signal_handler(signum)
        await holder.release()
        reclaim_terminal()
