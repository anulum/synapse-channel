# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tmux-backed wake transport for terminal coding agents
"""Tmux-backed wake transport for an existing terminal coding-agent session.

A terminal coding agent — Codex, Kimi K2, Claude Code, or any other agent that
reads its input from a tmux pane — does not re-engage on a Synapse message by
itself: its own ``synapse wait`` is a foreground tool call whose turn ends, so a
later wake never reaches the idle pane. This module is the external bridge that
closes that gap. It blocks on ``synapse wait`` for the target identity and, on
each directed message, types a fixed wake prompt into the agent's pane and
presses Enter so the agent reads its inbox and acts.

The transport is deliberately agent-agnostic: the only agent-specific input is
the launch command (:attr:`AgentTmuxConfig.agent_command`) and, for the status
probe, the binary name it resolves to. The wake prompt carries routing metadata
only and never the Synapse payload, so a remote sender cannot inject terminal
text.
"""

from __future__ import annotations

import json
import os

# Jitter spreads fleet-wide retry timing; it is not used for any security purpose.
import random  # nosec B311
import shlex

# Tmux and synapse subprocesses are this module's controlled process boundary.
import subprocess  # nosec B404
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import gettempdir
from typing import Protocol

from synapse_channel.client.agent import DEFAULT_HUB_URI

DEFAULT_AGENT_PANE_COMMANDS = frozenset({"codex", "node", "kimi", "claude"})
"""Pane command names that, on their own, indicate a live agent stack.

Terminal agents usually run under a shell (``fish``/``bash``), so the live agent
is normally identified from the pane's *start* command rather than its current
command. This set covers the cases where the agent binary is itself the pane
command; it is unioned with the per-config binary derived from
:attr:`AgentTmuxConfig.agent_command`.
"""

DEFAULT_SUBMIT_DELAY = 0.4
"""Seconds to wait between typing the wake prompt and pressing Enter.

A terminal agent UI ignores a submit key that arrives in the same
``tmux send-keys`` invocation as the prompt text: the Enter is processed before
the pasted line is committed to the input buffer, so the prompt is left sitting
unsent. Typing the text and pressing Enter as two calls separated by this delay
lets the UI settle and submit. See :func:`inject_wake`.
"""

DEFAULT_WAIT_RETRY_BASE = 1.0
"""Initial backoff, in seconds, after a failed ``synapse wait`` attempt."""

DEFAULT_WAIT_RETRY_CAP = 30.0
"""Maximum backoff, in seconds, between failed ``synapse wait`` attempts."""

DEFAULT_WAIT_RETRY_JITTER = 0.25
"""Fraction of the backoff added as random jitter, in ``[0, jitter]``.

A fleet of wakers that all lose the hub at the same instant — a hub restart — and
retry on the same exponential schedule would reconnect in a synchronised burst.
Spreading each delay by a random fraction de-correlates them so the hub does not
face a thundering herd on recovery.
"""


class Sleeper(Protocol):
    """Callable compatible with :func:`time.sleep` for injectable tests."""

    def __call__(self, seconds: float, /) -> object:
        """Sleep for ``seconds``."""


class CommandRunner(Protocol):
    """Callable compatible with :func:`subprocess.run` for injectable tests."""

    def __call__(
        self,
        args: Sequence[str],
        *,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        """Run ``args`` and return the completed process."""


@dataclass(frozen=True)
class AgentTmuxConfig:
    """Configuration for one tmux-backed terminal-agent wake target.

    Parameters
    ----------
    identity : str
        Synapse identity to wake (the agent's own name, not its ``-rx`` waiter).
    session : str
        tmux session target that hosts the agent's pane.
    cwd : Path
        Working directory used when starting the session.
    agent_command : tuple of str, optional
        Shell-style command that launches the agent (e.g. ``("codex",)`` or
        ``("kimi",)``). Its first token's basename is also the binary looked for
        when probing whether the pane is running the agent.
    pane_commands : frozenset of str, optional
        Pane current-command names that count as a live agent on their own.
    tmux_bin, synapse_bin : str, optional
        Executable names for tmux and the synapse CLI; injectable for testing.
    uri : str, optional
        Synapse hub URI.
    token : str or None, optional
        Shared-secret token for a secured hub.
    registry_dir : Path or None, optional
        Override for the local wake-target registry directory.
    submit_delay : float, optional
        Seconds between typing the prompt and pressing Enter.
    """

    identity: str
    session: str
    cwd: Path
    agent_command: tuple[str, ...] = ("codex",)
    pane_commands: frozenset[str] = DEFAULT_AGENT_PANE_COMMANDS
    tmux_bin: str = "tmux"
    synapse_bin: str = "synapse"
    uri: str = DEFAULT_HUB_URI
    token: str | None = None
    registry_dir: Path | None = None
    submit_delay: float = DEFAULT_SUBMIT_DELAY


@dataclass(frozen=True)
class AgentTmuxWakeResult:
    """Result returned by tmux start and wake operations."""

    injected: bool
    started: bool
    returncode: int
    detail: str


@dataclass(frozen=True)
class AgentTmuxStatus:
    """Health snapshot for one tmux-backed terminal-agent wake target."""

    identity: str
    session: str
    session_exists: bool
    pane_command: str | None
    pane_start_command: str | None
    agent_active: bool


@dataclass(frozen=True)
class RegistryRecord:
    """Local registry record for one tmux-backed terminal-agent wake target."""

    identity: str
    session: str
    cwd: str
    updated_at: float = field(default_factory=time.time)
    last_start_returncode: int | None = None
    last_inject_returncode: int | None = None


def agent_binary(config: AgentTmuxConfig) -> str:
    """Return the agent binary name probed for in the pane's start command.

    Parameters
    ----------
    config : AgentTmuxConfig
        Wake target whose ``agent_command`` names the launch binary.

    Returns
    -------
    str
        The basename of the first launch token (e.g. ``codex`` or ``kimi``), or
        an empty string when ``agent_command`` is empty.
    """
    if not config.agent_command:
        return ""
    return Path(config.agent_command[0]).name


def _safe_key(identity: str) -> str:
    """Return the filesystem-safe registry key for ``identity``."""
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in identity)


def _project_from_identity(identity: str) -> str:
    """Return the project segment for an identity."""
    return identity.split("/", 1)[0].strip()


def _registry_dir(config: AgentTmuxConfig) -> Path:
    """Return the registry directory for ``config``."""
    if config.registry_dir is not None:
        return config.registry_dir
    return Path(os.environ.get("XDG_RUNTIME_DIR") or gettempdir()) / "synapse-agent-tmux"


def registry_path(config: AgentTmuxConfig) -> Path:
    """Return the registry file path for ``config``."""
    return _registry_dir(config) / f"{_safe_key(config.identity)}.json"


def _write_registry(
    config: AgentTmuxConfig,
    *,
    last_start_returncode: int | None = None,
    last_inject_returncode: int | None = None,
) -> None:
    """Write the local wake-target registry atomically."""
    path = registry_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = RegistryRecord(
        identity=config.identity,
        session=config.session,
        cwd=str(config.cwd),
        last_start_returncode=last_start_returncode,
        last_inject_returncode=last_inject_returncode,
    )
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(record.__dict__, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def build_wake_prompt(identity: str) -> str:
    """Build the fixed prompt injected into the agent's tmux pane.

    The prompt contains only routing metadata. It deliberately excludes any
    Synapse message payload so a remote sender cannot inject terminal text.

    Parameters
    ----------
    identity : str
        Synapse identity whose inbox the agent is told to read.

    Returns
    -------
    str
        The fixed, payload-free wake instruction.
    """
    return (
        "Synapse wake: read your Synapse inbox for "
        f"{identity}, handle the newest directed message under the current "
        "repository rules, and reply once only if there is actionable directed "
        "work. If the inbox only contains routine peer status, broadcasts, or "
        "no actionable message, do not post status; stop and wait."
    )


def _has_session(config: AgentTmuxConfig, *, runner: CommandRunner) -> bool:
    """Return whether the configured tmux session exists."""
    proc = runner(
        [config.tmux_bin, "has-session", "-t", config.session],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def start_session(
    config: AgentTmuxConfig,
    *,
    runner: CommandRunner = subprocess.run,
) -> AgentTmuxWakeResult:
    """Start the tmux session running the agent when it is missing.

    Parameters
    ----------
    config : AgentTmuxConfig
        Wake target whose ``agent_command`` is launched in a new detached session.
    runner : CommandRunner, optional
        Subprocess runner; injectable for testing.

    Returns
    -------
    AgentTmuxWakeResult
        ``started`` is true only when a new session was created successfully.
    """
    if _has_session(config, runner=runner):
        _write_registry(config, last_start_returncode=0)
        return AgentTmuxWakeResult(
            injected=False,
            started=False,
            returncode=0,
            detail=f"tmux session {config.session} already exists",
        )

    command = shlex.join(
        [
            "env",
            f"SYN_PROJECT={_project_from_identity(config.identity)}",
            f"SYN_IDENTITY={config.identity}",
            "SYN_TMUX_PROVIDER=1",
            "SYNAPSE_AUTO_CONNECT=0",
            *config.agent_command,
        ]
    )
    proc = runner(
        [
            config.tmux_bin,
            "new-session",
            "-d",
            "-s",
            config.session,
            "-c",
            str(config.cwd),
            command,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    _write_registry(config, last_start_returncode=proc.returncode)
    return AgentTmuxWakeResult(
        injected=False,
        started=proc.returncode == 0,
        returncode=proc.returncode,
        detail="started" if proc.returncode == 0 else (proc.stderr or proc.stdout).strip(),
    )


def inject_wake(
    config: AgentTmuxConfig,
    *,
    runner: CommandRunner = subprocess.run,
    sleeper: Sleeper = time.sleep,
    unsafe_payload: str | None = None,
) -> AgentTmuxWakeResult:
    """Inject the fixed wake prompt into the configured tmux pane.

    The prompt text and the submit key are sent as two separate
    ``tmux send-keys`` invocations with a :attr:`AgentTmuxConfig.submit_delay`
    pause between them. A single invocation that appends the Enter key leaves the
    prompt unsent in the agent's input buffer, because the terminal UI commits
    the pasted line only after the Enter has already been consumed. The prompt is
    typed literally (``-l``) so no word in it is mistaken for a tmux key name.

    Parameters
    ----------
    config : AgentTmuxConfig
        Wake target whose ``submit_delay`` paces the two-step send.
    runner : CommandRunner, optional
        Subprocess runner; injectable for testing.
    sleeper : Sleeper, optional
        Sleep callable used for the submit delay; injectable for testing.
    unsafe_payload : str or None, optional
        Ignored. Present so callers may pass the raw wait output without it ever
        reaching the terminal, keeping a remote sender from injecting keystrokes.

    Returns
    -------
    AgentTmuxWakeResult
        ``injected`` is true only when both the type and submit calls succeed.
    """
    del unsafe_payload
    type_proc = runner(
        [
            config.tmux_bin,
            "send-keys",
            "-t",
            config.session,
            "-l",
            build_wake_prompt(config.identity),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if type_proc.returncode != 0:
        _write_registry(config, last_inject_returncode=type_proc.returncode)
        return AgentTmuxWakeResult(
            injected=False,
            started=False,
            returncode=type_proc.returncode,
            detail=(type_proc.stderr or type_proc.stdout).strip() or "type failed",
        )
    sleeper(max(config.submit_delay, 0.0))
    submit_proc = runner(
        [config.tmux_bin, "send-keys", "-t", config.session, "Enter"],
        capture_output=True,
        text=True,
        check=False,
    )
    _write_registry(config, last_inject_returncode=submit_proc.returncode)
    return AgentTmuxWakeResult(
        injected=submit_proc.returncode == 0,
        started=False,
        returncode=submit_proc.returncode,
        detail="injected"
        if submit_proc.returncode == 0
        else (submit_proc.stderr or submit_proc.stdout).strip() or "submit failed",
    )


def status(
    config: AgentTmuxConfig,
    *,
    runner: CommandRunner = subprocess.run,
) -> AgentTmuxStatus:
    """Return the tmux session and agent pane status for ``config``.

    Parameters
    ----------
    config : AgentTmuxConfig
        Wake target whose session and agent binary are probed.
    runner : CommandRunner, optional
        Subprocess runner; injectable for testing.

    Returns
    -------
    AgentTmuxStatus
        ``agent_active`` is true when the pane's current command is a known agent
        runtime or its start command launched this config's agent binary.
    """
    if not _has_session(config, runner=runner):
        return AgentTmuxStatus(
            identity=config.identity,
            session=config.session,
            session_exists=False,
            pane_command=None,
            pane_start_command=None,
            agent_active=False,
        )
    proc = runner(
        [
            config.tmux_bin,
            "display-message",
            "-p",
            "-t",
            config.session,
            "#{pane_current_command}\t#{pane_start_command}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    output = proc.stdout.strip() if proc.returncode == 0 else ""
    pane_command: str | None = None
    pane_start_command: str | None = None
    if output:
        pane_command, _, pane_start_command = output.partition("\t")
        pane_start_command = pane_start_command or None
    start_parts = shlex.split(pane_start_command.strip('"')) if pane_start_command else []
    binary = agent_binary(config)
    started_with_agent = bool(binary) and any(
        part == binary or part.endswith(f"/{binary}") for part in start_parts
    )
    return AgentTmuxStatus(
        identity=config.identity,
        session=config.session,
        session_exists=True,
        pane_command=pane_command,
        pane_start_command=pane_start_command,
        agent_active=pane_command in config.pane_commands or started_with_agent,
    )


def _wait_command(config: AgentTmuxConfig) -> list[str]:
    """Build the one-shot ``synapse wait`` command for ``config``."""
    command = [
        config.synapse_bin,
        "wait",
        "--name",
        f"{config.identity}-rx",
        "--for",
        config.identity,
        "--timeout",
        "0",
        "--directed-only",
        "--wake-capability",
        "pane_bridge",
    ]
    if config.uri != DEFAULT_HUB_URI:
        command.extend(["--uri", config.uri])
    if config.token:
        command.extend(["--token", config.token])
    return command


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

    Returns
    -------
    int
        ``0`` on completing ``max_wakes``, the failing wait return code once
        ``max_wait_failures`` consecutive failures are reached, or the failing
        inject return code when a tmux send fails.
    """
    wakes = 0
    consecutive_failures = 0
    while max_wakes is None or wakes < max_wakes:
        wait_proc = runner(_wait_command(config), capture_output=True, text=True, check=False)
        if wait_proc.returncode != 0:
            consecutive_failures += 1
            if max_wait_failures is not None and consecutive_failures >= max_wait_failures:
                return wait_proc.returncode
            sleeper(
                _backoff_delay(
                    consecutive_failures,
                    base=retry_base,
                    cap=retry_cap,
                    jitter=retry_jitter,
                    rng=rng,
                )
            )
            continue
        consecutive_failures = 0
        wake = inject_wake(config, runner=runner, sleeper=sleeper, unsafe_payload=wait_proc.stdout)
        if wake.returncode != 0:
            return wake.returncode
        wakes += 1
    return 0
