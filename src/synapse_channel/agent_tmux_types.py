# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — vocabulary for the tmux wake transport
"""Names shared by the tmux wake transport, its registry and its wait loop."""

from __future__ import annotations

import subprocess  # nosec B404 # completed-process annotation, never launched here
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from synapse_channel.client.agent import DEFAULT_HUB_URI

DEFAULT_AGENT_PANE_COMMANDS = frozenset(
    {"codex", "node", "kimi", "claude", "grok", "gemini", "agy", "opencode"}
)
"""Pane command names that, on their own, indicate a live agent stack.

Terminal agents usually run under a shell (``fish``/``bash``), so the live agent
is normally identified from the pane's *start* command rather than its current
command. This set covers the cases where the agent binary is itself the pane
command — every first-class provider binary (Codex, Kimi, Claude, Grok, Gemini,
Antigravity's ``agy``, plus ``node`` for Node-launched stacks) — and it is unioned
with the per-config binary derived from :attr:`AgentTmuxConfig.agent_command`, so a
custom or renamed binary is always detected too.
"""

DEFAULT_SUBMIT_DELAY = 0.4
"""Seconds between the bracketed wake paste and its second safety probe.

The bridge never batches prompt text with Enter. It lets the provider composer
settle, re-captures the pane, and submits only if the exact prompt remains in a
known idle composer with no modal or busy marker. See :func:`inject_wake`.
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

DEFAULT_PANE_PROBE_INTERVAL = 5.0
"""Seconds a pane bridge may advertise before re-proving its live target."""


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
        env: Mapping[str, str] | None = None,
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
        Seconds between the bracketed paste and the second pane-safety probe.
    pane_probe_interval : float, optional
        Maximum seconds between live pane and identity-binding probes.
    token_file : pathlib.Path or None, optional
        Owner-only token file passed to the wait child instead of exposing the
        secret value in its process arguments.
    opencode_editor_readback : bool, optional
        Opt a new pinned OpenCode mini client into exact native input readback.
        Existing owner clients are never reconfigured or restarted.
    first_no_takeover : bool, optional
        Register the pane waiter's name without a takeover until one wait has
        held it, so a live waiter is never displaced (see
        :func:`_first_registration`). Such a waiter joins an existing session
        only; it never starts one.
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
    pane_probe_interval: float = DEFAULT_PANE_PROBE_INTERVAL
    token_file: Path | None = None
    opencode_editor_readback: bool = False
    first_no_takeover: bool = False


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
    binding_valid: bool = False
    binding_detail: str = "session binding was not verified"
    pane_state: str = "unknown"
    pending_wake: bool = False
    pending_since: float | None = None
    compatibility_aligned: bool = False
    compatibility_detail: str = "provider compatibility was not evaluated"
