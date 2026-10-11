# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — systemd user-unit templates with finite start budgets
"""Render the systemd user units and the start budget each one carries.

Every generated unit restarts on failure with a growing delay and stops trying
after a fixed number of starts inside a fixed window. A unit that cannot start
then rests in the ``failed`` state, where ``systemctl --user --failed`` and
``synapse doctor`` show it, instead of restarting without end.

Two budgets exist. :data:`HUB_START_BUDGET` is short, because a hub that cannot
start is a fault to see at once. :data:`LISTENER_START_BUDGET` is long, because
a presence holder exits whenever the hub is unreachable and must outlast an
ordinary hub restart or upgrade.

The growing delay uses ``RestartSteps=`` and ``RestartMaxDelaySec=``, which
systemd understands from version 254. An older systemd ignores both lines and
restarts at the fixed ``RestartSec=``; :meth:`StartBudget.seconds_to_failed`
reports both cases.

Each unit text also states which processes the unit owns. Stopping a unit
kills its control group, so a process that must outlive the unit, such as a
tmux server shared by several seats, must never be started inside it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.service_hardening import (
    HUB_NOFILE,
    HUB_WRITE_PATHS,
    LISTENER_NOFILE,
    LISTENER_WRITE_PATHS,
    hardening_directives,
)
from synapse_channel.terminal_text import terminal_text

_SYSTEMD_EXEC_PREFIXES = frozenset("@-:+!|")
"""Leading ``ExecStart=`` characters that alter systemd execution semantics."""

_SYSTEMD_EXEC_GLOB_CHARS = frozenset("*?[")
"""Bytes rejected by upstream ``string_is_safe(path, 0)``."""

_SYSTEMD_INVALID_SIMPLE_EXECUTABLES = frozenset({".", "..", ";"})
"""Slash-free tokens that are invalid names or alter ``ExecStart=`` parsing."""

_SYSTEMD_SIMPLE_NAME_MAX_BYTES = 255
"""Linux ``NAME_MAX`` used by systemd's simple executable-name predicate."""

_SYSTEMD_PATH_MAX_BYTES = 4096
"""Linux ``PATH_MAX`` including the trailing NUL expected by systemd."""


@dataclass(frozen=True, slots=True)
class StartBudget:
    """How often a unit may start, and how long it waits between starts.

    Parameters
    ----------
    restart_sec : int
        Delay in seconds before the first restart (``RestartSec=``).
    max_delay_sec : int
        Largest delay in seconds between restarts (``RestartMaxDelaySec=``).
    steps : int
        Number of restarts over which the delay grows from ``restart_sec`` to
        ``max_delay_sec`` (``RestartSteps=``).
    burst : int
        Starts allowed inside one window (``StartLimitBurst=``). The start
        after the last allowed one is refused and the unit becomes failed.
    interval_sec : int
        Length of the window in seconds (``StartLimitIntervalSec=``).

    Raises
    ------
    ValueError
        A value is out of range, or the budget could never be used up: the
        allowed starts, spaced by the growing delays, would not fit inside
        one window, so the unit would restart without end.
    """

    restart_sec: int
    max_delay_sec: int
    steps: int
    burst: int
    interval_sec: int

    def __post_init__(self) -> None:
        """Refuse a budget that systemd would never enforce."""
        if self.restart_sec < 1 or self.max_delay_sec < self.restart_sec:
            raise ValueError("restart delay must be at least 1 s and not above its maximum")
        if self.steps < 1 or self.burst < 2 or self.interval_sec < 1:
            raise ValueError("steps must be at least 1, burst at least 2, interval at least 1 s")
        if self.seconds_to_failed() >= self.interval_sec:
            raise ValueError(
                f"{self.burst} starts take {self.seconds_to_failed():.0f} s, which does not fit "
                f"inside the {self.interval_sec} s window; the unit would never become failed"
            )

    def restart_delays(self, *, backoff: bool = True) -> tuple[float, ...]:
        """Return the delay before each restart until the budget is used up.

        The unit starts once without delay and then restarts ``burst`` times;
        the last of these restarts is the one systemd refuses. With
        ``backoff`` the delays follow systemd's own rule: restart ``n`` waits
        ``restart_sec * (max_delay_sec / restart_sec) ** ((n - 1) / steps)``
        and, from restart ``steps + 1`` on, ``max_delay_sec``. Without it every delay is
        ``restart_sec``, as on a systemd older than 254.
        """
        if not backoff or self.max_delay_sec == self.restart_sec:
            return (float(self.restart_sec),) * self.burst
        ratio = self.max_delay_sec / self.restart_sec
        return tuple(
            self.restart_sec * math.pow(ratio, min(1.0, (number - 1) / self.steps))
            for number in range(1, self.burst + 1)
        )

    def seconds_to_failed(self, *, backoff: bool = True) -> float:
        """Return how long a unit that fails at once keeps trying, in seconds."""
        return sum(self.restart_delays(backoff=backoff))

    def unit_directives(self) -> str:
        """Return the ``[Unit]`` lines of the budget, newline-terminated."""
        return f"StartLimitIntervalSec={self.interval_sec}\nStartLimitBurst={self.burst}\n"

    def service_directives(self) -> str:
        """Return the ``[Service]`` restart lines of the budget, newline-terminated."""
        return (
            "Restart=always\n"
            f"RestartSec={self.restart_sec}\n"
            f"RestartSteps={self.steps}\n"
            f"RestartMaxDelaySec={self.max_delay_sec}\n"
        )


HUB_START_BUDGET = StartBudget(restart_sec=2, max_delay_sec=30, steps=4, burst=10, interval_sec=600)
"""The hub: a hub that cannot start is failed within a few minutes."""

LISTENER_START_BUDGET = StartBudget(
    restart_sec=5, max_delay_sec=60, steps=5, burst=30, interval_sec=1800
)
"""Presence, wake listener and waker: they outlast a hub outage of about 26 minutes."""

HUB_OWNS = "the hub process"
"""Processes the hub unit owns and kills when it stops."""

PRESENCE_OWNS = "one presence listener process for %I"
"""Processes a presence instance owns and kills when it stops."""

ARM_OWNS = "one wake listener process for %I"
"""Processes a wake-listener instance owns and kills when it stops."""

WAKER_OWNS = (
    "the waker process for %I and the commands it runs; "
    "never the tmux server or the provider session it wakes"
)
"""Processes a waker instance owns and kills when it stops."""


def _header(title: str, owns: str) -> str:
    """Return the licence header and the ownership statement of a unit file."""
    return (
        "# SPDX-License-"
        "Identifier: AGPL-3.0-or-later\n"
        "# Commercial license available\n"
        "# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.\n"
        "# © Code 2020–2026 Miroslav Šotek. All rights reserved.\n"
        "# ORCID: 0009-0009-3560-0851\n"
        "# Contact: www.anulum.li | protoscience@anulum.li\n"
        f"# SYNAPSE CHANNEL — {title}\n"
        f"# Owns: {owns}\n\n"
    )


def _unit_token(value: str, *, label: str) -> str:
    """Return a safe single-token systemd value or reject ambiguous input."""
    if (
        not value
        or terminal_text(value) != value
        or any(character.isspace() or character in {'"', "'", "\\", "$"} for character in value)
    ):
        raise ValueError(
            f"{label} must be one non-empty systemd token without whitespace, controls, "
            "quotes, backslashes, or '$' expansion"
        )
    return value.replace("%", "%%")


def _systemd_executable_string_is_safe(value: str) -> bool:
    """Return whether ``value`` matches systemd's UTF-8 and glob safety rules."""
    for character in value:
        codepoint = ord(character)
        if (
            character in _SYSTEMD_EXEC_GLOB_CHARS
            or 0xD800 <= codepoint <= 0xDFFF
            or 0xFDD0 <= codepoint <= 0xFDEF
            or (codepoint & 0xFFFE) == 0xFFFE
        ):
            return False
    return True


def validate_systemd_executable(value: str) -> str:
    """Return a literal executable token with systemd control prefixes refused.

    Public service-install entrypoints call this pure validator before any
    repository, hook, scaffold, or service-file mutation. Unit renderers call it
    again so direct library use retains the same fail-closed boundary.
    """
    if not _systemd_executable_string_is_safe(value):
        raise ValueError(
            "synapse executable path must be systemd-safe UTF-8 without glob metacharacters (*?[)"
        )
    executable = _unit_token(value, label="synapse executable path")
    if executable[0] in _SYSTEMD_EXEC_PREFIXES:
        prefixes = "".join(sorted(_SYSTEMD_EXEC_PREFIXES))
        raise ValueError(
            "synapse executable path must not start with a systemd ExecStart control "
            f"prefix ({prefixes})"
        )
    # systemd units always use POSIX absolute paths. On Windows,
    # Path("/bin/synapse").is_absolute() is False, so also treat leading "/" as
    # absolute (validators run on every OS; units only deploy on Linux).
    is_absolute = Path(value).is_absolute() or value.startswith("/")
    if is_absolute:
        encoded = value.encode("utf-8")
        components = value.replace("\\", "/").split("/")
        oversized_component = any(
            len(component.encode("utf-8")) > _SYSTEMD_SIMPLE_NAME_MAX_BYTES
            for component in components
            if component not in ("",)
        )
        if (
            len(encoded) >= _SYSTEMD_PATH_MAX_BYTES
            or oversized_component
            or value.endswith(("/", "/.", "/..", "\\", "\\.", "\\.."))
        ):
            raise ValueError(
                "synapse executable absolute path must not imply a directory, must keep "
                "each component within 255 UTF-8 bytes, and must remain below PATH_MAX"
            )
    elif (
        "/" in value
        or "\\" in value
        or value in _SYSTEMD_INVALID_SIMPLE_EXECUTABLES
        or len(value.encode("utf-8")) > _SYSTEMD_SIMPLE_NAME_MAX_BYTES
    ):
        raise ValueError(
            "synapse executable path must be absolute or a valid simple file name without "
            "slashes or systemd command metatokens"
        )
    return executable


def render_hub_unit(*, synapse_bin: str) -> str:
    """Render the local-first hub user service."""
    executable = validate_systemd_executable(synapse_bin)
    # The hub must not be ordered After=default.target: WantedBy=default.target
    # gives the target an implicit After= on this unit, and the resulting boot
    # ordering cycle makes systemd delete the presence/arm start jobs.
    return (
        _header("generated user service for the coordination hub", HUB_OWNS) + "[Unit]\n"
        "Description=SYNAPSE CHANNEL coordination hub (local-first, loopback)\n"
        "Documentation=https://github.com/anulum/synapse-channel\n"
        + HUB_START_BUDGET.unit_directives()
        + "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={executable} hub --port=8876 --db=%h/synapse/hub.db "
        "--relay-log=%h/synapse/feed.ndjson --relay-max-lines=20000\n"
        + HUB_START_BUDGET.service_directives()
        + hardening_directives(write_paths=HUB_WRITE_PATHS, nofile=HUB_NOFILE)
        + "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def render_presence_unit(*, synapse_bin: str) -> str:
    """Render the project presence-holder template unit."""
    executable = validate_systemd_executable(synapse_bin)
    return (
        _header("generated user service for provider-neutral presence", PRESENCE_OWNS) + "[Unit]\n"
        "Description=SYNAPSE CHANNEL presence holder for %I\n"
        "Documentation=https://github.com/anulum/synapse-channel\n"
        "After=synapse-hub.service\n"
        "Wants=synapse-hub.service\n" + LISTENER_START_BUDGET.unit_directives() + "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={executable} listen --name=%I-presence --for=%I "
        "--uri=ws://localhost:8876\n"
        + LISTENER_START_BUDGET.service_directives()
        + hardening_directives(write_paths=LISTENER_WRITE_PATHS, nofile=LISTENER_NOFILE)
        + "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def render_arm_unit(
    *,
    synapse_bin: str,
    uri: str = "ws://localhost:8876",
    token_file: str | None = None,
) -> str:
    """Render the persistent non-LLM wake listener template unit."""
    executable = validate_systemd_executable(synapse_bin)
    hub_uri = _unit_token(uri, label="hub URI")
    extra_argument = ""
    if token_file is not None:
        token_path = _unit_token(token_file, label="token file path")
        extra_argument = f" --token-file={token_path}"
    return (
        _header("generated user service for a provider-neutral wake listener", ARM_OWNS)
        + "[Unit]\n"
        "Description=SYNAPSE CHANNEL wake listener for %I\n"
        "Documentation=https://github.com/anulum/synapse-channel\n"
        "After=synapse-hub.service\n" + LISTENER_START_BUDGET.unit_directives() + "\n"
        "[Service]\n"
        "Type=simple\n"
        "Environment=SYN_PROJECT=%I\n"
        "Environment=SYN_IDENTITY=%I\n"
        "WorkingDirectory=%h/.local/share\n"
        "Environment=XDG_DATA_HOME=.\n"
        f"ExecStart={executable} arm --name=%I-rx --for=%I --directed-only "
        f"--mailbox --uri={hub_uri}{extra_argument}\n"
        "SuccessExitStatus=78\n"
        + LISTENER_START_BUDGET.service_directives()
        + "RestartPreventExitStatus=78\n"
        + hardening_directives(write_paths=LISTENER_WRITE_PATHS, nofile=LISTENER_NOFILE)
        + "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def render_waker_unit(*, synapse_bin: str) -> str:
    """Render the active terminal-agent waker template unit.

    Unlike passive listeners, this unit must share the host ``/tmp`` namespace
    with the already-running tmux server. Its writable state remains confined
    to Synapse's owner-only data directory. ``KillMode=control-group`` is safe
    only because the unit never starts the tmux server: the server and the
    provider session live outside this unit's control group.
    """
    executable = validate_systemd_executable(synapse_bin)
    return (
        _header("generated active terminal-agent waker service", WAKER_OWNS) + "[Unit]\n"
        "Description=SYNAPSE CHANNEL active terminal-agent waker for %I\n"
        "Documentation=https://github.com/anulum/synapse-channel\n"
        "After=network-online.target synapse-hub.service\n"
        "Wants=network-online.target\n" + LISTENER_START_BUDGET.unit_directives() + "\n"
        "[Service]\n"
        "Type=notify\n"
        "NotifyAccess=main\n"
        "Environment=XDG_DATA_HOME=%h/.local/share\n"
        "Environment=XDG_RUNTIME_DIR=%h/.local/share/synapse/runtime\n"
        f"ExecStart={executable} waker run --identity=%I\n"
        + LISTENER_START_BUDGET.service_directives()
        + "RestartPreventExitStatus=78\n"
        "WatchdogSec=90\n"
        "TimeoutStopSec=15\n"
        "KillMode=control-group\n"
        + hardening_directives(
            write_paths=LISTENER_WRITE_PATHS,
            nofile=LISTENER_NOFILE,
            private_tmp=False,
        )
        + "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
