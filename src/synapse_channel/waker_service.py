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
    """Desired, service-manager, and provider state for one exact waker."""

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

    @property
    def ready(self) -> bool:
        """Return whether desired, service, binding, and provider state are healthy."""
        return (
            self.desired_state == DESIRED_ARMED
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
    except OSError as exc:
        return None, f"failed: {' '.join(command)} — {exc}"
    if completed.returncode == 0:
        return completed, ""
    detail = terminal_text((completed.stderr or completed.stdout).strip())
    return completed, f"failed: {' '.join(command)}" + (f" — {detail}" if detail else "")


def _unit(identity: str, *, runner: CommandRunner) -> str:
    """Return the exact escaped systemd unit for ``identity``."""
    return escaped_instance(identity, template=WAKER_TEMPLATE, runner=runner)


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
    runner: CommandRunner = subprocess.run,
    clock: Callable[[], float] = time.time,
    platform: str = sys.platform,
) -> WakerOperationResult:
    """Install or update one active-waker configuration and user unit."""
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
            unit_path = user_systemd_dir(home=home) / WAKER_TEMPLATE
            unit_path.parent.mkdir(parents=True, exist_ok=True)
            unit_path.write_text(render_waker_unit(synapse_bin=executable), encoding="utf-8")
            config_path = save_waker_config(config, home=home)
    except (OSError, ValueError) as exc:
        return WakerOperationResult(False, (f"failed to install waker — {exc}",))
    lines = [f"wrote {unit_path}", f"wrote {config_path}", f"generation: {generation}"]
    unit = _unit(identity, runner=runner)
    if config.desired_state == DESIRED_INHIBITED:
        lines.extend(
            (
                "desired state remains inhibited; service was not started",
                f"run: synapse waker resume {identity}",
            )
        )
        return WakerOperationResult(True, tuple(lines), generation)
    if not start:
        lines.extend(
            (
                "run: systemctl --user daemon-reload",
                f"run: systemctl --user enable --now {unit}",
            )
        )
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
    return WakerOperationResult(True, tuple(lines), generation)


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
    runner: CommandRunner = subprocess.run,
    clock: Callable[[], float] = time.time,
) -> WakerOperationResult:
    """Inhibit one exact waker persistently before stopping its service."""
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
        path = save_waker_config(updated, home=home)
    unit = _unit(identity, runner=runner)
    _completed, failure = _run_command(["systemctl", "--user", "stop", unit], runner=runner)
    lines = [f"inhibited {identity} at generation {updated.generation}", f"wrote {path}"]
    if failure:
        lines.extend((failure, "desired state remains inhibited; a later start cannot run"))
        return WakerOperationResult(False, tuple(lines), updated.generation)
    lines.append(f"ok: systemctl --user stop {unit}")
    return WakerOperationResult(True, tuple(lines), updated.generation)


def resume_waker(
    identity: str,
    *,
    expected_generation: int | None = None,
    home: Path | None = None,
    runner: CommandRunner = subprocess.run,
    clock: Callable[[], float] = time.time,
) -> WakerOperationResult:
    """Clear an inhibit explicitly and start the exact waker service."""
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
        path = save_waker_config(updated, home=home)
    unit = _unit(identity, runner=runner)
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
    runner: CommandRunner = subprocess.run,
    provider_status: Callable[[AgentTmuxConfig], AgentTmuxStatus] = status,
) -> WakerStatus:
    """Return desired, systemd, and real provider state for one exact seat."""
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
    )
