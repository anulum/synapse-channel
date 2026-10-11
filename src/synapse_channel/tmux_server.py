# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — keep the tmux server out of every other unit's control group
"""Keep the tmux server in a control group of its own.

The first ``tmux new-session`` on a host forks the tmux server, and the server
stays in the control group of whoever ran that command. When that is a systemd
service unit, stopping the unit kills the server and with it every session of
every seat on the host.

This module answers where the server lives and offers two remedies:

* :func:`new_server_prefix` returns a ``systemd-run --user --scope`` prefix
  for the command that would create the server, so that a new server starts
  in a transient scope of its own;
* :func:`move_to_own_scope` asks the user service manager to put a server that
  already runs into a new transient scope, without restarting it.

Both need a systemd user manager. Without one the functions say so and change
nothing; tmux then behaves as it always did.

Only the server process is placed. A tmux built with systemd support, 3.4 and
later, already runs every pane in a scope of its own. With an older tmux the
panes stay children of the server in the original control group.
"""

from __future__ import annotations

import subprocess  # nosec B404
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from synapse_channel.terminal_text import terminal_text

SCOPE_DESCRIPTION = (
    "tmux server in a scope of its own, so that stopping a Synapse unit cannot kill every pane"
)
"""Description given to every scope this module creates."""

NEW_SCOPE_PREFIX = "synapse-tmux-server-"
"""Name prefix of the scope a new server is started in."""

MOVED_SCOPE_PREFIX = "tmux-server-"
"""Name prefix of the scope a running server is moved into; the process id follows."""


class CommandRunner(Protocol):
    """Callable compatible with :func:`subprocess.run` for the calls made here."""

    def __call__(
        self,
        args: Sequence[str],
        *,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        """Run ``args`` and return the completed process."""


@dataclass(frozen=True, slots=True)
class TmuxServerPlacement:
    """Where the tmux server reached through one tmux executable lives."""

    pid: int | None
    unit: str | None

    @property
    def running(self) -> bool:
        """Return whether a server answered."""
        return self.pid is not None

    @property
    def in_service_unit(self) -> bool:
        """Return whether stopping a service unit would kill this server."""
        return self.unit is not None and self.unit.endswith(".service")


@dataclass(frozen=True, slots=True)
class MoveResult:
    """Outcome of moving a running server into a scope of its own."""

    moved: bool
    unit: str | None
    detail: str


def _run(
    command: Sequence[str], *, runner: CommandRunner
) -> subprocess.CompletedProcess[str] | None:
    """Run one command; return None when it could not be run or timed out."""
    try:
        return runner(list(command), capture_output=True, text=True, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None


def server_pid(tmux_bin: str, *, runner: CommandRunner) -> int | None:
    """Return the process id of the server ``tmux_bin`` reaches, or None without one."""
    completed = _run([tmux_bin, "display-message", "-p", "#{pid}"], runner=runner)
    if completed is None or completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return int(value) if value.isdigit() else None


def unit_of_pid(pid: int, *, proc_root: Path = Path("/proc")) -> str | None:
    """Return the systemd unit whose control group holds process ``pid``.

    The unified hierarchy line of ``/proc/<pid>/cgroup`` ends in the unit name.
    None is returned when the process is gone, the file is unreadable, or the
    control group is not a service or a scope.
    """
    try:
        text = (proc_root / str(pid) / "cgroup").read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("0::"):
            leaf = line[3:].rstrip("/").rsplit("/", 1)[-1]
            return leaf if leaf.endswith((".service", ".scope")) else None
    return None


def placement(
    tmux_bin: str, *, runner: CommandRunner, proc_root: Path = Path("/proc")
) -> TmuxServerPlacement:
    """Return where the server reached through ``tmux_bin`` lives."""
    pid = server_pid(tmux_bin, runner=runner)
    if pid is None:
        return TmuxServerPlacement(None, None)
    return TmuxServerPlacement(pid, unit_of_pid(pid, proc_root=proc_root))


def user_manager_available(*, runner: CommandRunner) -> bool:
    """Return whether a systemd user manager answers for this user."""
    completed = _run(
        ["systemctl", "--user", "show", "--property=Version", "--value"], runner=runner
    )
    return completed is not None and completed.returncode == 0 and bool(completed.stdout.strip())


def new_server_prefix(
    tmux_bin: str, *, runner: CommandRunner, token: str | None = None
) -> list[str]:
    """Return the prefix for a tmux command that would create the server.

    Parameters
    ----------
    tmux_bin : str
        The tmux executable the command will use.
    runner : CommandRunner
        Command boundary for the two read-only probes.
    token : str or None
        Suffix of the scope name; a random one by default.

    Returns
    -------
    list of str
        ``systemd-run --user --scope …`` arguments ending in ``--`` when no
        server answers yet and a user manager is available. An empty list
        when a server already runs, because the command then only talks to
        it, or when no user manager answers.
    """
    if server_pid(tmux_bin, runner=runner) is not None:
        return []
    if not user_manager_available(runner=runner):
        return []
    name = f"{NEW_SCOPE_PREFIX}{token or uuid.uuid4().hex[:12]}.scope"
    return [
        "systemd-run",
        "--user",
        "--scope",
        "--quiet",
        "--collect",
        f"--unit={name}",
        f"--description={SCOPE_DESCRIPTION}",
        "--",
    ]


def move_to_own_scope(
    tmux_bin: str,
    *,
    runner: CommandRunner,
    proc_root: Path = Path("/proc"),
    settle_timeout: float = 5.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> MoveResult:
    """Move a running tmux server out of a service unit into a scope of its own.

    The server is not restarted and no session is touched. Nothing is done
    when no server answers, or when the server is not in a service unit.

    Returns
    -------
    MoveResult
        ``moved`` is true only when the server's control group was read back
        and is the new scope.
    """
    found = placement(tmux_bin, runner=runner, proc_root=proc_root)
    if found.pid is None:
        return MoveResult(False, None, "no tmux server answers")
    if not found.in_service_unit:
        where = found.unit or "no service or scope unit"
        return MoveResult(False, found.unit, f"tmux server pid {found.pid} is in {where}")
    scope = f"{MOVED_SCOPE_PREFIX}{found.pid}.scope"
    command = [
        "busctl",
        "--user",
        "call",
        "org.freedesktop.systemd1",
        "/org/freedesktop/systemd1",
        "org.freedesktop.systemd1.Manager",
        "StartTransientUnit",
        "ssa(sv)a(sa(sv))",
        scope,
        "fail",
        "2",
        "PIDs",
        "au",
        "1",
        str(found.pid),
        "Description",
        "s",
        SCOPE_DESCRIPTION,
        "0",
    ]
    completed = _run(command, runner=runner)
    if completed is None or completed.returncode != 0:
        detail = "" if completed is None else (completed.stderr or completed.stdout).strip()
        return MoveResult(
            False,
            found.unit,
            f"could not create {scope}" + (f" — {terminal_text(detail)}" if detail else ""),
        )
    deadline = clock() + settle_timeout
    now = unit_of_pid(found.pid, proc_root=proc_root)
    while now != scope and clock() < deadline:
        sleep(0.1)
        now = unit_of_pid(found.pid, proc_root=proc_root)
    if now != scope:
        return MoveResult(
            False, now, f"tmux server pid {found.pid} is still in {now or 'an unknown unit'}"
        )
    return MoveResult(
        True, scope, f"tmux server pid {found.pid} moved from {found.unit} to {scope}"
    )
