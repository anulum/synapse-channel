# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the pane waiter, run inside the seat holder's own process
"""The pane waiter of a seat, run inside ``synapse seat-hold``: one process per seat.

Without this module a tmux seat has two waiter processes besides its agent
(``agent-tmux wait`` and its ``synapse wait`` child). Here the seat holder runs
the same wait loop, :func:`~synapse_channel.agent_tmux.wait_and_wake`, in one
thread, and each bridge wait runs inside the process too:

* **Its own event loop.** Each wait runs with :func:`asyncio.run` in the waiter
  thread, never on the holder's loop, so a stall in a wait can never delay the
  holder's continuous registration of ``<identity>-seat``.
* **Faults stay in the waiter.** Whatever the wait, the probe or the loop
  raises ends the waiter only. The seat runs on, unwoken, and the reason is
  recorded in the seat's registry entry and in the holder's log.
* **Nothing reaches the agent's terminal.** While the agent runs, the holder's
  own standard output and error (descriptors 1 and 2) point to a private log;
  descriptor 0 is never touched. One router per stream, installed once, sends the waiter
  thread's printed lines to that wait's in-memory capture instead.

If the holder dies while redirected, its last lines are in the log, not in the
pane.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import functools
import io
import logging
import os
import subprocess  # nosec B404 # only the CompletedProcess type; nothing is run
import sys
import threading
import traceback
from collections.abc import Callable, Coroutine, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TextIO

from synapse_channel.agent_tmux import (
    DEFAULT_PANE_PROBE_INTERVAL,
    AgentTmuxConfig,
    record_waiter_stop,
    wait_and_wake,
    wait_executor,
)

SEAT_LOG_LIMIT_BYTES = 4 * 1024 * 1024
"""Size above which the holder's log is rotated once (one older file is kept)."""

WAITER_STOP_SECONDS = 10.0
"""How long a stopping holder waits for its waiter thread to finish."""

WAITER_STOPPED_REFUSAL = 143
"""The refusal a wait returns when the holder stops its waiter (``128 + SIGTERM``)."""


@dataclass(frozen=True)
class WakeTarget:
    """The tmux session whose agent the holder's waiter wakes."""

    session: str
    tmux_bin: str = "tmux"
    pane_probe_interval: float = DEFAULT_PANE_PROBE_INTERVAL


class OutputRouter(io.TextIOBase):
    """``sys.stdout`` for the holder: the waiter thread's lines go to its capture.

    It is installed once, at the holder's start, and never swapped per wait,
    because ``sys.stdout`` belongs to the whole process. A write from any other
    thread, or from the waiter thread outside a wait, goes to the original
    stream, which is descriptor 1.
    """

    def __init__(self, target: TextIO) -> None:
        super().__init__()
        self._target = target
        self._captures: dict[int, io.StringIO] = {}

    def write(self, text: str) -> int:
        """Write ``text`` to the calling thread's capture, or to the original stream."""
        capture = self._captures.get(threading.get_ident())
        return (capture if capture is not None else self._target).write(text)

    def flush(self) -> None:
        """Flush the original stream."""
        self._target.flush()

    def fileno(self) -> int:
        """Return the original stream's descriptor."""
        return self._target.fileno()

    def isatty(self) -> bool:
        """Report whether the original stream is a terminal."""
        return self._target.isatty()

    @contextlib.contextmanager
    def capture(self) -> Iterator[io.StringIO]:
        """Capture the calling thread's writes until the block ends."""
        buffer = io.StringIO()
        ident = threading.get_ident()
        self._captures[ident] = buffer
        try:
            yield buffer
        finally:
            self._captures.pop(ident, None)


def install_router(stream: Literal["stdout", "stderr"] = "stdout") -> OutputRouter:
    """Install a router as ``sys.stdout`` or ``sys.stderr`` once, and return it."""
    current = getattr(sys, stream)
    if isinstance(current, OutputRouter):
        return current
    router = OutputRouter(current)
    setattr(sys, stream, router)
    return router


def flush_output() -> None:
    """Flush standard output, standard error and every logging handler."""
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(OSError, ValueError):
            stream.flush()
    loggers = [logging.getLogger()]
    loggers += [
        logger
        for logger in logging.Logger.manager.loggerDict.values()
        if isinstance(logger, logging.Logger)
    ]
    for logger in loggers:
        for handler in logger.handlers:
            with contextlib.suppress(OSError, ValueError):
                handler.flush()


class QuietTerminal:
    """Points the holder's descriptors 1 and 2 at its private log, and back.

    Descriptor 0 is never touched. Output is flushed before every switch, so
    text written just before it lands on the side it was written for.
    """

    def __init__(self, log_path: Path, *, limit: int = SEAT_LOG_LIMIT_BYTES) -> None:
        self.log_path = log_path
        self.limit = limit
        self._saved: tuple[int, int] | None = None

    @property
    def redirected(self) -> bool:
        """Whether descriptors 1 and 2 point at the log now."""
        return self._saved is not None

    def _point_at_log(self) -> None:
        fd = os.open(self.log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.dup2(fd, 1)
            os.dup2(fd, 2)
        finally:
            os.close(fd)

    def redirect(self) -> None:
        """Send descriptors 1 and 2 to the log; the terminal's copies are kept."""
        if self._saved is not None:
            return
        flush_output()
        self._saved = (os.dup(1), os.dup(2))
        self._point_at_log()

    def restore(self) -> None:
        """Point descriptors 1 and 2 at the terminal again."""
        if self._saved is None:
            return
        flush_output()
        out, err = self._saved
        self._saved = None
        os.dup2(out, 1)
        os.dup2(err, 2)
        os.close(out)
        os.close(err)

    def rotate_if_full(self) -> None:
        """Rotate the log once it is above its limit, keeping one older file."""
        if self._saved is None or os.fstat(1).st_size <= self.limit:
            return
        flush_output()
        os.replace(self.log_path, self.log_path.with_name(self.log_path.name + ".1"))
        self._point_at_log()


@functools.cache
def _wait_parser() -> argparse.ArgumentParser:
    """Return a parser for ``synapse wait`` alone, without the rest of the command line."""
    from synapse_channel.cli_messaging import add_parsers

    parser = argparse.ArgumentParser(prog="synapse", add_help=False)
    add_parsers(parser.add_subparsers(dest="command"))
    return parser


class InProcessWait:
    """Runs a bridge's ``synapse wait`` inside this process, on the thread's own loop.

    It is the in-process form of
    :func:`~synapse_channel.pane_bridge_wait.run_probed_wait`: the same command
    line, parsed by the same parser and run by the same command function, with
    the same probe and heartbeat each ``interval``, and the same results (the
    wait's exit code and printed lines, or a timeout-shaped result with the
    probe's refusal). The environment is the holder's own.
    """

    def __init__(
        self,
        *,
        router: OutputRouter,
        stop: threading.Event,
        quiet: QuietTerminal | None = None,
        errors: OutputRouter | None = None,
    ) -> None:
        self._router = router
        self._errors = errors
        self._stop = stop
        self._quiet = quiet
        self._running: tuple[asyncio.AbstractEventLoop, asyncio.Future[int]] | None = None

    def cancel(self) -> None:
        """Cancel the wait in flight, from any thread."""
        running = self._running
        if running is not None:
            loop, task = running
            with contextlib.suppress(RuntimeError):  # the loop has already closed
                loop.call_soon_threadsafe(task.cancel)

    def __call__(
        self,
        command: Sequence[str],
        *,
        env: Mapping[str, str],
        interval: float,
        probe: Callable[[], int],
        heartbeat: Callable[[], None] | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], int]:
        """Run one wait; see :class:`~synapse_channel.agent_tmux.ProbedWait`."""
        del env  # the holder's own environment; a pane-bridge wait never yields
        refusal = [0]
        if self._quiet is not None:
            self._quiet.rotate_if_full()
        if self._stop.is_set():
            return _timeout_shaped(command), WAITER_STOPPED_REFUSAL
        args = _wait_parser().parse_args(list(command[1:]))
        args.exit_with_parent = None  # one process: there is no parent to follow

        def run(coroutine: Coroutine[Any, Any, int]) -> int:
            return asyncio.run(self._probed(coroutine, interval, probe, heartbeat, refusal))

        crashed: Exception | None = None
        with self._router.capture() as captured, self._capture_errors() as errors:
            try:
                code = int(args.func(args, async_runner=run))
            except Exception as exc:  # a crashed wait reads as a crashed child: exit 1
                crashed, code = exc, 1
        if crashed is not None:  # outside the capture: the trace belongs in the holder's log
            traceback.print_exception(crashed)
        if refusal[0]:
            return _timeout_shaped(command), refusal[0]
        result = subprocess.CompletedProcess(
            list(command), code, captured.getvalue(), errors.getvalue()
        )
        return result, 0

    def _capture_errors(self) -> contextlib.AbstractContextManager[io.StringIO]:
        """Capture the wait's standard error when a router for it is installed."""
        if self._errors is None:
            return contextlib.nullcontext(io.StringIO())
        return self._errors.capture()

    async def _probed(
        self,
        coroutine: Coroutine[Any, Any, int],
        interval: float,
        probe: Callable[[], int],
        heartbeat: Callable[[], None] | None,
        refusal: list[int],
    ) -> int:
        task = asyncio.ensure_future(coroutine)
        self._running = (asyncio.get_running_loop(), task)
        try:
            while True:
                done, _pending = await asyncio.wait({task}, timeout=max(interval, 0.1))
                if done:
                    if task.cancelled():
                        refusal[0] = WAITER_STOPPED_REFUSAL
                        return 2
                    return task.result()
                if heartbeat is not None:
                    heartbeat()
                refusal[0] = WAITER_STOPPED_REFUSAL if self._stop.is_set() else probe()
                if refusal[0]:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    return 2
        finally:
            self._running = None


def _timeout_shaped(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Return the result a stopped wait reports: exit 2 and no output."""
    return subprocess.CompletedProcess(list(command), 2, "", "")


class PaneWaiter:
    """The seat's pane waiter: :func:`wait_and_wake` in one thread of the holder.

    The loop wakes the agent's pane on directed messages, exactly as
    ``agent-tmux wait`` does. Every exception stops the waiter only: the seat
    runs on, unwoken, and the reason is recorded. The loop already retries
    passing faults, so a loop that returns has a lasting cause and is not
    restarted.
    """

    def __init__(
        self,
        config: AgentTmuxConfig,
        *,
        router: OutputRouter,
        quiet: QuietTerminal | None = None,
        wake: Callable[..., int] = wait_and_wake,
        record: Callable[[AgentTmuxConfig, str], None] = record_waiter_stop,
        errors: OutputRouter | None = None,
    ) -> None:
        self.config = config
        self.reason: str | None = None
        self._stop = threading.Event()
        self._executor = InProcessWait(router=router, stop=self._stop, quiet=quiet, errors=errors)
        self._wake = wake
        self._record = record
        self._thread = threading.Thread(target=self._run, name="synapse-seat-waiter", daemon=True)

    def start(self) -> None:
        """Start the waiter thread."""
        self._thread.start()

    def _run(self) -> None:
        try:
            with wait_executor(self._executor):
                code = self._wake(self.config, sleeper=self._stop.wait)
            reason = f"the wait loop ended with {code}"
        except (Exception, SystemExit) as exc:  # a waiter fault never ends the seat
            reason = f"{type(exc).__name__}: {exc}"
        if self._stop.is_set():
            return
        self.reason = reason
        try:
            print(
                f"synapse seat-hold: the pane waiter of {self.config.identity} stopped: {reason}",
                file=sys.stderr,
                flush=True,
            )
            self._record(self.config, reason)
        except (Exception, SystemExit) as exc:  # nor does a failed record
            with contextlib.suppress(Exception):
                print(
                    f"synapse seat-hold: the waiter's stop was not recorded: {exc}", file=sys.stderr
                )

    def stop(self, timeout: float = WAITER_STOP_SECONDS) -> bool:
        """Stop the waiter and wait for its thread; return whether it has ended."""
        self._stop.set()
        self._executor.cancel()
        if self._thread.is_alive():
            self._thread.join(timeout)
        return not self._thread.is_alive()


class SeatWaiter:
    """The waiter and the quiet terminal of one running agent, opened and closed together."""

    def __init__(self, waiter: PaneWaiter, quiet: QuietTerminal) -> None:
        self.waiter = waiter
        self.quiet = quiet

    def close(self, timeout: float = WAITER_STOP_SECONDS) -> None:
        """Stop the waiter, then give descriptors 1 and 2 back to the terminal."""
        try:
            self.waiter.stop(timeout)
        finally:
            self.quiet.restore()


def open_seat_waiter(
    target: WakeTarget,
    *,
    identity: str,
    agent_argv: Sequence[str],
    uri: str,
    token: str | None,
    log_path: Path,
    router: OutputRouter,
    errors: OutputRouter | None = None,
) -> SeatWaiter:
    """Silence the holder's output and start the pane waiter for a running agent.

    Call it once the agent has started: the agent already holds its own copies
    of the terminal, so only the holder's descriptors 1 and 2 move to the log.
    The waiter never displaces a live waiter of the same seat
    (``first_no_takeover``).
    """
    config = AgentTmuxConfig(
        identity=identity,
        session=target.session,
        cwd=Path.cwd(),
        agent_command=tuple(agent_argv),
        tmux_bin=target.tmux_bin,
        uri=uri,
        token=token,
        pane_probe_interval=target.pane_probe_interval,
        first_no_takeover=True,
    )
    quiet = QuietTerminal(log_path)
    quiet.redirect()
    waiter = PaneWaiter(config, router=router, quiet=quiet, errors=errors)
    waiter.start()
    return SeatWaiter(waiter, quiet)
