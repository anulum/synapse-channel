# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — systemd lifecycle for active terminal-agent wakers
"""Install, inhibit, resume, and inspect exact-seat active-waker services."""

from __future__ import annotations

import subprocess  # nosec B404
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import Protocol

from synapse_channel.agent_tmux import AgentTmuxConfig, AgentTmuxStatus, status
from synapse_channel.client.agent import DEFAULT_HUB_URI
from synapse_channel.service_setup import (
    default_synapse_bin,
    escaped_instance,
    render_waker_unit,
    user_systemd_dir,
)
from synapse_channel.terminal_text import terminal_text
from synapse_channel.waker_commands import (
    DEFAULT_COMMAND_TIMEOUT,
    run_waker_command,
)
from synapse_channel.waker_commands import (
    command_timeout as validate_command_timeout,
)
from synapse_channel.waker_config import (
    DESIRED_ARMED,
    DESIRED_INHIBITED,
    WakerConfig,
    WakerConfigError,
    clean_waker_text,
    load_waker_config,
    save_waker_config,
    validate_waker_config,
    waker_config_path,
)
from synapse_channel.waker_lock import waker_control_lock
from synapse_channel.waker_transition import transition_state, waker_transition

WAKER_TEMPLATE = "synapse-waker@.service"
"""Systemd user-unit template used by active wakers."""


class CommandRunner(Protocol):
    """Callable compatible with fixed system service commands."""

    def __call__(
        self,
        args: list[str],
        *,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        """Run ``args`` and return its completed process."""


@dataclass(frozen=True, slots=True)
class WakerOperationResult:
    """Result and operator-facing evidence from a lifecycle operation."""

    ok: bool
    lines: tuple[str, ...]
    generation: int | None = None


@dataclass(frozen=True, slots=True)
class WakerStatus:
    """Desired, control-outcome, service-manager and provider state for one waker.

    control_state is idle only when no interrupted or active control is observed.
    A live service alone is not ready while control_state is pending or uncertain.
    """

    identity: str
    desired_state: str
    generation: int
    inhibit_reason: str | None
    unit: str
    service_active: str
    service_substate: str
    restart_count: int | None
    main_status: int | None
    provider: AgentTmuxStatus
    service_query_ok: bool
    control_state: str = "idle"

    @property
    def ready(self) -> bool:
        """Return whether desired, service, binding, and provider state are healthy."""
        return (
            self.control_state == "idle"
            and self.desired_state == DESIRED_ARMED
            and self.service_query_ok
            and self.service_active == "active"
            and self.provider.session_exists
            and self.provider.binding_valid
            and self.provider.agent_active
        )


def _run_command(
    command: list[str], *, runner: CommandRunner
) -> tuple[subprocess.CompletedProcess[str] | None, str]:
    """Run one fixed command and return a sanitised failure description."""
    try:
        completed = runner(command, capture_output=True, text=True, check=False)
    except subprocess.TimeoutExpired:
        return None, f"timed out: {' '.join(command)} — systemd job outcome is unknown"
    except OSError as exc:
        return None, f"failed: {' '.join(command)} — {exc}"
    if completed.returncode == 0:
        return completed, ""
    detail = terminal_text((completed.stderr or completed.stdout).strip())
    return completed, f"failed: {' '.join(command)}" + (f" — {detail}" if detail else "")


def _unit(identity: str, *, runner: CommandRunner) -> str:
    """Return the exact escaped systemd unit for ``identity``."""
    try:
        return escaped_instance(identity, template=WAKER_TEMPLATE, runner=runner)
    except subprocess.TimeoutExpired as exc:
        raise WakerConfigError("systemd unit name lookup timed out") from exc


def _runner(runner: CommandRunner | None, timeout: float) -> CommandRunner:
    limit = validate_command_timeout(timeout)
    return runner if runner is not None else partial(run_waker_command, timeout=limit)


def install_waker(
    *,
    identity: str,
    session: str,
    cwd: Path,
    agent_command: Sequence[str],
    tmux_bin: str = "tmux",
    synapse_bin: str | None = None,
    uri: str = DEFAULT_HUB_URI,
    token_file: Path | None = None,
    submit_delay: float = 0.35,
    pane_probe_interval: float = 30.0,
    start: bool = False,
    home: Path | None = None,
    runner: CommandRunner | None = None,
    command_timeout: float = DEFAULT_COMMAND_TIMEOUT,
    clock: Callable[[], float] = time.time,
    platform: str = sys.platform,
) -> WakerOperationResult:
    """Install or update a waker while holding its lock through service commands.

    A competing lifecycle operation is refused without changing configuration.
    The lock is released after command completion or failure; desired state is
    retained on service failure and does not certify the applied service state.

    Parameters
    ----------
    identity, session : str
        Exact waker identity and its existing provider tmux session.
    cwd : pathlib.Path
        Provider working directory.
    agent_command : sequence of str
        Provider command tokens stored in the configuration.
    tmux_bin, synapse_bin : str or None
        Executables used by the bridge; Synapse defaults to its installed path.
    uri : str
        Hub endpoint.
    token_file : pathlib.Path or None
        Owner-only credential file, never an inline persisted token.
    submit_delay, pane_probe_interval : float
        Provider submit and liveness-probe intervals in seconds.
    start : bool
        Also enable and start the service. False permits configuration repair
        without clearing an existing recovery gate.
    home : pathlib.Path or None
        Configuration root, defaulting to the current user's home.
    runner : CommandRunner or None
        Injected service-command boundary. None uses the bounded local runner;
        an injected runner is responsible for its own timeout behaviour.
    command_timeout : float
        Positive finite wait limit in seconds for each default-runner command.
    clock : callable
        Source of Unix timestamps for configuration updates.
    platform : str
        Platform selector; service installation requires Linux or WSL/systemd.

    Returns
    -------
    WakerOperationResult
        Configuration generation and command evidence, not provider readiness.
    """
    runner = _runner(runner, command_timeout)
    if not platform.startswith("linux"):
        return WakerOperationResult(
            False,
            ("active-waker service installation requires Linux or WSL with systemd",),
        )
    executable = synapse_bin or default_synapse_bin()
    try:
        with waker_control_lock(identity, home=home):
            existing_path = waker_config_path(identity, home=home)
            existing = load_waker_config(identity, home=home) if existing_path.exists() else None
            generation = existing.generation + 1 if existing is not None else 1
            config = WakerConfig(
                identity=identity,
                session=session,
                cwd=str(cwd.expanduser().resolve()),
                agent_command=tuple(agent_command),
                tmux_bin=tmux_bin,
                synapse_bin=executable,
                uri=uri,
                token_file=(
                    str(token_file.expanduser().resolve()) if token_file is not None else None
                ),
                registry_dir=str(
                    (
                        (Path.home() if home is None else home)
                        / ".local/share/synapse/runtime/agent-tmux"
                    )
                    .expanduser()
                    .resolve()
                ),
                submit_delay=submit_delay,
                pane_probe_interval=pane_probe_interval,
                generation=generation,
                updated_at=clock(),
                desired_state=(existing.desired_state if existing is not None else DESIRED_ARMED),
                inhibit_reason=(existing.inhibit_reason if existing is not None else None),
            )
            validate_waker_config(config)
            unit = _unit(identity, runner=runner)
            unit_path = user_systemd_dir(home=home) / WAKER_TEMPLATE
            unit_text = render_waker_unit(synapse_bin=executable)
            with waker_transition(
                identity, generation, "install" if start else "configure", home=home
            ) as transition:
                unit_path.parent.mkdir(parents=True, exist_ok=True)
                unit_path.write_text(unit_text, encoding="utf-8")
                config_path = save_waker_config(config, home=home)
                lines = [f"wrote {unit_path}", f"wrote {config_path}", f"generation: {generation}"]
                if config.desired_state == DESIRED_INHIBITED:
                    lines.extend(
                        (
                            "desired state remains inhibited; service was not started",
                            f"run: synapse waker resume {identity}",
                        )
                    )
                    transition.complete()
                    return WakerOperationResult(True, tuple(lines), generation)
                if not start:
                    lines.extend(
                        (
                            "run: systemctl --user daemon-reload",
                            f"run: systemctl --user enable --now {unit}",
                        )
                    )
                    transition.complete()
                    return WakerOperationResult(True, tuple(lines), generation)
                for command in (
                    ["systemctl", "--user", "daemon-reload"],
                    ["systemctl", "--user", "enable", unit],
                    ["systemctl", "--user", "restart", unit],
                ):
                    _completed, failure = _run_command(command, runner=runner)
                    if failure:
                        lines.append(failure)
                        return WakerOperationResult(False, tuple(lines), generation)
                    lines.append(f"ok: {' '.join(command)}")
                transition.complete()
                return WakerOperationResult(True, tuple(lines), generation)

    except (OSError, ValueError) as exc:
        return WakerOperationResult(False, (f"failed to install waker — {exc}",))


def _require_generation(config: WakerConfig, expected: int | None) -> None:
    """Refuse a lifecycle mutation based on stale observed configuration."""
    if expected is not None and config.generation != expected:
        raise WakerConfigError(
            f"generation changed: expected {expected}, found {config.generation}"
        )


def inhibit_waker(
    identity: str,
    *,
    reason: str,
    expected_generation: int | None = None,
    home: Path | None = None,
    runner: CommandRunner | None = None,
    command_timeout: float = DEFAULT_COMMAND_TIMEOUT,
    clock: Callable[[], float] = time.time,
) -> WakerOperationResult:
    """Persist inhibition and stop the service under one identity lock.

    Parameters
    ----------
    identity, reason : str
        Exact waker identity and persistent inhibition reason.
    expected_generation : int or None
        Refuse a stale observed configuration when provided.
    home : pathlib.Path or None
        Configuration root.
    runner : CommandRunner or None
        Injected command boundary, or the bounded local runner by default.
    command_timeout : float
        Positive finite per-command wait in seconds for the default runner.
    clock : callable
        Source of Unix timestamps.

    Returns
    -------
    WakerOperationResult
        Stop outcome. A successful stop does not acknowledge earlier uncertainty.

    Raises
    ------
    WakerLockError
        Another lifecycle operation for this identity is still running.
    WakerConfigError
        The expected configuration generation no longer matches.
    """
    runner = _runner(runner, command_timeout)
    with waker_control_lock(identity, home=home):
        config = load_waker_config(identity, home=home)
        _require_generation(config, expected_generation)
        updated = replace(
            config,
            desired_state=DESIRED_INHIBITED,
            inhibit_reason=clean_waker_text(reason, field="reason"),
            generation=config.generation + 1,
            updated_at=clock(),
        )
        unit = _unit(identity, runner=runner)
        with waker_transition(identity, updated.generation, "stop", home=home) as transition:
            path = save_waker_config(updated, home=home)
            _completed, failure = _run_command(["systemctl", "--user", "stop", unit], runner=runner)
            lines = [f"inhibited {identity} at generation {updated.generation}", f"wrote {path}"]
            if failure:
                lines.extend((failure, "desired state remains inhibited; a later start cannot run"))
                return WakerOperationResult(False, tuple(lines), updated.generation)
            lines.append(f"ok: systemctl --user stop {unit}")
            transition.complete()
            return WakerOperationResult(True, tuple(lines), updated.generation)


def resume_waker(
    identity: str,
    *,
    expected_generation: int | None = None,
    acknowledge_uncertain: bool = False,
    home: Path | None = None,
    runner: CommandRunner | None = None,
    command_timeout: float = DEFAULT_COMMAND_TIMEOUT,
    clock: Callable[[], float] = time.time,
) -> WakerOperationResult:
    """Clear inhibition and start the service under one identity lock.

    Parameters
    ----------
    identity : str
        Exact waker identity.
    expected_generation : int or None
        Refuse stale control decisions; mandatory with recovery acknowledgement.
    acknowledge_uncertain : bool
        Operator assertion that earlier control processes and manager jobs have
        settled. This is not inferred from command exit or service liveness.
    home : pathlib.Path or None
        Configuration root.
    runner : CommandRunner or None
        Injected command boundary, or the bounded local runner by default.
    command_timeout : float
        Positive finite per-command wait in seconds for the default runner.
    clock : callable
        Source of Unix timestamps.

    Returns
    -------
    WakerOperationResult
        Start-command outcome and new generation, not provider readiness.

    Raises
    ------
    WakerLockError
        Another lifecycle operation for this identity is still running.
    WakerConfigError
        The expected configuration generation no longer matches.
    WakerTransitionError
        An uncertain operation requires acknowledgement or a controller is active.
    """
    if acknowledge_uncertain and expected_generation is None:
        raise WakerConfigError("recovery acknowledgement requires --expect-generation")
    runner = _runner(runner, command_timeout)
    with waker_control_lock(identity, home=home):
        config = load_waker_config(identity, home=home)
        _require_generation(config, expected_generation)
        updated = replace(
            config,
            desired_state=DESIRED_ARMED,
            inhibit_reason=None,
            generation=config.generation + 1,
            updated_at=clock(),
        )
        unit = _unit(identity, runner=runner)
        with waker_transition(
            identity,
            updated.generation,
            "resume",
            home=home,
            acknowledge_uncertain=acknowledge_uncertain,
        ) as transition:
            path = save_waker_config(updated, home=home)
            lines = [f"armed {identity} at generation {updated.generation}", f"wrote {path}"]
            for command in (
                ["systemctl", "--user", "daemon-reload"],
                ["systemctl", "--user", "enable", "--now", unit],
            ):
                _completed, failure = _run_command(command, runner=runner)
                if failure:
                    lines.append(failure)
                    return WakerOperationResult(False, tuple(lines), updated.generation)
                lines.append(f"ok: {' '.join(command)}")
            transition.complete()
            return WakerOperationResult(True, tuple(lines), updated.generation)


def _optional_int(value: str | None) -> int | None:
    """Parse a systemd integer property, or return ``None``."""
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def inspect_waker(
    identity: str,
    *,
    home: Path | None = None,
    runner: CommandRunner | None = None,
    command_timeout: float = DEFAULT_COMMAND_TIMEOUT,
    provider_status: Callable[[AgentTmuxConfig], AgentTmuxStatus] = status,
) -> WakerStatus:
    """Return desired, control, systemd and provider state for one exact seat.

    Parameters
    ----------
    identity : str
        Exact configured waker identity.
    home : pathlib.Path or None
        Configuration root.
    runner : CommandRunner or None
        Injected service-query boundary, or the bounded local runner by default.
    command_timeout : float
        Positive finite wait in seconds per service query, not a total deadline
        for provider inspection or the whole operation.
    provider_status : callable
        Existing provider inspection surface.

    Returns
    -------
    WakerStatus
        Separate observations; failed queries do not certify readiness.
    """
    runner = _runner(runner, command_timeout)
    config = load_waker_config(identity, home=home)
    unit = _unit(identity, runner=runner)
    command = [
        "systemctl",
        "--user",
        "show",
        unit,
        "--property=ActiveState",
        "--property=SubState",
        "--property=NRestarts",
        "--property=ExecMainStatus",
        "--no-pager",
    ]
    completed, _failure = _run_command(command, runner=runner)
    properties = (
        dict(line.split("=", 1) for line in completed.stdout.splitlines() if "=" in line)
        if completed is not None
        else {}
    )
    return WakerStatus(
        identity=identity,
        desired_state=config.desired_state,
        generation=config.generation,
        inhibit_reason=config.inhibit_reason,
        unit=unit,
        service_active=properties.get("ActiveState", "unknown"),
        service_substate=properties.get("SubState", "unknown"),
        restart_count=_optional_int(properties.get("NRestarts")),
        main_status=_optional_int(properties.get("ExecMainStatus")),
        provider=provider_status(config.agent_tmux_config()),
        service_query_ok=completed is not None and completed.returncode == 0,
        control_state=transition_state(identity, home=home, generation=config.generation),
    )
