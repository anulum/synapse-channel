# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — active-waker runtime and systemd watchdog
"""Run an active terminal waker under a truthful systemd watchdog contract."""

from __future__ import annotations

import os
import socket
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol

from synapse_channel.agent_tmux import AgentTmuxConfig, wait_and_wake
from synapse_channel.waker_config import DESIRED_INHIBITED, load_waker_config

INHIBITED_EXIT_CODE = 78
"""Exit code reserved for an explicit persistent operator or agent inhibit."""

RELOAD_EXIT_CODE = 75
"""Temporary exit that asks systemd to restart with a newer armed generation."""


class _ControlChange(RuntimeError):
    """Internal main-loop signal for a durable desired-state change."""

    def __init__(self, exit_code: int) -> None:
        self.exit_code = exit_code


class WakeRunner(Protocol):
    """Callable compatible with the active tmux wake loop."""

    def __call__(
        self,
        config: AgentTmuxConfig,
        *,
        max_wakes: int | None,
        max_wait_failures: int | None,
        heartbeat: Callable[[], None] | None,
    ) -> int:
        """Run the active wake loop for ``config``."""


class _NotifySocket(Protocol):
    """Socket methods required by the systemd notification path."""

    def connect(self, address: str) -> None:
        """Connect to the systemd notification endpoint."""

    def sendall(self, data: bytes) -> None:
        """Send one complete notification datagram."""

    def close(self) -> None:
        """Close the notification socket."""


def systemd_notify(
    message: str,
    *,
    environ: Mapping[str, str] | None = None,
    socket_factory: Callable[..., _NotifySocket] = socket.socket,
) -> bool:
    """Send one readiness/watchdog datagram to the current systemd manager.

    Returns ``False`` outside a notify service or when its socket is gone.
    Abstract-namespace addresses use systemd's leading ``@`` notation and are
    translated to the kernel's leading NUL form.
    """
    env = os.environ if environ is None else environ
    address = env.get("NOTIFY_SOCKET", "")
    if not address:
        return False
    target = f"\0{address[1:]}" if address.startswith("@") else address
    client = socket_factory(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        client.connect(target)
        client.sendall(message.encode("utf-8"))
    except OSError:
        return False
    finally:
        client.close()
    return True


def run_waker(
    identity: str,
    *,
    home: Path | None = None,
    wake_runner: WakeRunner = wait_and_wake,
    notifier: Callable[[str], bool] = systemd_notify,
) -> int:
    """Run the configured active bridge until stopped or fatally refused."""
    config = load_waker_config(identity, home=home)
    if config.desired_state == DESIRED_INHIBITED:
        print(
            f"waker {identity} is inhibited at generation {config.generation}: "
            f"{config.inhibit_reason or 'no reason recorded'}"
        )
        return INHIBITED_EXIT_CODE
    notifier(f"READY=1\nSTATUS=armed generation {config.generation}")

    def heartbeat() -> None:
        current = load_waker_config(identity, home=home)
        if current.desired_state == DESIRED_INHIBITED:
            raise _ControlChange(INHIBITED_EXIT_CODE)
        if current.generation != config.generation:
            raise _ControlChange(RELOAD_EXIT_CODE)
        notifier("WATCHDOG=1")

    try:
        try:
            return wake_runner(
                config.agent_tmux_config(),
                max_wakes=None,
                max_wait_failures=None,
                heartbeat=heartbeat,
            )
        except _ControlChange as change:
            return change.exit_code
    finally:
        notifier("STOPPING=1")
