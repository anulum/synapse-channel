# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — release redeploy operations checklist helpers
"""Build operator checklists for post-release local fleet redeploys.

The checklist is intentionally side-effect free. ``synapse doctor`` can print it
after the live health checks, but the operator still chooses when to run the
restart and verification commands. That keeps release redeploy guidance
copyable without letting a diagnostic command mutate long-running services.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.client.agent import DEFAULT_HUB_URI
from synapse_channel.service_setup import CommandRunner, default_synapse_bin, escaped_instance

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

    Returns
    -------
    list[RedeployCheck]
        Ordered package, restart, reconnect, replay, and hook checks.
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
    quoted_project = shlex.quote(project)
    quoted_uri = shlex.quote(hub_uri)
    return [
        RedeployCheck(
            label="Package and executable",
            command=f"command -v {_shell_word(synapse)} && {_shell_word(synapse)} --version",
            expected="installed command reports the release version",
        ),
        RedeployCheck(
            label="Hub service restart",
            command=(
                "systemctl --user restart synapse-hub.service && "
                "systemctl --user status --no-pager synapse-hub.service"
            ),
            expected="hub service is active after restart",
        ),
        RedeployCheck(
            label="Presence daemon restart",
            command=(
                f"systemctl --user restart {shlex.quote(presence_unit)} && "
                f"systemctl --user status --no-pager {shlex.quote(presence_unit)}"
            ),
            expected=f"project presence for {project!r} reconnects",
        ),
        RedeployCheck(
            label="Wake listener restart",
            command=(
                f"systemctl --user restart {shlex.quote(arm_unit)} && "
                f"systemctl --user status --no-pager {shlex.quote(arm_unit)}"
            ),
            expected=f"directed waiter for {identity!r} is armed",
        ),
        RedeployCheck(
            label="Roster reconnect",
            command=f"synapse who --project {quoted_project} --uri {quoted_uri}",
            expected="active claims and waiters are visible after restart",
        ),
        RedeployCheck(
            label="Durable state replay",
            command=(
                f"sqlite3 {db} {shlex.quote(EVENT_SUMMARY_SQL)} && synapse state --uri {quoted_uri}"
            ),
            expected="event log is readable and replayed claims remain visible",
        ),
        RedeployCheck(
            label="Git hook wiring",
            command="synapse git-hook test",
            expected="claim-aware post-commit/post-merge hook path still resolves",
        ),
    ]


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
                f"[{index}] {check.label}",
                f"    command: {check.command}",
                f"    expected: {check.expected}",
            ]
        )
    return lines


def _shell_word(value: str) -> str:
    """Return a shell word while preserving conventional ``~/`` expansion."""
    if value.startswith("~/"):
        return value
    return shlex.quote(value)
