# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — active-waker system service tests

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from _platform_caps import requires_linux
from synapse_channel.agent_tmux import AgentTmuxConfig, AgentTmuxStatus
from synapse_channel.waker_config import (
    DESIRED_ARMED,
    DESIRED_INHIBITED,
    WakerConfigError,
    load_waker_config,
)
from synapse_channel.waker_lock import WakerLockError, waker_control_lock
from synapse_channel.waker_service import (
    WakerStatus,
    inhibit_waker,
    inspect_waker,
    install_waker,
    resume_waker,
)
from synapse_channel.waker_transition import (
    WakerTransitionError,
    transition_state,
    waker_transition,
)

IDENTITY = "repo/codex-1"
pytestmark = requires_linux
UNIT = "synapse-waker@repo-codex-1.service"


class ServiceRunner:
    def __init__(self, *, fail_at: tuple[str, ...] | None = None, show: str = "") -> None:
        self.commands: list[list[str]] = []
        self.fail_at = fail_at
        self.show = show

    def __call__(
        self,
        args: list[str],
        *,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        del capture_output, text, check
        self.commands.append(args)
        if args[0] == "systemd-escape":
            return subprocess.CompletedProcess(args, 0, stdout=f"{UNIT}\n", stderr="")
        if self.fail_at is not None and tuple(args[: len(self.fail_at)]) == self.fail_at:
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="failed\nforged")
        return subprocess.CompletedProcess(args, 0, stdout=self.show, stderr="")


def _install(tmp_path: Path, runner: ServiceRunner, *, start: bool = False) -> None:
    result = install_waker(
        identity=IDENTITY,
        session="repo-codex-1",
        cwd=tmp_path,
        agent_command=("codex", "--model", "gpt-5"),
        synapse_bin="/usr/bin/synapse",
        token_file=tmp_path / "token",
        start=start,
        home=tmp_path,
        runner=runner,
        clock=lambda: 10.0,
    )
    assert result.ok is True


def _provider(config: AgentTmuxConfig, **changes: Any) -> AgentTmuxStatus:
    values: dict[str, Any] = {
        "identity": config.identity,
        "session": config.session,
        "session_exists": True,
        "pane_command": "codex",
        "pane_start_command": "codex",
        "agent_active": True,
        "binding_valid": True,
        "binding_detail": "verified",
        "pending_wake": False,
    }
    values.update(changes)
    return AgentTmuxStatus(**values)


def test_install_writes_exact_unit_and_owner_configuration(tmp_path: Path) -> None:
    runner = ServiceRunner()
    result = install_waker(
        identity=IDENTITY,
        session="repo-codex-1",
        cwd=tmp_path,
        agent_command=("codex",),
        synapse_bin="/usr/bin/synapse",
        start=False,
        home=tmp_path,
        runner=runner,
        clock=lambda: 10.0,
    )

    assert result.ok is True
    assert result.generation == 1
    assert result.lines[-1] == f"run: systemctl --user enable --now {UNIT}"
    unit = (tmp_path / ".config/systemd/user/synapse-waker@.service").read_text()
    assert "ExecStart=/usr/bin/synapse waker run --identity=%I" in unit
    assert "Restart=always" in unit
    assert "RestartPreventExitStatus=78" in unit
    assert "Type=notify" in unit
    assert "WatchdogSec=90" in unit
    assert "PrivateTmp=no" in unit
    assert load_waker_config(IDENTITY, home=tmp_path).desired_state == DESIRED_ARMED
    assert load_waker_config(IDENTITY, home=tmp_path).registry_dir == str(
        (tmp_path / ".local/share/synapse/runtime/agent-tmux").resolve()
    )

    second = install_waker(
        identity=IDENTITY,
        session="repo-codex-1",
        cwd=tmp_path,
        agent_command=("codex",),
        synapse_bin="/usr/bin/synapse",
        home=tmp_path,
        runner=runner,
        clock=lambda: 11.0,
    )
    assert second.generation == 2


def test_install_refuses_unsupported_platform_before_writing(tmp_path: Path) -> None:
    result = install_waker(
        identity=IDENTITY,
        session="repo-codex-1",
        cwd=tmp_path,
        agent_command=("codex",),
        home=tmp_path,
        platform="win32",
    )
    assert result.ok is False
    assert "requires Linux or WSL" in result.lines[0]
    assert not (tmp_path / ".config/systemd/user/synapse-waker@.service").exists()


def test_install_start_reloads_enables_and_restarts_exact_unit(tmp_path: Path) -> None:
    runner = ServiceRunner()
    _install(tmp_path, runner, start=True)
    assert runner.commands[-3:] == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", UNIT],
        ["systemctl", "--user", "restart", UNIT],
    ]


def test_reinstall_preserves_inhibit_and_does_not_restart(tmp_path: Path) -> None:
    runner = ServiceRunner()
    _install(tmp_path, runner)
    inhibit_waker(
        IDENTITY,
        reason="provider loop malfunction",
        home=tmp_path,
        runner=runner,
        clock=lambda: 20.0,
    )
    before = len(runner.commands)

    result = install_waker(
        identity=IDENTITY,
        session="repo-codex-1",
        cwd=tmp_path,
        agent_command=("codex",),
        synapse_bin="/usr/bin/synapse",
        start=True,
        home=tmp_path,
        runner=runner,
        clock=lambda: 30.0,
    )

    config = load_waker_config(IDENTITY, home=tmp_path)
    assert result.ok is True
    assert config.desired_state == DESIRED_INHIBITED
    assert config.inhibit_reason == "provider loop malfunction"
    assert config.generation == 3
    assert runner.commands[before:] == [
        ["systemd-escape", "--template=synapse-waker@.service", "--", IDENTITY]
    ]
    assert result.lines[-2:] == (
        "desired state remains inhibited; service was not started",
        f"run: synapse waker resume {IDENTITY}",
    )
    assert not any("enable --now" in line for line in result.lines)


@pytest.mark.parametrize(
    "failed_prefix",
    [
        ("systemctl", "--user", "daemon-reload"),
        ("systemctl", "--user", "enable"),
        ("systemctl", "--user", "restart"),
    ],
)
def test_install_reports_each_service_manager_failure(
    tmp_path: Path, failed_prefix: tuple[str, ...]
) -> None:
    runner = ServiceRunner(fail_at=failed_prefix)
    result = install_waker(
        identity=IDENTITY,
        session="repo-codex-1",
        cwd=tmp_path,
        agent_command=("codex",),
        synapse_bin="/usr/bin/synapse",
        start=True,
        home=tmp_path,
        runner=runner,
        clock=lambda: 10.0,
    )
    assert result.ok is False
    assert "failed:" in result.lines[-1]
    assert "\n" not in result.lines[-1]
    assert "\\nforged" in result.lines[-1]


def test_install_reports_unit_render_failure(tmp_path: Path) -> None:
    result = install_waker(
        identity=IDENTITY,
        session="repo-codex-1",
        cwd=tmp_path,
        agent_command=("codex",),
        synapse_bin="bad/path",
        home=tmp_path,
    )
    assert result.ok is False
    assert "failed to install waker" in result.lines[0]


def test_service_manager_os_error_is_reported_without_losing_config(tmp_path: Path) -> None:
    def unavailable_runner(
        args: list[str],
        *,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        del capture_output, text, check
        if args[0] == "systemd-escape":
            return subprocess.CompletedProcess(args, 0, stdout=f"{UNIT}\n", stderr="")
        raise OSError("service manager unavailable")

    _install(tmp_path, ServiceRunner())
    result = inhibit_waker(
        IDENTITY,
        reason="malfunction",
        home=tmp_path,
        runner=unavailable_runner,
        clock=lambda: 20.0,
    )
    assert result.ok is False
    assert "service manager unavailable" in result.lines[-2]
    assert load_waker_config(IDENTITY, home=tmp_path).desired_state == DESIRED_INHIBITED


def test_inhibit_persists_before_stopping_and_resume_is_explicit(tmp_path: Path) -> None:
    runner = ServiceRunner()
    _install(tmp_path, runner)

    stopped = inhibit_waker(
        IDENTITY,
        reason="provider loop malfunction",
        expected_generation=1,
        home=tmp_path,
        runner=runner,
        clock=lambda: 20.0,
    )
    inhibited = load_waker_config(IDENTITY, home=tmp_path)
    assert stopped.ok is True
    assert inhibited.desired_state == DESIRED_INHIBITED
    assert inhibited.inhibit_reason == "provider loop malfunction"
    assert inhibited.generation == 2
    assert runner.commands[-1] == ["systemctl", "--user", "stop", UNIT]

    resumed = resume_waker(
        IDENTITY,
        expected_generation=2,
        home=tmp_path,
        runner=runner,
        clock=lambda: 30.0,
    )
    armed = load_waker_config(IDENTITY, home=tmp_path)
    assert resumed.ok is True
    assert armed.desired_state == DESIRED_ARMED
    assert armed.inhibit_reason is None
    assert armed.generation == 3
    assert runner.commands[-2:] == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", UNIT],
    ]


def test_stop_failure_leaves_durable_inhibit(tmp_path: Path) -> None:
    install_runner = ServiceRunner()
    _install(tmp_path, install_runner)
    runner = ServiceRunner(fail_at=("systemctl", "--user", "stop"))
    result = inhibit_waker(
        IDENTITY,
        reason="malfunction",
        home=tmp_path,
        runner=runner,
        clock=lambda: 20.0,
    )
    assert result.ok is False
    assert "desired state remains inhibited" in result.lines[-1]
    assert load_waker_config(IDENTITY, home=tmp_path).desired_state == DESIRED_INHIBITED


@pytest.mark.parametrize(
    "failed_prefix",
    [("systemctl", "--user", "daemon-reload"), ("systemctl", "--user", "enable")],
)
def test_resume_reports_service_manager_failure(
    tmp_path: Path, failed_prefix: tuple[str, ...]
) -> None:
    _install(tmp_path, ServiceRunner())
    inhibit_waker(
        IDENTITY,
        reason="malfunction",
        home=tmp_path,
        runner=ServiceRunner(),
        clock=lambda: 20.0,
    )
    result = resume_waker(
        IDENTITY,
        home=tmp_path,
        runner=ServiceRunner(fail_at=failed_prefix),
        clock=lambda: 30.0,
    )
    assert result.ok is False
    assert load_waker_config(IDENTITY, home=tmp_path).desired_state == DESIRED_ARMED


def test_stale_generation_and_invalid_reason_fail_before_systemctl(tmp_path: Path) -> None:
    runner = ServiceRunner()
    _install(tmp_path, runner)
    before = len(runner.commands)
    with pytest.raises(WakerConfigError, match="generation changed"):
        inhibit_waker(
            IDENTITY,
            reason="malfunction",
            expected_generation=99,
            home=tmp_path,
            runner=runner,
        )
    with pytest.raises(WakerConfigError, match="reason contains control"):
        inhibit_waker(IDENTITY, reason="bad\nreason", home=tmp_path, runner=runner)
    assert len(runner.commands) == before


def test_inspect_combines_systemd_provider_and_pending_state(tmp_path: Path) -> None:
    runner = ServiceRunner(
        show="ActiveState=active\nSubState=running\nNRestarts=4\nExecMainStatus=0\n"
    )
    _install(tmp_path, runner)
    snapshot = inspect_waker(
        IDENTITY,
        home=tmp_path,
        runner=runner,
        provider_status=lambda config: _provider(config, pending_wake=True),
    )
    assert snapshot.ready is True
    assert snapshot.restart_count == 4
    assert snapshot.main_status == 0
    assert snapshot.provider.pending_wake is True
    assert snapshot.service_active == "active"


def test_inspect_degrades_unknown_service_properties_and_bad_integers(tmp_path: Path) -> None:
    _install(tmp_path, ServiceRunner())
    runner = ServiceRunner(
        fail_at=("systemctl", "--user", "show"), show="NRestarts=bad\nExecMainStatus=bad\n"
    )
    snapshot = inspect_waker(
        IDENTITY,
        home=tmp_path,
        runner=runner,
        provider_status=lambda config: _provider(config),
    )
    assert snapshot.ready is False
    assert snapshot.service_query_ok is False
    assert snapshot.restart_count is None
    assert snapshot.main_status is None
    assert snapshot.service_active == "unknown"

    bad_integers = inspect_waker(
        IDENTITY,
        home=tmp_path,
        runner=ServiceRunner(show="ActiveState=active\nNRestarts=bad\nExecMainStatus=bad\n"),
        provider_status=lambda config: _provider(config),
    )
    assert bad_integers.service_query_ok is True
    assert bad_integers.restart_count is None
    assert bad_integers.main_status is None


@pytest.mark.parametrize(
    "change",
    [
        {"desired_state": DESIRED_INHIBITED},
        {"service_query_ok": False},
        {"service_active": "inactive"},
        {"provider": _provider(AgentTmuxConfig(IDENTITY, "s", Path("/")), session_exists=False)},
        {"provider": _provider(AgentTmuxConfig(IDENTITY, "s", Path("/")), binding_valid=False)},
        {"provider": _provider(AgentTmuxConfig(IDENTITY, "s", Path("/")), agent_active=False)},
    ],
)
def test_ready_requires_every_execution_layer(change: dict[str, Any]) -> None:
    provider = _provider(AgentTmuxConfig(IDENTITY, "s", Path("/")))
    baseline = WakerStatus(
        identity=IDENTITY,
        desired_state=DESIRED_ARMED,
        generation=1,
        inhibit_reason=None,
        unit=UNIT,
        service_active="active",
        service_substate="running",
        restart_count=0,
        main_status=0,
        provider=provider,
        service_query_ok=True,
    )
    assert replace(baseline, **change).ready is False


@pytest.mark.parametrize("operation", ["install", "stop", "resume"])
@pytest.mark.parametrize("fail", [False, True])
def test_lifecycle_holds_lock_for_every_command_and_releases_after_result(
    tmp_path: Path, operation: str, fail: bool
) -> None:
    _install(tmp_path, ServiceRunner())
    commands: list[list[str]] = []

    def runner(
        args: list[str],
        *,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        del capture_output, text, check
        commands.append(args)
        with pytest.raises(WakerLockError, match="already changing"):
            with waker_control_lock(IDENTITY, home=tmp_path):
                pytest.fail("service command ran outside lifecycle lock")
        if args[0] == "systemd-escape":
            return subprocess.CompletedProcess(args, 0, stdout=f"{UNIT}\n", stderr="")
        return subprocess.CompletedProcess(
            args, int(fail), stdout="", stderr="failed" if fail else ""
        )

    if operation == "install":
        result = install_waker(
            identity=IDENTITY,
            session="repo-codex-1",
            cwd=tmp_path,
            agent_command=("codex",),
            synapse_bin="/usr/bin/synapse",
            start=True,
            home=tmp_path,
            runner=runner,
        )
    elif operation == "stop":
        result = inhibit_waker(IDENTITY, reason="test", home=tmp_path, runner=runner)
    else:
        result = resume_waker(IDENTITY, home=tmp_path, runner=runner)
    assert result.ok is not fail
    assert commands[0][0] == "systemd-escape"
    assert commands[-1][0] == "systemctl"
    assert load_waker_config(IDENTITY, home=tmp_path).generation == 2
    if fail:
        with pytest.raises(WakerTransitionError, match="recovery required"):
            resume_waker(IDENTITY, expected_generation=2, home=tmp_path, runner=ServiceRunner())
        assert transition_state(IDENTITY, home=tmp_path) == "uncertain"
    retried = resume_waker(
        IDENTITY,
        expected_generation=2,
        home=tmp_path,
        runner=ServiceRunner(),
        acknowledge_uncertain=fail,
    )
    assert retried.ok and retried.generation == 3


@pytest.mark.parametrize("operation", ["install", "stop", "resume", "status"])
def test_service_timeout_is_unknown_and_mutations_require_recovery(
    tmp_path: Path,
    operation: str,
) -> None:
    _install(tmp_path, ServiceRunner())

    def timed_out(
        args: list[str],
        *,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        del capture_output, text, check
        if args[0] == "systemd-escape":
            return subprocess.CompletedProcess(args, 0, stdout=UNIT, stderr="")
        raise subprocess.TimeoutExpired(args, 0.1)

    if operation == "status":
        snapshot = inspect_waker(
            IDENTITY,
            home=tmp_path,
            runner=timed_out,
            provider_status=lambda config: _provider(config),
        )
        assert not snapshot.ready and not snapshot.service_query_ok
        assert snapshot.service_active == "unknown"
        assert transition_state(IDENTITY, home=tmp_path) == "idle"
        return
    if operation == "install":
        result = install_waker(
            identity=IDENTITY,
            session="repo-codex-1",
            cwd=tmp_path,
            agent_command=("codex",),
            synapse_bin="/usr/bin/synapse",
            home=tmp_path,
            runner=timed_out,
            start=True,
        )
    elif operation == "stop":
        result = inhibit_waker(IDENTITY, reason="test", home=tmp_path, runner=timed_out)
    else:
        result = resume_waker(IDENTITY, home=tmp_path, runner=timed_out)
    assert not result.ok and "job outcome is unknown" in " ".join(result.lines)
    assert transition_state(IDENTITY, home=tmp_path) == "uncertain"


def test_unit_lookup_timeout_does_not_mutate_configuration(tmp_path: Path) -> None:
    _install(tmp_path, ServiceRunner())
    before = load_waker_config(IDENTITY, home=tmp_path)

    def timed_out(
        args: list[str],
        *,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        del capture_output, text, check
        raise subprocess.TimeoutExpired(args, 0.1)

    with pytest.raises(WakerConfigError, match="name lookup timed out"):
        resume_waker(IDENTITY, home=tmp_path, runner=timed_out)
    assert load_waker_config(IDENTITY, home=tmp_path) == before
    assert transition_state(IDENTITY, home=tmp_path) == "idle"


def test_recovery_requires_explicit_generation_before_commands(tmp_path: Path) -> None:
    _install(tmp_path, ServiceRunner())
    runner = ServiceRunner()
    with pytest.raises(WakerConfigError, match="requires --expect-generation"):
        resume_waker(IDENTITY, home=tmp_path, runner=runner, acknowledge_uncertain=True)
    assert not runner.commands


def test_interrupted_first_install_can_repair_config_without_clearing_recovery(
    tmp_path: Path,
) -> None:
    with waker_transition(IDENTITY, 1, "install", home=tmp_path):
        pass
    _install(tmp_path, ServiceRunner())
    assert load_waker_config(IDENTITY, home=tmp_path).generation == 1
    assert transition_state(IDENTITY, home=tmp_path) == "uncertain"
    with pytest.raises(WakerTransitionError, match="recovery required"):
        resume_waker(IDENTITY, home=tmp_path, runner=ServiceRunner())
    result = resume_waker(
        IDENTITY,
        home=tmp_path,
        runner=ServiceRunner(),
        expected_generation=1,
        acknowledge_uncertain=True,
    )
    assert result.ok and transition_state(IDENTITY, home=tmp_path) == "idle"
