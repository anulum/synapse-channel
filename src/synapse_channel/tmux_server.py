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

* :func:`start_own_server` starts a new server, before the command that
  creates a session, in a transient service of its own. The user service
  manager starts that service, so the server does not inherit the caller's
  sandbox: a server first created from a unit with ``NoNewPrivileges``, a user
  namespace or a seccomp filter would otherwise pass them to every later pane
  (``sudo`` and helpers that need privileges then fail in every seat). The
  caller's ``PATH``, locale and ``TMUX_TMPDIR`` are passed on by name, and the
  caller's own command keeps its exit code, output and environment;
* :func:`move_to_own_scope` asks the user service manager to put a server that
  already runs into a new transient scope, without restarting it.

Both need a systemd user manager. Without one the functions say so and change
nothing; tmux then behaves as it always did.

Only the server process is placed. A tmux built with systemd support, 3.4 and
later, already runs every pane in a scope of its own. With an older tmux the
panes stay children of the server in the original control group.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from synapse_channel.terminal_text import terminal_text

SCOPE_DESCRIPTION = (
    "tmux server in a scope of its own, so that stopping a Synapse unit cannot kill every pane"
)
"""Description given to every scope this module creates."""

SERVER_DESCRIPTION = (
    "tmux server in a service of its own, started by the user manager outside any caller's sandbox"
)
"""Description given to the service a new server is started in."""

NEW_SERVER_UNIT_PREFIX = "synapse-tmux-server-"
"""Name prefix of the transient service a new server is started in."""

INHERITED_ENVIRONMENT = ("PATH", "LANG", "LC_ALL", "TMUX_TMPDIR")
"""Caller variables the new server keeps, when set: where commands and the socket are found."""

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
        """Return whether stopping some other service unit would kill this server.

        The service :func:`start_own_server` starts holds only the server, so a
        server there is in its own unit and does not count.
        """
        if self.unit is None or not self.unit.endswith(".service"):
            return False
        return not self.unit.startswith(NEW_SERVER_UNIT_PREFIX)


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


@dataclass(frozen=True, slots=True)
class ServerStart:
    """Outcome of starting a tmux server in a service of its own."""

    started: bool
    unit: str | None
    detail: str


def own_server_command(
    tmux_bin: str, *, unit: str, environ: Mapping[str, str] | None = None
) -> list[str]:
    """Return the command that starts an empty tmux server in a transient service.

    The service is ``forking``: the user manager runs the tmux client in a clean
    execution context, the client forks the server and exits, and the server
    stays as the service's main process. ``exit-empty off`` keeps the server
    while it has no session yet. :data:`INHERITED_ENVIRONMENT` names the caller
    variables passed on; ``--setenv=NAME`` copies the value, so it is not
    written into the command line.

    Parameters
    ----------
    tmux_bin : str
        The tmux executable.
    unit : str
        Name of the transient service.
    environ : Mapping[str, str] or None
        The caller's environment; defaults to :data:`os.environ`.

    Returns
    -------
    list of str
        The ``systemd-run --user …`` command.
    """
    env = os.environ if environ is None else environ
    return [
        "systemd-run",
        "--user",
        "--quiet",
        "--collect",
        "--service-type=forking",
        f"--unit={unit}",
        f"--description={SERVER_DESCRIPTION}",
        *(f"--setenv={key}" for key in INHERITED_ENVIRONMENT if key in env),
        "--",
        tmux_bin,
        "start-server",
        ";",
        "set-option",
        "-g",
        "exit-empty",
        "off",
    ]


def start_own_server(
    tmux_bin: str,
    *,
    runner: CommandRunner,
    token: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> ServerStart:
    """Start the tmux server in a service of its own when none answers yet.

    The command that then creates a session runs as the caller's own child, so
    its exit code, output and environment are what they always were; only the
    server it talks to was started by the user manager, outside any caller's
    sandbox. ``systemd-run`` waits for the start, so the server answers when
    this returns.

    Parameters
    ----------
    tmux_bin : str
        The tmux executable the caller will use.
    runner : CommandRunner
        Command boundary for the probes and the start.
    token : str or None
        Suffix of the service name; a random one by default.
    environ : Mapping[str, str] or None
        The caller's environment; defaults to :data:`os.environ`.

    Returns
    -------
    ServerStart
        ``started`` only when the service was started. Nothing is started
        when a server already answers or no user manager does; a refused
        start is reported, and the caller's own command then starts the
        server as tmux always did.
    """
    if server_pid(tmux_bin, runner=runner) is not None:
        return ServerStart(False, None, "a tmux server already answers")
    if not user_manager_available(runner=runner):
        return ServerStart(False, None, "no systemd user manager answers")
    unit = f"{NEW_SERVER_UNIT_PREFIX}{token or uuid.uuid4().hex[:12]}.service"
    completed = _run(own_server_command(tmux_bin, unit=unit, environ=environ), runner=runner)
    if completed is None or completed.returncode != 0:
        detail = "" if completed is None else (completed.stderr or completed.stdout).strip()
        return ServerStart(
            False,
            None,
            f"could not start {unit}" + (f" — {terminal_text(detail)}" if detail else ""),
        )
    return ServerStart(True, unit, f"tmux server started in {unit}")


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
