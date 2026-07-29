# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — release redeploy operations checklist helpers
"""Build operator checklists for post-release local fleet redeploys.

The checklist builder is side-effect free. Its default output withholds every
restart command and exposes only read-only live-session checks. An operator can
request a disruptive command only by supplying the exact currently authorised
hub PID; the rendered command rechecks that PID while holding a fail-fast
host-local file lock before it restarts the local services.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from synapse_channel.client.agent import DEFAULT_HUB_URI
from synapse_channel.service_setup import CommandRunner, default_synapse_bin, escaped_instance
from synapse_channel.terminal_text import shell_command_arg, shell_long_option, terminal_text

EVENT_SUMMARY_SQL = "select kind, count(*) from events group by kind order by kind;"
"""SQLite query used by the durable replay checklist step."""


@dataclass(frozen=True)
class RedeployCheck:
    """One post-release redeploy check an operator can copy and run.

    Attributes
    ----------
    label : str
        Short name for the operational check.
    command : str
        Shell command that performs or verifies the step.
    expected : str
        Observable outcome that should be true after the command.
    """

    label: str
    command: str
    expected: str


def build_redeploy_checklist(
    *,
    project: str,
    identity: str,
    hub_uri: str = DEFAULT_HUB_URI,
    db_path: Path | str = "~/synapse/hub.db",
    synapse_bin: str | None = None,
    escape_runner: CommandRunner | None = None,
    authorized_hub_pid: int | None = None,
) -> list[RedeployCheck]:
    """Return the release redeploy checklist for one project and identity.

    Parameters
    ----------
    project : str
        Project namespace whose presence and roster should recover.
    identity : str
        Worker identity whose wake listener should recover.
    hub_uri : str, optional
        Hub URI to verify after restart.
    db_path : pathlib.Path or str, optional
        SQLite event-store path used by the hub service.
    synapse_bin : str or None, optional
        Installed ``synapse`` executable path to verify. Defaults to the
        executable resolved from ``PATH``.
    escape_runner : CommandRunner, optional
        Injectable command runner used only for ``systemd-escape``.
    authorized_hub_pid : int or None, optional
        Exact live hub PID whose disruption has fresh operator authority.
        ``None`` withholds every restart command. The generated restart command
        rechecks a positive supplied PID while holding a unique host-local lock.

    Returns
    -------
    list[RedeployCheck]
        Ordered package, live-session, optional restart, replay, and hook checks.
    """
    synapse = synapse_bin or default_synapse_bin()
    if escape_runner is None:
        presence_unit = escaped_instance(project, template="synapse-presence@.service")
        arm_unit = escaped_instance(identity, template="synapse-arm@.service")
    else:
        presence_unit = escaped_instance(
            project, template="synapse-presence@.service", runner=escape_runner
        )
        arm_unit = escaped_instance(identity, template="synapse-arm@.service", runner=escape_runner)
    db = _shell_word(str(db_path))
    project_option = shell_long_option("--project", project)
    uri_option = shell_long_option("--uri", hub_uri)
    synapse_word = _shell_word(synapse)
    checks = [
        RedeployCheck(
            label="Package and executable",
            command=f"command -v -- {synapse_word} && {synapse_word} --version",
            expected="installed command reports the release version",
        ),
        RedeployCheck(
            label="Live-session disruption preflight",
            command=(
                "systemctl --user show --property=ActiveState,MainPID,NRestarts -- "
                f"synapse-hub.service && {synapse_word} who {project_option} {uri_option}"
            ),
            expected=(
                "inspect the exact hub PID and every live claim/waiter; restart commands "
                "remain withheld without fresh disruption authority"
            ),
        ),
        RedeployCheck(
            label="Roster reconnect",
            command=f"{synapse_word} who {project_option} {uri_option}",
            expected="active claims and waiters are visible",
        ),
        RedeployCheck(
            label="Durable state replay",
            command=(
                f"sqlite3 -- {db} {shell_command_arg(EVENT_SUMMARY_SQL)} && "
                f"{synapse_word} state {uri_option}"
            ),
            expected="event log is readable and replayed claims remain visible",
        ),
        RedeployCheck(
            label="Git hook wiring",
            command=f"{synapse_word} git-hook test",
            expected="claim-aware post-commit/post-merge hook path still resolves",
        ),
    ]
    if authorized_hub_pid is None:
        return checks
    if authorized_hub_pid <= 1:
        raise ValueError("authorized_hub_pid must be a live process id greater than one")

    units = " ".join(
        (
            "synapse-hub.service",
            shell_command_arg(presence_unit),
            shell_command_arg(arm_unit),
        )
    )
    pid_guarded_restart = (
        'test "$(systemctl --user show --property=MainPID --value -- '
        'synapse-hub.service)" = "$1"; '
        f"systemctl --user restart -- {units}; "
        f"systemctl --user status --no-pager -- {units}"
    )
    local_lock = '"${XDG_RUNTIME_DIR:?XDG_RUNTIME_DIR is required}"/synapse-channel-redeploy.lock'
    checks.insert(
        2,
        RedeployCheck(
            label="Authorised locked service restart",
            command=(
                f"flock --exclusive --nonblock {local_lock} "
                f"sh -ceu {shell_command_arg(pid_guarded_restart)} sh "
                f"{authorized_hub_pid}"
            ),
            expected=(
                "the exact authorised hub PID was still current; hub, project presence, "
                "and directed waiter restarted under one host-local custody lock"
            ),
        ),
    )
    return checks


def render_redeploy_checklist(checks: list[RedeployCheck]) -> list[str]:
    """Render ``checks`` as stable, copyable CLI output.

    Parameters
    ----------
    checks : list[RedeployCheck]
        Ordered checks returned by :func:`build_redeploy_checklist`.

    Returns
    -------
    list[str]
        Lines suitable for printing to stdout.
    """
    lines = ["synapse doctor: release redeploy checklist"]
    for index, check in enumerate(checks, start=1):
        lines.extend(
            [
                f"[{index}] {terminal_text(check.label)}",
                f"    command: {terminal_text(check.command)}",
                f"    expected: {terminal_text(check.expected)}",
            ]
        )
    return lines


def _shell_word(value: str) -> str:
    """Return a shell word while preserving conventional ``~/`` expansion."""
    if value.startswith("~/"):
        return f'"${{HOME}}"/{shell_command_arg(value[2:])}'
    return shell_command_arg(value)
