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
each directed message, safely pastes a fixed wake prompt only into a verified
idle provider composer. Busy, modal, unknown, and ambiguous panes queue the wake
without emitting a key and retry it after a bounded probe interval.

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
import re
import shlex

# Tmux and synapse subprocesses are this module's controlled process boundary.
import subprocess  # nosec B404
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from synapse_channel.client.agent import DEFAULT_HUB_URI
from synapse_channel.waiter_identity import pane_waiter_name

DEFAULT_AGENT_PANE_COMMANDS = frozenset(
    {"codex", "node", "kimi", "claude", "grok", "gemini", "opencode"}
)
"""Pane command names that, on their own, indicate a live agent stack.

Terminal agents usually run under a shell (``fish``/``bash``), so the live agent
is normally identified from the pane's *start* command rather than its current
command. This set covers the cases where the agent binary is itself the pane
command — every first-class provider binary (Codex, Kimi, Claude, Grok, Gemini,
plus ``node`` for Node-launched stacks) — and it is unioned with the per-config
binary derived from :attr:`AgentTmuxConfig.agent_command`, so a custom or renamed
binary is always detected too.
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

CODEX_MANAGED_UPDATE_CONFIG = "check_for_update_on_startup=false"
"""Codex override used when Synapse centrally manages provider launches."""

BINDING_REFUSAL_EXIT_CODE = 4
"""Stable refusal code when a live tmux session belongs to another identity."""

PANE_CAPTURE_MAX_CHARS = 64 * 1024
"""Maximum visible pane text retained by the safety classifier."""

_PROVIDER_IDLE_PATTERNS = {
    "codex": re.compile(r"(?m)^\s*›(?:\s|$)"),
    "claude": re.compile(r"(?m)^\s*❯(?:\s|$)"),
    "kimi": re.compile(r"(?m)^\s*(?:›|❯|>)\s*(?:$|Ask\b|Type\b)"),
    "grok": re.compile(r"(?m)^\s*(?:›|❯|>)\s*(?:$|Ask\b|Type\b)"),
    "gemini": re.compile(
        r"(?im)^\s*(?:›|❯|>)?\s*(?:Type your message|Type a message|Ask Gemini)\b"
    ),
    "opencode": re.compile(r"(?im)^\s*Ask anything\.\.\.\s*(?:\"|$)"),
}
"""Provider-specific idle composer markers; unknown providers fail closed."""

_UNSAFE_PANE_PATTERN = re.compile(
    r"(?im)(?:"
    r"^\s*(?:›|❯|>|→)?\s*\d+[.)]\s+"
    r"|\b(?:approve|approval|allow|deny|permission|confirm|confirmation|trust)\b"
    r"|\b(?:working|thinking|generating|running tool|press .*? to cancel|esc to interrupt)\b"
    r")"
)
"""Conservative modal/busy markers that override every idle marker."""

_CODEX_UPDATE_MODAL_PATTERN = re.compile(
    r"(?is)Update available!\s*\S+\s*->\s*\S+.*Skip until next version.*"
    r"Press enter to continue"
)
"""Exact Codex update chooser that blocks the interactive composer."""


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


@dataclass(frozen=True)
class RegistryRecord:
    """Local registry record for one tmux-backed terminal-agent wake target."""

    identity: str
    session: str
    cwd: str
    updated_at: float = field(default_factory=time.time)
    last_start_returncode: int | None = None
    last_inject_returncode: int | None = None
    pending_wake: bool = False
    wake_prompt_staged: bool = False
    pending_since: float | None = None


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
    """Return the registry directory for ``config`` (SCH-H-NEW-12 / REV-SEC-10).

    Materialises the directory through
    :func:`~synapse_channel.core.private_dir.ensure_private_dir` so a pre-existing
    symlink, foreign-owned, or loose directory fails closed.
    """
    from synapse_channel.core.private_dir import ensure_private_dir

    if config.registry_dir is not None:
        return ensure_private_dir(
            config.registry_dir,
            parents=True,
            purpose="agent-tmux registry directory",
        )
    root = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if root:
        runtime = Path(root) / "synapse-agent-tmux"
    else:
        from synapse_channel.reap import private_runtime_parent

        runtime = private_runtime_parent() / "synapse-agent-tmux"
    return ensure_private_dir(
        runtime,
        parents=True,
        purpose="agent-tmux registry directory",
    )


def registry_path(config: AgentTmuxConfig) -> Path:
    """Return the registry file path for ``config``."""
    return _registry_dir(config) / f"{_safe_key(config.identity)}.json"


def _write_registry(
    config: AgentTmuxConfig,
    *,
    last_start_returncode: int | None = None,
    last_inject_returncode: int | None = None,
    pending_wake: bool | None = None,
    wake_prompt_staged: bool | None = None,
) -> None:
    """Write the local wake-target registry atomically, preserving prior state."""
    # Parent is already an owner-only directory via :func:`_registry_dir`.
    path = registry_path(config)
    existing: dict[str, object] = {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            existing = loaded
    except (OSError, json.JSONDecodeError):
        pass
    existing_pending = existing.get("pending_wake") is True
    resolved_pending = pending_wake if pending_wake is not None else existing_pending
    existing_pending_since = existing.get("pending_since")
    pending_since = (
        float(existing_pending_since)
        if resolved_pending
        and existing_pending
        and isinstance(existing_pending_since, int | float)
        and not isinstance(existing_pending_since, bool)
        else time.time()
        if resolved_pending
        else None
    )
    record = RegistryRecord(
        identity=config.identity,
        session=config.session,
        cwd=str(config.cwd),
        last_start_returncode=(
            last_start_returncode
            if last_start_returncode is not None
            else _optional_int(existing.get("last_start_returncode"))
        ),
        last_inject_returncode=(
            last_inject_returncode
            if last_inject_returncode is not None
            else _optional_int(existing.get("last_inject_returncode"))
        ),
        pending_wake=resolved_pending,
        wake_prompt_staged=(
            wake_prompt_staged
            if wake_prompt_staged is not None
            else existing.get("wake_prompt_staged") is True
        ),
        pending_since=pending_since,
    )
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(record.__dict__, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def _optional_int(value: object) -> int | None:
    """Return a stored integer without accepting booleans or loose coercion."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _pending_wake(config: AgentTmuxConfig) -> bool:
    """Return whether a consumed wake remains queued for safe pane delivery."""
    try:
        payload = json.loads(registry_path(config).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and payload.get("pending_wake") is True


def _pending_since(config: AgentTmuxConfig) -> float | None:
    """Return when the current queued wake first became pending."""
    try:
        payload = json.loads(registry_path(config).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = payload.get("pending_since") if isinstance(payload, dict) else None
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


def _wake_prompt_staged(config: AgentTmuxConfig) -> bool:
    """Return whether the queued wake prompt is already present in the pane."""
    try:
        payload = json.loads(registry_path(config).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        isinstance(payload, dict)
        and payload.get("pending_wake") is True
        and payload.get("wake_prompt_staged") is True
    )


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
        f"Synapse wake for {identity}. Treat this as a routing hint, not as an "
        "instruction to replace an active user task. "
        f"If {identity} is not your current Synapse identity, ignore the wake "
        "unless you verify a new inbox item "
        f"addressed exactly to {identity}. Handle only the newest such item under "
        "the current repository rules and reply once only if it is actionable. "
        "Routine peer status, broadcasts, or no actionable exact-target message "
        "require no status reply; continue any active user-directed task and wait "
        "only when otherwise idle."
    )


def _provider_family(config: AgentTmuxConfig) -> str | None:
    """Return the supported terminal provider named by the launch command."""
    for token in config.agent_command:
        if token.startswith("-"):
            continue
        binary = Path(token).name.lower()
        for suffix in (".js", ".mjs", ".cjs", ".py"):
            if binary.endswith(suffix):
                binary = binary[: -len(suffix)]
        for provider in _PROVIDER_IDLE_PATTERNS:
            if binary == provider or binary.startswith(f"{provider}-"):
                return provider
    return None


def _has_codex_update_override(command: Sequence[str]) -> bool:
    """Return whether ``command`` explicitly configures Codex update checks."""
    return any("check_for_update_on_startup" in token.replace(" ", "") for token in command)


def _managed_agent_command(config: AgentTmuxConfig) -> tuple[str, ...]:
    """Return the provider command with centrally managed Codex update checks.

    Existing sessions are never rewritten or restarted. The override applies
    only when Synapse creates a new Codex tmux session, and an explicit owner
    override in ``agent_command`` always wins.
    """
    command = config.agent_command
    if _provider_family(config) != "codex" or _has_codex_update_override(command):
        return command
    return (*command, "--config", CODEX_MANAGED_UPDATE_CONFIG)


def _pane_state(provider: str, screen: str) -> str:
    """Return the provider pane's operational wake state."""
    if provider == "codex" and _CODEX_UPDATE_MODAL_PATTERN.search(screen):
        return "update-required"
    if _UNSAFE_PANE_PATTERN.search(screen):
        return "blocked"
    if _PROVIDER_IDLE_PATTERNS[provider].search(screen):
        return "idle"
    return "unknown"


def _capture_pane(config: AgentTmuxConfig, *, runner: CommandRunner) -> tuple[str | None, str]:
    """Capture the current visible pane without returning its contents in errors."""
    proc = runner(
        [
            config.tmux_bin,
            "capture-pane",
            "-t",
            config.session,
            "-p",
            "-J",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None, "pane capture failed"
    if len(proc.stdout) > PANE_CAPTURE_MAX_CHARS:
        return None, "pane capture exceeded the safety bound"
    return proc.stdout, "pane captured"


def _rendered_text_match(screen: str, required_text: str) -> re.Match[str] | None:
    """Return the last whitespace-tolerant match for terminal-rendered text."""
    pattern = r"\s+".join(re.escape(token) for token in required_text.split())
    matches = list(re.finditer(pattern, screen))
    return matches[-1] if matches else None


def _contains_rendered_text(screen: str, required_text: str) -> bool:
    """Return whether terminal wrapping preserved the complete required text."""
    return _rendered_text_match(screen, required_text) is not None


def _screen_is_safe_for_submit(
    provider: str,
    screen: str,
    *,
    required_text: str | None = None,
) -> tuple[bool, str]:
    """Classify an already captured pane for safe Enter submission."""
    state = _pane_state(provider, screen)
    if state == "update-required":
        return False, f"{provider} update chooser blocks the managed composer"
    if state == "blocked":
        return False, f"{provider} pane is busy, modal, or ambiguous"
    if state != "idle":
        return False, f"{provider} idle composer marker is absent"
    if required_text is not None and not _contains_rendered_text(screen, required_text):
        return False, "wake prompt was not accepted by the idle composer"
    return True, f"{provider} idle composer verified"


def _staged_wake_was_consumed(
    provider: str,
    screen: str,
    prompt: str,
) -> bool:
    """Return whether a staged prompt has observable at-most-once completion.

    A prompt that disappeared cannot safely be replayed. If it remains visible,
    it counts as consumed only when a newer provider idle composer appears after
    its final rendered occurrence. Busy or modal text alone is deliberately not
    an acknowledgement: current Codex can show asynchronous startup status while
    leaving the prompt unsubmitted in the active composer.
    """
    prompt_match = _rendered_text_match(screen, prompt)
    if prompt_match is None:
        return True
    return _PROVIDER_IDLE_PATTERNS[provider].search(screen, prompt_match.end()) is not None


def _observe_staged_wake(
    config: AgentTmuxConfig,
    provider: str,
    prompt: str,
    *,
    runner: CommandRunner,
) -> tuple[bool, str]:
    """Observe whether the pane consumed a staged wake without exposing text."""
    screen, detail = _capture_pane(config, runner=runner)
    if screen is None:
        return False, detail
    if _staged_wake_was_consumed(provider, screen, prompt):
        return True, "wake prompt consumption observed"
    return False, "wake prompt remains staged"


def _pane_is_safe_for_submit(
    config: AgentTmuxConfig,
    *,
    runner: CommandRunner,
    required_text: str | None = None,
) -> tuple[bool, str]:
    """Require a known idle composer and reject modal or busy provider state.

    Pane text is used only as a local classification input and is never included
    in diagnostics or registry records. Unknown providers and ambiguous screens
    queue the wake instead of emitting any key.
    """
    provider = _provider_family(config)
    if provider is None:
        return False, "provider has no fail-closed idle profile"
    screen, detail = _capture_pane(config, runner=runner)
    if screen is None:
        return False, detail
    return _screen_is_safe_for_submit(provider, screen, required_text=required_text)


def _submit_staged_wake(
    config: AgentTmuxConfig,
    provider: str,
    prompt: str,
    *,
    runner: CommandRunner,
    sleeper: Sleeper,
) -> AgentTmuxWakeResult:
    """Send Enter and require pane evidence before acknowledging delivery."""
    submit_proc = runner(
        [config.tmux_bin, "send-keys", "-t", config.session, "Enter"],
        capture_output=True,
        text=True,
        check=False,
    )
    if submit_proc.returncode != 0:
        _write_registry(
            config,
            last_inject_returncode=submit_proc.returncode,
            pending_wake=True,
            wake_prompt_staged=True,
        )
        return AgentTmuxWakeResult(
            injected=False,
            started=False,
            returncode=submit_proc.returncode,
            detail=(submit_proc.stderr or submit_proc.stdout).strip() or "submit failed",
        )

    sleeper(max(config.submit_delay, 0.0))
    consumed, observation = _observe_staged_wake(
        config,
        provider,
        prompt,
        runner=runner,
    )
    _write_registry(
        config,
        last_inject_returncode=0,
        pending_wake=not consumed,
        wake_prompt_staged=not consumed,
    )
    return AgentTmuxWakeResult(
        injected=consumed,
        started=False,
        returncode=0,
        detail="injected and consumption observed"
        if consumed
        else f"wake submit unacknowledged: {observation}",
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


def _session_binding(config: AgentTmuxConfig, *, runner: CommandRunner) -> tuple[bool, str]:
    """Verify the stable tmux session environment against ``config``.

    Session environment is set when :func:`start_session` creates the target and
    survives pane command changes. It is therefore the binding anchor for every
    later start/status/inject operation; pane title, command, cwd, and the local
    registry are useful observations but cannot prove which Synapse identity owns
    a live session.
    """
    proc = runner(
        [config.tmux_bin, "show-environment", "-t", config.session],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip() or "tmux session environment unavailable"
        return False, detail
    environment: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if not line or line.startswith("-") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in {"SYN_PROJECT", "SYN_IDENTITY"}:
            environment[key] = value
    expected_project = _project_from_identity(config.identity)
    observed_project = environment.get("SYN_PROJECT")
    observed_identity = environment.get("SYN_IDENTITY")
    if observed_project == expected_project and observed_identity == config.identity:
        return True, (f"verified SYN_PROJECT={expected_project} and SYN_IDENTITY={config.identity}")
    observed = (
        f"SYN_PROJECT={observed_project or '<missing>'}, "
        f"SYN_IDENTITY={observed_identity or '<missing>'}"
    )
    expected = f"SYN_PROJECT={expected_project}, SYN_IDENTITY={config.identity}"
    return (
        False,
        f"session {config.session} binding mismatch: observed {observed}; expected {expected}",
    )


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
        binding_valid, binding_detail = _session_binding(config, runner=runner)
        if not binding_valid:
            return AgentTmuxWakeResult(
                injected=False,
                started=False,
                returncode=BINDING_REFUSAL_EXIT_CODE,
                detail=f"refusing existing tmux session: {binding_detail}",
            )
        _write_registry(config, last_start_returncode=0)
        return AgentTmuxWakeResult(
            injected=False,
            started=False,
            returncode=0,
            detail=f"tmux session {config.session} already exists with {binding_detail}",
        )

    provider_env = [
        f"SYN_PROJECT={_project_from_identity(config.identity)}",
        f"SYN_IDENTITY={config.identity}",
        "SYN_TMUX_PROVIDER=1",
        "SYNAPSE_AUTO_CONNECT=0",
    ]
    command = shlex.join(["env", *provider_env, *_managed_agent_command(config)])
    proc = runner(
        [
            config.tmux_bin,
            "new-session",
            "-d",
            "-s",
            config.session,
            *(arg for item in provider_env for arg in ("-e", item)),
            "-c",
            str(config.cwd),
            command,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    returncode = proc.returncode
    detail = "started" if proc.returncode == 0 else (proc.stderr or proc.stdout).strip()
    if proc.returncode == 0:
        binding_valid, binding_detail = _session_binding(config, runner=runner)
        if not binding_valid:
            returncode = BINDING_REFUSAL_EXIT_CODE
            detail = f"started tmux session but refusing unverified binding: {binding_detail}"
    _write_registry(config, last_start_returncode=returncode)
    return AgentTmuxWakeResult(
        injected=False,
        started=returncode == 0,
        returncode=returncode,
        detail=detail,
    )


def inject_wake(
    config: AgentTmuxConfig,
    *,
    runner: CommandRunner = subprocess.run,
    sleeper: Sleeper = time.sleep,
    unsafe_payload: str | None = None,
) -> AgentTmuxWakeResult:
    """Inject the fixed wake prompt only into a verified idle provider composer.

    The pane is captured before any mutation and must match a provider-specific
    idle profile with no modal/busy marker. The fixed prompt is then delivered as
    one bracketed paste, not as individual shortcut-capable keys. The private
    registry records that paste before the second safety probe, so a queued retry
    never pastes the same wake a second time. After the submit delay a second
    capture must still show both the idle composer and the exact fixed prompt
    before Enter is sent. A zero-returning key send is not delivery evidence: a
    post-submit capture must show that the prompt disappeared or that a newer
    idle composer appeared after it. Otherwise the one staged prompt remains
    pending and :func:`wait_and_wake` retries Enter after a bounded quiet
    interval, without pasting the text again.

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
        ``injected`` is true only when the safety probes, paste, submit, and
        observable consumption succeed. A queued wake returns zero with
        ``injected`` false.
    """
    del unsafe_payload
    binding_valid, binding_detail = _session_binding(config, runner=runner)
    if not binding_valid:
        _write_registry(config, pending_wake=True)
        return AgentTmuxWakeResult(
            injected=False,
            started=False,
            returncode=BINDING_REFUSAL_EXIT_CODE,
            detail=f"refusing wake injection: {binding_detail}",
        )
    provider = _provider_family(config)
    if provider is None:
        _write_registry(config, last_inject_returncode=0, pending_wake=True)
        return AgentTmuxWakeResult(
            injected=False,
            started=False,
            returncode=0,
            detail="wake queued: provider has no fail-closed idle profile",
        )
    prompt = build_wake_prompt(config.identity)
    prompt_staged = _wake_prompt_staged(config)
    if prompt_staged:
        screen, capture_detail = _capture_pane(config, runner=runner)
        if screen is None:
            safe, safety_detail = False, capture_detail
        elif _staged_wake_was_consumed(provider, screen, prompt):
            _write_registry(
                config,
                last_inject_returncode=0,
                pending_wake=False,
                wake_prompt_staged=False,
            )
            return AgentTmuxWakeResult(
                injected=True,
                started=False,
                returncode=0,
                detail="staged wake consumption observed; not repasted",
            )
        else:
            safe, safety_detail = _screen_is_safe_for_submit(
                provider,
                screen,
                required_text=prompt,
            )
    else:
        safe, safety_detail = _pane_is_safe_for_submit(config, runner=runner)
    if not safe:
        _write_registry(
            config,
            last_inject_returncode=0,
            pending_wake=True,
            wake_prompt_staged=prompt_staged,
        )
        return AgentTmuxWakeResult(
            injected=False,
            started=False,
            returncode=0,
            detail=f"wake queued: {safety_detail}",
        )
    if prompt_staged:
        return _submit_staged_wake(
            config,
            provider,
            prompt,
            runner=runner,
            sleeper=sleeper,
        )
    buffer_name = f"synapse-wake-{_safe_key(config.identity)}"
    buffer_proc = runner(
        [
            config.tmux_bin,
            "set-buffer",
            "-b",
            buffer_name,
            "--",
            prompt,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if buffer_proc.returncode != 0:
        _write_registry(
            config,
            last_inject_returncode=buffer_proc.returncode,
            pending_wake=True,
            wake_prompt_staged=False,
        )
        return AgentTmuxWakeResult(
            injected=False,
            started=False,
            returncode=buffer_proc.returncode,
            detail=(buffer_proc.stderr or buffer_proc.stdout).strip() or "buffer setup failed",
        )
    paste_proc = runner(
        [
            config.tmux_bin,
            "paste-buffer",
            "-b",
            buffer_name,
            "-d",
            "-p",
            "-t",
            config.session,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if paste_proc.returncode != 0:
        _write_registry(
            config,
            last_inject_returncode=paste_proc.returncode,
            pending_wake=True,
            wake_prompt_staged=False,
        )
        return AgentTmuxWakeResult(
            injected=False,
            started=False,
            returncode=paste_proc.returncode,
            detail=(paste_proc.stderr or paste_proc.stdout).strip() or "paste failed",
        )
    _write_registry(
        config,
        last_inject_returncode=0,
        pending_wake=True,
        wake_prompt_staged=True,
    )
    sleeper(max(config.submit_delay, 0.0))
    safe, safety_detail = _pane_is_safe_for_submit(
        config,
        runner=runner,
        required_text=prompt,
    )
    if not safe:
        _write_registry(
            config,
            last_inject_returncode=0,
            pending_wake=True,
            wake_prompt_staged=True,
        )
        return AgentTmuxWakeResult(
            injected=False,
            started=False,
            returncode=0,
            detail=f"wake queued after paste: {safety_detail}",
        )
    return _submit_staged_wake(
        config,
        provider,
        prompt,
        runner=runner,
        sleeper=sleeper,
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
            binding_valid=False,
            binding_detail=f"tmux session {config.session} is missing",
            pane_state="missing",
        )
    binding_valid, binding_detail = _session_binding(config, runner=runner)
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
    agent_active = binding_valid and (pane_command in config.pane_commands or started_with_agent)
    provider = _provider_family(config)
    screen, _capture_detail = _capture_pane(config, runner=runner)
    pane_state = (
        _pane_state(provider, screen)
        if agent_active and provider is not None and screen is not None
        else "unknown"
    )
    start_command = tuple(start_parts)
    codex_policy_aligned = provider != "codex" or _has_codex_update_override(start_command)
    compatibility_aligned = bool(
        agent_active
        and provider is not None
        and pane_state in {"idle", "blocked"}
        and codex_policy_aligned
    )
    if not agent_active:
        compatibility_detail = "active provider pane was not verified"
    elif provider is None:
        compatibility_detail = "provider has no supported pane profile"
    elif pane_state == "update-required":
        compatibility_detail = "provider update chooser blocks automatic wake delivery"
    elif not codex_policy_aligned:
        compatibility_detail = "Codex update checks are not centrally managed for this session"
    elif pane_state == "unknown":
        compatibility_detail = "provider pane readiness is unknown"
    else:
        compatibility_detail = "provider launch and pane profile are aligned"
    return AgentTmuxStatus(
        identity=config.identity,
        session=config.session,
        session_exists=True,
        pane_command=pane_command,
        pane_start_command=pane_start_command,
        agent_active=agent_active,
        binding_valid=binding_valid,
        binding_detail=binding_detail,
        pane_state=pane_state,
        pending_wake=_pending_wake(config),
        pending_since=_pending_since(config),
        compatibility_aligned=compatibility_aligned,
        compatibility_detail=compatibility_detail,
    )


def _wait_command(config: AgentTmuxConfig) -> list[str]:
    """Build the one-shot ``synapse wait`` command for ``config``."""
    command = [
        config.synapse_bin,
        "wait",
        "--name",
        pane_waiter_name(config.identity),
        "--for",
        config.identity,
        "--timeout",
        f"{max(config.pane_probe_interval, 0.1):g}",
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
    pending = _pending_wake(config)
    consecutive_failures = 0
    while max_wakes is None or wakes < max_wakes:
        if pending:
            snapshot = status(config, runner=runner)
            if not snapshot.session_exists or not snapshot.agent_active:
                return 1
            if not snapshot.binding_valid:
                return BINDING_REFUSAL_EXIT_CODE
            wake = inject_wake(config, runner=runner, sleeper=sleeper)
            if wake.returncode != 0:
                return wake.returncode
            if wake.injected:
                pending = False
                wakes += 1
            else:
                # Keep advertising the pane capability while a modal, busy turn,
                # or asynchronous startup prevents injection. New exact wakes
                # coalesce into the already durable routing hint instead of
                # disappearing into the passive mailbox sidecar alone.
                wait_proc = runner(
                    _wait_command(config),
                    capture_output=True,
                    text=True,
                    check=False,
                    env=_wait_process_env(),
                )
                false_wake = wait_proc.returncode == 0 and _is_provider_yield_stdout(
                    wait_proc.stdout
                )
                if wait_proc.returncode in {0, 2} and not false_wake:
                    consecutive_failures = 0
                    continue
                consecutive_failures += 1
                if max_wait_failures is not None and consecutive_failures >= max_wait_failures:
                    return 3 if false_wake else wait_proc.returncode
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
        wait_proc = runner(
            _wait_command(config),
            capture_output=True,
            text=True,
            check=False,
            env=_wait_process_env(),
        )
        # A provider-yield exit is rc=0 with the plain-passive message. Treating
        # that as a wake re-injects forever (false-wake loop). Count it as a
        # failed wait so backoff applies and the pane stays quiet.
        false_wake = wait_proc.returncode == 0 and _is_provider_yield_stdout(wait_proc.stdout)
        if wait_proc.returncode == 2 and not false_wake:
            # A bounded wait timeout is the bridge's liveness checkpoint. The
            # child has already disconnected, so prove the session, exact binding,
            # and agent pane again before advertising a fresh pane receiver.
            snapshot = status(config, runner=runner)
            if not snapshot.session_exists:
                return 1
            if not snapshot.binding_valid:
                return BINDING_REFUSAL_EXIT_CODE
            if not snapshot.agent_active:
                return 1
            consecutive_failures = 0
            continue
        if wait_proc.returncode != 0 or false_wake:
            consecutive_failures += 1
            if max_wait_failures is not None and consecutive_failures >= max_wait_failures:
                return 3 if false_wake else wait_proc.returncode
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
        if not wake.injected:
            pending = True
            continue
        wakes += 1
    return 0
