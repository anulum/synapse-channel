# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — wait loop for the tmux transport
"""Drive ``synapse wait`` and deliver the wake it reports."""

from __future__ import annotations

import os

# Jitter spreads fleet-wide retry timing; it is not used for any security purpose.
import random  # nosec B311 # jitter for retry timing, not a secret
import subprocess  # nosec B404 # fixed argument vectors only, never a shell
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from functools import partial
from typing import Protocol

from synapse_channel.agent_tmux import BINDING_REFUSAL_EXIT_CODE, inject_wake, status
from synapse_channel.agent_tmux_registry import _pending_wake
from synapse_channel.agent_tmux_types import (
    DEFAULT_WAIT_RETRY_BASE,
    DEFAULT_WAIT_RETRY_CAP,
    DEFAULT_WAIT_RETRY_JITTER,
    AgentTmuxConfig,
    AgentTmuxStatus,
    CommandRunner,
    Sleeper,
)
from synapse_channel.client.agent import DEFAULT_HUB_URI
from synapse_channel.pane_bridge_wait import DEFAULT_BRIDGE_WAIT_TIMEOUT, run_probed_wait
from synapse_channel.terminal_text import terminal_text
from synapse_channel.waiter_identity import pane_waiter_name


def _wait_command(
    config: AgentTmuxConfig, *, timeout: float | None = None, parent_pid: int | None = None
) -> list[str]:
    """Build the ``synapse wait`` command for ``config``.

    ``timeout`` defaults to the pane probe interval (the one-shot wait); the
    probed bridge wait passes a long timeout and its own pid, so the child stops
    with the bridge.
    """
    seconds = config.pane_probe_interval if timeout is None else timeout
    command = [
        config.synapse_bin,
        "wait",
        "--name",
        pane_waiter_name(config.identity),
        "--for",
        config.identity,
        "--timeout",
        f"{max(seconds, 0.1):g}",
        "--directed-only",
        "--wake-capability",
        "pane_bridge",
    ]
    if parent_pid is not None:
        command.extend(["--exit-with-parent", str(parent_pid)])
    if config.uri != DEFAULT_HUB_URI:
        command.extend(["--uri", config.uri])
    if config.token_file is not None:
        command.extend(["--token-file", str(config.token_file)])
    elif config.token:
        command.extend(["--token", config.token])
    if config.first_no_takeover:
        command.append("--no-takeover")
    return command


NO_TAKEOVER_UNSUPPORTED_EXIT_CODE = 2
"""Exit of a waiter whose ``synapse`` cannot wait without a takeover."""


def _first_registration(
    config: AgentTmuxConfig, wait_proc: subprocess.CompletedProcess[str], refusal: int
) -> tuple[AgentTmuxConfig, subprocess.CompletedProcess[str], int]:
    """Settle a waiter's first registration when it must not take its name over.

    With ``first_no_takeover`` each wait carries ``--no-takeover`` until one of
    them has held the name, which that wait says on standard error with the
    exact line :func:`~synapse_channel.cli_messaging_wait.registered_line` for
    this waiter's name. An exit code never counts as that proof: ``2``, for
    one, is also a usage error. A live connection on the name ends the waiter
    with ``synapse wait``'s name-live code, so it displaces nothing and wakes
    nothing. A ``synapse`` too old for the flag ends it as well, rather than
    wait with a takeover. Once a wait has held the name, later waits re-arm with
    a takeover as before: they meet only this waiter's own stale socket.

    Returns
    -------
    tuple[AgentTmuxConfig, subprocess.CompletedProcess[str], int]
        The configuration for the next wait, the wait itself, and the refusal
        (``0`` to go on).
    """
    from synapse_channel.cli_messaging_wait import WAIT_NAME_LIVE_EXIT_CODE, registered_line

    if not config.first_no_takeover or refusal:
        return config, wait_proc, refusal
    who = f"agent-tmux {terminal_text(config.identity)}"
    if wait_proc.returncode == WAIT_NAME_LIVE_EXIT_CODE:
        print(
            f"{who}: {pane_waiter_name(config.identity)} is held by a live connection; "
            "nothing is taken over or woken",
            file=sys.stderr,
            flush=True,
        )
        return config, wait_proc, WAIT_NAME_LIVE_EXIT_CODE
    stderr = wait_proc.stderr or ""
    if (
        wait_proc.returncode == 2
        and "unrecognized arguments" in stderr
        and "--no-takeover" in stderr
    ):
        print(
            f"{who}: {terminal_text(config.synapse_bin)} cannot wait without a takeover; "
            "this waiter stops rather than take the name over",
            file=sys.stderr,
            flush=True,
        )
        return config, wait_proc, NO_TAKEOVER_UNSUPPORTED_EXIT_CODE
    if registered_line(pane_waiter_name(config.identity)) in stderr.splitlines():
        return replace(config, first_no_takeover=False), wait_proc, 0
    return config, wait_proc, 0


def _backoff_delay(
    failures: int,
    *,
    base: float,
    cap: float,
    jitter: float = 0.0,
    rng: Callable[[], float] = random.random,
) -> float:
    """Return the capped exponential backoff for the ``failures``-th attempt.

    Parameters
    ----------
    failures : int
        Number of consecutive failures so far (``1`` for the first retry).
    base, cap : float
        Base delay and ceiling, in seconds, for the exponential schedule.
    jitter : float, optional
        Fraction of the capped delay added as random spread in ``[0, jitter]``.
    rng : Callable[[], float], optional
        Returns a float in ``[0, 1)``; injectable so tests stay deterministic.

    Returns
    -------
    float
        ``0.0`` for ``failures <= 0``; otherwise the capped exponential delay
        plus up to ``jitter`` of itself.
    """
    if failures <= 0:
        return 0.0
    capped = min(base * (2.0 ** (failures - 1)), cap)
    if jitter <= 0.0:
        return capped
    return capped * (1.0 + jitter * rng())


# Substring of the plain-passive yield line from cli_messaging_wait._cmd_wait.
# agent-tmux must never treat that exit as a directed wake.
_PROVIDER_YIELD_MARKER = "Yielding plain passive"


def _wait_process_env() -> dict[str, str]:
    """Return the sanitized environment for the one-shot wait subprocess."""
    env = dict(os.environ)
    # This marker is for provider panes and shell hooks. If the bridge's own
    # `synapse wait` inherits it, cli_messaging_wait yields immediately with a
    # success code and the bridge mistakes that yield for a real wake.
    env.pop("SYN_TMUX_PROVIDER", None)
    return env


def _is_provider_yield_stdout(stdout: str | None) -> bool:
    """Return whether ``stdout`` is the plain-passive yield, not a real wake."""
    return stdout is not None and _PROVIDER_YIELD_MARKER in stdout


def _target_refusal(snapshot: AgentTmuxStatus) -> int:
    """Check session identity before the agent that may run inside that session."""
    if not snapshot.session_exists:
        return 1
    if not snapshot.binding_valid:
        return BINDING_REFUSAL_EXIT_CODE
    return 0 if snapshot.agent_active else 1


_ONE_SHOT_WAIT_BINARIES: set[str] = set()
"""``synapse`` executables found too old for the long, parent-bound bridge wait."""

_PARENT_FLAG_REFUSAL = "unrecognized arguments: --exit-with-parent"
"""Argparse's refusal when a ``synapse`` predates ``wait --exit-with-parent``."""

_DELIVERED = -1
"""Private probe result: the pending wake was injected, so the pending wait stops.

No exit code is negative, so it never collides with a refusal, and
:func:`wait_and_wake` consumes it and never returns it."""


def _retry_exit(
    failures: int,
    max_failures: int | None,
    false_wake: bool,
    wait: subprocess.CompletedProcess[str],
) -> int | None:
    """Return the bridge exit code once consecutive failed waits reach the limit.

    Parameters
    ----------
    failures : int
        Consecutive failed waits so far.
    max_failures : int or None
        Limit; ``None`` retries indefinitely.
    false_wake : bool
        Whether the last wait was a provider-yield false wake.
    wait : subprocess.CompletedProcess[str]
        The last wait.

    Returns
    -------
    int or None
        ``3`` for a false wake or the wait's own code when the limit is reached,
        otherwise ``None`` (keep retrying).
    """
    if max_failures is None or failures < max_failures:
        return None
    return 3 if false_wake else wait.returncode


def _pending_attempt(config: AgentTmuxConfig, *, runner: CommandRunner, sleeper: Sleeper) -> int:
    """Retry a pending wake once; return ``_DELIVERED``, ``0`` while queued, or its failure."""
    wake = inject_wake(config, runner=runner, sleeper=sleeper)
    if wake.returncode != 0:
        return wake.returncode
    return _DELIVERED if wake.injected else 0


def _pending_step(
    config: AgentTmuxConfig,
    *,
    runner: CommandRunner,
    sleeper: Sleeper,
    heartbeat: Callable[[], None] | None,
) -> tuple[subprocess.CompletedProcess[str], int]:
    """Run one pending-wake cycle: re-prove the target, retry the wake, then wait.

    The pane capability stays advertised while a modal, busy turn, or
    asynchronous startup prevents injection, so new exact wakes coalesce into
    the already durable routing hint instead of disappearing into the passive
    mailbox sidecar alone. The long bridge wait retries the staged wake every
    probe interval instead of starting a new wait process each time.

    Returns
    -------
    tuple[subprocess.CompletedProcess[str], int]
        The wait and ``0``; or a timeout-shaped wait with ``_DELIVERED`` once the
        wake was injected, or with the target refusal or injection failure code.
    """
    attempt = partial(_pending_attempt, config, runner=runner, sleeper=sleeper)
    refusal = _target_refusal(status(config, runner=runner)) or attempt()
    if refusal:
        return subprocess.CompletedProcess(_wait_command(config), 2, "", ""), refusal
    return _bridge_wait(config, runner=runner, heartbeat=heartbeat, attempt=attempt)


class ProbedWait(Protocol):
    """Runs one probed bridge wait, as ``pane_bridge_wait.run_probed_wait`` does."""

    def __call__(
        self,
        command: Sequence[str],
        *,
        env: Mapping[str, str],
        interval: float,
        probe: Callable[[], int],
        heartbeat: Callable[[], None] | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], int]:
        """Run the wait ``command`` while ``probe`` re-proves its target every ``interval``."""


_PROBED_WAIT: ContextVar[ProbedWait | None] = ContextVar(
    "synapse_agent_tmux_probed_wait", default=None
)


@contextmanager
def wait_executor(executor: ProbedWait) -> Iterator[None]:
    """Run the calling thread's probed bridge waits through ``executor``.

    Internal to Synapse. By default a bridge wait is a ``synapse wait`` child
    process; ``seat-hold`` runs its pane waiter's waits inside its own process
    instead. The choice lives in the calling thread's context, so it never
    reaches another thread.
    """
    token = _PROBED_WAIT.set(executor)
    try:
        yield
    finally:
        _PROBED_WAIT.reset(token)


def _bridge_wait(
    config: AgentTmuxConfig,
    *,
    runner: CommandRunner,
    heartbeat: Callable[[], None] | None,
    attempt: Callable[[], int] | None = None,
) -> tuple[subprocess.CompletedProcess[str], int]:
    """Run one receiver wait and return it with a refusal code (``0`` when valid).

    With the real process runner the bridge keeps ONE wait connected for
    :data:`~synapse_channel.pane_bridge_wait.DEFAULT_BRIDGE_WAIT_TIMEOUT` seconds
    and re-proves the session, binding and agent pane every
    ``pane_probe_interval`` seconds while it runs; a refused probe stops the wait
    at once, so a lost pane keeps no receiver longer than before. A delivered
    message is followed by one more fresh probe before any injection. The wait
    only signals a wake: the message itself stays in the hub, so a refusal here
    loses nothing. An injected runner (tests), or a configured ``synapse`` too
    old to accept ``wait --exit-with-parent``, keeps the one-shot,
    interval-bounded wait; the old executable is detected from its argparse
    refusal once and remembered for the life of the bridge.

    While a wake is pending, ``attempt`` runs after every passed probe of the
    long wait, so a staged wake is retried each interval without a new wait
    process; a non-zero result stops the wait and is returned as the refusal.

    Parameters
    ----------
    config : AgentTmuxConfig
        Wake target.
    runner : CommandRunner
        Subprocess runner for tmux probes and, when injected, the wait itself.
    heartbeat : Callable[[], None] or None
        Supervisor heartbeat, called at every probe interval of a long wait.
    attempt : Callable[[], int] or None, optional
        Pending-wake injection attempt for the long wait's probe; ``0`` keeps
        waiting. The one-shot wait never calls it: its caller retries between waits.

    Returns
    -------
    tuple[subprocess.CompletedProcess[str], int]
        The finished wait and ``0``, or a timeout-shaped wait and the refusal code.
    """
    if runner is not subprocess.run or config.synapse_bin in _ONE_SHOT_WAIT_BINARIES:
        one_shot = runner(
            _wait_command(config),
            capture_output=True,
            text=True,
            check=False,
            env=_wait_process_env(),
        )
        return one_shot, 0

    def target() -> int:
        return _target_refusal(status(config, runner=runner))

    def probe() -> int:
        refusal = target()
        return refusal if refusal or attempt is None else attempt()

    wait_proc, refusal = (_PROBED_WAIT.get() or run_probed_wait)(
        _wait_command(config, timeout=DEFAULT_BRIDGE_WAIT_TIMEOUT, parent_pid=os.getpid()),
        env=_wait_process_env(),
        interval=config.pane_probe_interval,
        probe=probe,
        heartbeat=heartbeat,
    )
    if wait_proc.returncode == 2 and _PARENT_FLAG_REFUSAL in (wait_proc.stderr or ""):
        # An older `synapse` cannot bind its wait to the bridge; keep this
        # bridge on the one-shot, interval-bounded wait it always used. Say so
        # once in the bridge log, with the refusal, so the switch is visible.
        _ONE_SHOT_WAIT_BINARIES.add(config.synapse_bin)
        # The refusal comes from another program; controls in it are made
        # visible so they cannot alter a terminal that shows the log.
        refusal_line = terminal_text((wait_proc.stderr or "").strip().splitlines()[-1][:300])
        print(
            f"agent-tmux {terminal_text(config.identity)}: {terminal_text(config.synapse_bin)} "
            f"refused 'wait --exit-with-parent' ({refusal_line}); using the one-shot "
            f"{config.pane_probe_interval:g} s wait from now on",
            file=sys.stderr,
            flush=True,
        )
        return _bridge_wait(config, runner=runner, heartbeat=heartbeat, attempt=attempt)
    if not refusal and wait_proc.returncode == 0:
        refusal = target()
    return wait_proc, refusal


def wait_and_wake(
    config: AgentTmuxConfig,
    *,
    runner: CommandRunner = subprocess.run,
    max_wakes: int | None = None,
    sleeper: Sleeper = time.sleep,
    max_wait_failures: int | None = None,
    retry_base: float = DEFAULT_WAIT_RETRY_BASE,
    retry_cap: float = DEFAULT_WAIT_RETRY_CAP,
    retry_jitter: float = DEFAULT_WAIT_RETRY_JITTER,
    rng: Callable[[], float] = random.random,
    heartbeat: Callable[[], None] | None = None,
) -> int:
    """Run the wait loop and inject the fixed prompt after successful wakes.

    A failed ``synapse wait`` no longer ends the loop. The hub being briefly
    unreachable — a restart, a capacity eviction, a transient network drop — used
    to kill the waker permanently, leaving the agent pane unwoken until a human
    relaunched it. Instead each failure is retried with capped exponential
    backoff so the waker reattaches on its own once the hub returns.

    Parameters
    ----------
    config : AgentTmuxConfig
        Wake target driving the ``synapse wait`` command and tmux injection.
    runner : CommandRunner, optional
        Subprocess runner; injectable for testing.
    max_wakes : int or None, optional
        Stop after this many successful wakes; ``None`` runs until interrupted.
    sleeper : Sleeper, optional
        Sleep callable used for backoff and the submit delay; injectable for tests.
    max_wait_failures : int or None, optional
        Give up and return the wait return code after this many *consecutive*
        failures. ``None`` (the default) retries indefinitely, which is what a
        supervised daemon wants; the counter resets on every successful wait.
    retry_base, retry_cap : float, optional
        Base and ceiling, in seconds, for the exponential backoff between
        consecutive failed waits.
    retry_jitter : float, optional
        Fraction of each backoff added as random spread so a fleet of wakers does
        not reconnect in a synchronised burst after a shared hub outage.
    rng : Callable[[], float], optional
        Returns a float in ``[0, 1)`` for the jitter; injectable for tests.
    heartbeat : Callable[[], None] or None, optional
        Called before each bounded wait/injection cycle. A systemd supervisor
        uses this to prove that the main delivery loop, rather than a detached
        helper thread, is still advancing.

    Returns
    -------
    int
        ``0`` on completing ``max_wakes``, the failing wait return code once
        ``max_wait_failures`` consecutive failures are reached, or the failing
        inject return code when a tmux send fails.
    """
    wakes = 0
    pending = _pending_wake(config)
    consecutive_failures = 0
    backoff = partial(_backoff_delay, base=retry_base, cap=retry_cap, jitter=retry_jitter, rng=rng)
    while max_wakes is None or wakes < max_wakes:
        if heartbeat is not None:
            heartbeat()
        if pending:
            config, wait_proc, refusal = _first_registration(
                config, *_pending_step(config, runner=runner, sleeper=sleeper, heartbeat=heartbeat)
            )
            if refusal == _DELIVERED:
                pending = False
                wakes += 1
                continue
            if refusal:
                return refusal
            false_wake = wait_proc.returncode == 0 and _is_provider_yield_stdout(wait_proc.stdout)
            if wait_proc.returncode in {0, 2} and not false_wake:
                consecutive_failures = 0
                continue
            consecutive_failures += 1
            exit_code = _retry_exit(consecutive_failures, max_wait_failures, false_wake, wait_proc)
            if exit_code is not None:
                return exit_code
            sleeper(backoff(consecutive_failures))
            continue
        wait_proc, refusal = _bridge_wait(config, runner=runner, heartbeat=heartbeat)
        config, wait_proc, refusal = _first_registration(config, wait_proc, refusal)
        if refusal:
            return refusal
        # A provider-yield exit is rc=0 with the plain-passive message. Treating
        # that as a wake re-injects forever (false-wake loop). Count it as a
        # failed wait so backoff applies and the pane stays quiet.
        false_wake = wait_proc.returncode == 0 and _is_provider_yield_stdout(wait_proc.stdout)
        if wait_proc.returncode == 2 and not false_wake:
            # A bounded wait timeout is the bridge's liveness checkpoint. The
            # child has already disconnected, so prove the session, exact binding,
            # and agent pane again before advertising a fresh pane receiver.
            refusal = _target_refusal(status(config, runner=runner))
            if refusal:
                return refusal
            consecutive_failures = 0
            continue
        if wait_proc.returncode != 0 or false_wake:
            consecutive_failures += 1
            exit_code = _retry_exit(consecutive_failures, max_wait_failures, false_wake, wait_proc)
            if exit_code is not None:
                return exit_code
            sleeper(backoff(consecutive_failures))
            continue
        consecutive_failures = 0
        wake = inject_wake(config, runner=runner, sleeper=sleeper, unsafe_payload=wait_proc.stdout)
        if wake.returncode != 0:
            return wake.returncode
        if not wake.injected:
            pending = True
            continue
        wakes += 1
    return 0
