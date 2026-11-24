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

import shlex

# Tmux and synapse subprocesses are this module's controlled process boundary.
import subprocess  # nosec B404 # fixed argument vectors only, never a shell
import time
from collections.abc import Mapping, Sequence
from functools import partial

from synapse_channel import agent_pane_state as _pane_classification
from synapse_channel.agent_pane_state import (
    bound_pane_state,
    bound_screen_safety,
    bound_staged_wake_was_consumed,
    provider_compatibility,
)
from synapse_channel.agent_tmux_provider import (
    _has_codex_update_override,
    _managed_agent_command,
    _provider_family,
    agent_binary,
)
from synapse_channel.agent_tmux_registry import (
    _pending_since,
    _pending_wake,
    _project_from_identity,
    _safe_key,
    _wake_prompt_staged,
    _write_registry,
)
from synapse_channel.agent_tmux_types import (
    AgentTmuxConfig,
    AgentTmuxStatus,
    AgentTmuxWakeResult,
    CommandRunner,
    Sleeper,
)
from synapse_channel.tmux_server import start_own_server

_pane_state = _pane_classification.pane_state
_screen_is_safe_for_submit = _pane_classification.screen_is_safe_for_submit
_staged_wake_was_consumed = _pane_classification.staged_wake_was_consumed

BINDING_REFUSAL_EXIT_CODE = 4
"""Stable refusal code when a live tmux session belongs to another identity."""

PANE_CAPTURE_MAX_CHARS = 64 * 1024
"""Maximum visible pane text retained by the safety classifier."""


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
        f"Synapse wake for {identity}. Routing hint: verify identity and newest exact-target "
        "inbox under repository rules. Reply once only if actionable; ignore routine peer "
        "status and broadcasts; continue any active user-directed task and wait only when "
        "otherwise idle."
    )


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
    if bound_staged_wake_was_consumed(config, provider, screen, prompt, runner=runner):
        return True, "wake prompt consumption observed"
    return False, "wake prompt remains staged"


def _pane_is_safe_for_submit(
    config: AgentTmuxConfig,
    provider: str,
    *,
    runner: CommandRunner,
    required_text: str | None = None,
) -> tuple[bool, str]:
    """Require a known idle composer and reject modal or busy provider state.

    Pane text is used only as a local classification input and is never included
    in diagnostics or registry records. ``provider`` is the family the wake was
    admitted for: an unknown provider is queued before any probe, and ambiguous
    screens queue the wake instead of emitting any key.
    """
    screen, detail = _capture_pane(config, runner=runner)
    if screen is None:
        return False, detail
    return bound_screen_safety(config, provider, screen, runner=runner, required_text=required_text)


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


def _bounded_native_command(
    args: Sequence[str],
    *,
    capture_output: bool = False,
    text: bool = False,
    check: bool = False,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Keep native startup controls within the existing bounded process boundary."""
    from synapse_channel.opencode_composer_files import ComposerError
    from synapse_channel.waker_commands import run_waker_command

    if env is not None:
        raise ComposerError("native startup controls do not replace the environment")
    try:
        return run_waker_command(
            list(args), capture_output=capture_output, text=text, check=check, timeout=2.0
        )
    except subprocess.SubprocessError as exc:
        raise ComposerError("native startup control command did not complete") from exc


def _launch_tmux_session(
    config: AgentTmuxConfig,
    target: str,
    provider_env: Sequence[str],
    *,
    runner: CommandRunner,
) -> subprocess.CompletedProcess[str]:
    """Create one new managed session under the caller's selected target."""
    command = shlex.join(["env", *provider_env, *_managed_agent_command(config)])
    start_own_server(config.tmux_bin, runner=runner)
    return runner(
        [
            config.tmux_bin,
            "new-session",
            "-d",
            "-s",
            target,
            *(arg for item in provider_env for arg in ("-e", item)),
            "-c",
            str(config.cwd),
            command,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def start_session(
    config: AgentTmuxConfig,
    *,
    runner: CommandRunner = subprocess.run,
) -> AgentTmuxWakeResult:
    """Start the tmux session running the agent when it is missing.

    When this command would also create the tmux server, the server is started
    in a transient systemd scope of its own, so that it does not live in the
    control group of the caller. A server inside a service unit is killed, with
    every session on it, when that unit is stopped. Without a systemd user
    manager the server starts as tmux starts it.

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
    if config.opencode_editor_readback:
        from synapse_channel.opencode_composer_startup import launch_editor_session

        native_runner = _bounded_native_command if runner is subprocess.run else runner
        startup = launch_editor_session(
            config,
            provider_env,
            launch=partial(_launch_tmux_session, config, runner=native_runner),
            runner=native_runner,
        )
        proc = subprocess.CompletedProcess(
            [], startup.returncode, stdout=startup.stdout, stderr=startup.stderr
        )
    else:
        proc = _launch_tmux_session(config, config.session, provider_env, runner=runner)
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
    from synapse_channel.pane_wake import PaneWakeBindings, inject_pane_wake

    del unsafe_payload
    ports = PaneWakeBindings(
        binding=partial(_session_binding, config, runner=runner),
        provider=partial(_provider_family, config),
        prompt=partial(build_wake_prompt, config.identity),
        staged=partial(_wake_prompt_staged, config),
        capture=partial(_capture_pane, config, runner=runner),
        consumed=partial(bound_staged_wake_was_consumed, config, runner=runner),
        screen_safe=partial(bound_screen_safety, config, runner=runner),
        pane_safe=partial(_pane_is_safe_for_submit, config, runner=runner),
        submit=partial(_submit_staged_wake, config, runner=runner, sleeper=sleeper),
        record=partial(_write_registry, config),
        buffer_key=partial(_safe_key, config.identity),
        result=AgentTmuxWakeResult,
        binding_refusal_code=BINDING_REFUSAL_EXIT_CODE,
        adopt_visible_stage=not config.opencode_editor_readback,
    )
    if config.opencode_editor_readback:
        from synapse_channel.opencode_composer_files import ComposerError
        from synapse_channel.opencode_composer_probe import native_wake_transaction

        try:
            with native_wake_transaction(config):
                return inject_pane_wake(config, ports, runner=runner, sleeper=sleeper)
        except ComposerError:
            return AgentTmuxWakeResult(
                False, False, 0, "wake deferred: native transaction unavailable"
            )
    return inject_pane_wake(config, ports, runner=runner, sleeper=sleeper)


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
        bound_pane_state(config, provider, screen, runner=runner)
        if agent_active and provider is not None and screen is not None
        else "unknown"
    )
    start_command = tuple(start_parts)
    codex_policy_aligned = provider != "codex" or _has_codex_update_override(start_command)
    compatibility_aligned, compatibility_detail = provider_compatibility(
        provider, pane_state, active=agent_active, codex_policy_aligned=codex_policy_aligned
    )
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
