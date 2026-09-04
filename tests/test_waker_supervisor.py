# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — active-waker runtime and watchdog tests

from __future__ import annotations

import socket
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from _platform_caps import requires_proc
from synapse_channel.agent_tmux import AgentTmuxConfig
from synapse_channel.waker_config import DESIRED_INHIBITED, WakerConfig, save_waker_config
from synapse_channel.waker_supervisor import (
    INHIBITED_EXIT_CODE,
    RELOAD_EXIT_CODE,
    run_waker,
    systemd_notify,
)
from synapse_channel.waker_transition import waker_transition

IDENTITY = "repo/codex-1"


class RecordingSocket:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.connected: str | None = None
        self.sent: bytes | None = None
        self.closed = False

    def connect(self, address: str) -> None:
        self.connected = address
        if self.fail:
            raise OSError("notify socket gone")

    def sendall(self, payload: bytes) -> None:
        self.sent = payload

    def close(self) -> None:
        self.closed = True


def _config(tmp_path: Path, **changes: Any) -> WakerConfig:
    config = WakerConfig(
        identity=IDENTITY,
        session="repo-codex-1",
        cwd=str(tmp_path.resolve()),
        agent_command=("codex",),
        registry_dir=str((tmp_path / "registry").resolve()),
        updated_at=1.0,
    )
    return replace(config, **changes)


def test_notify_is_a_noop_outside_systemd() -> None:
    assert systemd_notify("READY=1", environ={}) is False


@pytest.mark.parametrize(
    ("address", "expected"),
    [("/run/user/1/notify", "/run/user/1/notify"), ("@abstract", "\0abstract")],
)
def test_notify_sends_to_filesystem_and_abstract_sockets(address: str, expected: str) -> None:
    client = RecordingSocket()

    def factory(family: int, kind: int) -> Any:
        assert family == socket.AF_UNIX
        assert kind == socket.SOCK_DGRAM
        return client

    assert systemd_notify("WATCHDOG=1", environ={"NOTIFY_SOCKET": address}, socket_factory=factory)
    assert client.connected == expected
    assert client.sent == b"WATCHDOG=1"
    assert client.closed is True


def test_notify_failure_is_nonfatal_and_closes_socket() -> None:
    client = RecordingSocket(fail=True)
    assert (
        systemd_notify(
            "READY=1",
            environ={"NOTIFY_SOCKET": "/gone"},
            socket_factory=lambda *_args: client,
        )
        is False
    )
    assert client.closed is True


def test_inhibited_configuration_refuses_execution(tmp_path: Path, capsys: Any) -> None:
    save_waker_config(
        _config(tmp_path, desired_state=DESIRED_INHIBITED, inhibit_reason="malfunction"),
        home=tmp_path,
    )
    called = False

    def wake_runner(
        config: AgentTmuxConfig,
        *,
        max_wakes: int | None,
        max_wait_failures: int | None,
        heartbeat: Any,
    ) -> int:
        nonlocal called
        called = True
        return 0

    assert run_waker(IDENTITY, home=tmp_path, wake_runner=wake_runner) == INHIBITED_EXIT_CODE
    assert called is False
    assert "is inhibited" in capsys.readouterr().out


def test_armed_runtime_reports_ready_watchdog_and_stopping(tmp_path: Path) -> None:
    save_waker_config(_config(tmp_path), home=tmp_path)
    notifications: list[str] = []

    def notify(message: str) -> bool:
        notifications.append(message)
        return True

    def wake_runner(
        config: AgentTmuxConfig,
        *,
        max_wakes: int | None,
        max_wait_failures: int | None,
        heartbeat: Any,
    ) -> int:
        assert config.identity == IDENTITY
        assert max_wakes is None
        assert max_wait_failures is None
        heartbeat()
        heartbeat()
        return 7

    code = run_waker(
        IDENTITY,
        home=tmp_path,
        wake_runner=wake_runner,
        notifier=notify,
    )
    assert code == 7
    assert notifications == [
        "READY=1\nSTATUS=armed generation 1",
        "WATCHDOG=1",
        "WATCHDOG=1",
        "STOPPING=1",
    ]


def test_runtime_reports_stopping_when_bridge_raises(tmp_path: Path) -> None:
    save_waker_config(_config(tmp_path), home=tmp_path)
    notifications: list[str] = []

    def notify(message: str) -> bool:
        notifications.append(message)
        return True

    def wake_runner(*_args: Any, **_kwargs: Any) -> int:
        raise RuntimeError("bridge failed")

    with pytest.raises(RuntimeError, match="bridge failed"):
        run_waker(
            IDENTITY,
            home=tmp_path,
            wake_runner=wake_runner,
            notifier=notify,
        )
    assert notifications[-1] == "STOPPING=1"


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"desired_state": DESIRED_INHIBITED, "inhibit_reason": "malfunction"}, 78),
        ({}, RELOAD_EXIT_CODE),
    ],
)
def test_heartbeat_applies_durable_control_changes(
    tmp_path: Path, change: dict[str, Any], expected: int
) -> None:
    initial = _config(tmp_path)
    save_waker_config(initial, home=tmp_path)

    def wake_runner(
        config: AgentTmuxConfig,
        *,
        max_wakes: int | None,
        max_wait_failures: int | None,
        heartbeat: Callable[[], None] | None,
    ) -> int:
        del config, max_wakes, max_wait_failures
        save_waker_config(replace(initial, generation=2, **change), home=tmp_path)
        assert heartbeat is not None
        heartbeat()
        return 0

    assert run_waker(IDENTITY, home=tmp_path, wake_runner=wake_runner) == expected


@requires_proc
def test_uncertain_control_is_checked_before_ready_and_at_heartbeat(tmp_path: Path) -> None:
    save_waker_config(_config(tmp_path), home=tmp_path)
    notifications: list[str] = []

    def notify(message: str) -> bool:
        notifications.append(message)
        return True

    def wake_runner(
        config: AgentTmuxConfig,
        *,
        max_wakes: int | None,
        max_wait_failures: int | None,
        heartbeat: Callable[[], None] | None,
    ) -> int:
        del config, max_wakes, max_wait_failures
        with waker_transition(IDENTITY, 1, "resume", home=tmp_path):
            pass
        assert heartbeat is not None
        heartbeat()
        pytest.fail("uncertain control did not stop the wake loop")

    assert run_waker(IDENTITY, home=tmp_path, wake_runner=wake_runner, notifier=notify) == 78
    assert notifications == ["READY=1\nSTATUS=armed generation 1", "STOPPING=1"]
    notifications.clear()
    assert run_waker(IDENTITY, home=tmp_path, wake_runner=wake_runner, notifier=notify) == 78
    assert notifications == []


@requires_proc
def test_live_pending_controller_allows_startup_before_success_receipt(tmp_path: Path) -> None:
    save_waker_config(_config(tmp_path), home=tmp_path)
    with waker_transition(IDENTITY, 1, "resume", home=tmp_path) as transition:
        code = run_waker(
            IDENTITY,
            home=tmp_path,
            wake_runner=lambda *_args, **_kwargs: 7,
            notifier=lambda _message: True,
        )
        assert code == 7
        transition.complete()
