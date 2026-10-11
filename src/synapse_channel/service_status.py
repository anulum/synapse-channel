# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — desired and effective state of the local Synapse user services
"""Report, per user unit, what it should be doing and what it is doing.

The report is the feed for ``synapse service status`` and for a dashboard. It
is read from the service manager and from the unit files it names; nothing is
started, stopped or reset. For every unit it gives:

* **desired**: the unit-file state as the service manager reports it, and
  whether it means that the operator wants the unit running (``enabled`` or
  ``enabled-runtime`` only);
* **effective**: the active state, the restart count and the last result;
* **start limit**: the budget the loaded unit carries, or ``unlimited`` for a
  unit that would restart without end;
* **ownership**: the ``# Owns:`` line of its unit file, when it has one;
* **attention**: why the unit needs an operator, or an empty string.

A unit that is enabled but failed, for example a presence holder that used up
its start budget during a long hub outage, is the case this report exists to
make visible; the recovery commands are printed, never run.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from synapse_channel.service_dependencies import (
    HUB_UNIT,
    CommandRunner,
    ServiceQueryError,
    hub_dependents,
    query_service_manager,
)
from synapse_channel.terminal_text import shell_command_arg, terminal_text
from synapse_channel.tmux_server import TmuxServerPlacement

UNIT_PATTERN = "synapse-*"
"""Unit name pattern of the units Synapse generates."""

_PROPERTIES = (
    "Id",
    "UnitFileState",
    "ActiveState",
    "SubState",
    "NRestarts",
    "Result",
    "FragmentPath",
    "StartLimitBurst",
    "StartLimitIntervalUSec",
)

_ENABLED_STATES = frozenset({"enabled", "enabled-runtime"})
"""Unit-file states in which the operator has asked for the unit to run.

``linked`` and ``linked-runtime`` only make a unit file known to the manager;
``alias``, ``static``, ``indirect``, ``generated`` and ``transient`` say nothing
about the operator's wish either. They are reported as they are.
"""

OWNS_PREFIX = "# Owns: "
"""Start of the unit-file line that names the processes a unit owns."""


@dataclass(frozen=True, slots=True)
class ServiceRow:
    """One unit: what is wanted of it, what it does, and whether to look at it."""

    unit: str
    hub_relations: tuple[str, ...]
    desired: str
    wanted_running: bool
    active_state: str
    sub_state: str
    restarts: int
    result: str
    start_limit: str
    owns: str | None
    attention: str


@dataclass(frozen=True, slots=True)
class ServiceReport:
    """Every row, and where the tmux server lives."""

    hub_unit: str
    rows: tuple[ServiceRow, ...]
    tmux_server: TmuxServerPlacement

    @property
    def needs_attention(self) -> tuple[ServiceRow, ...]:
        """Return the rows an operator should look at."""
        return tuple(row for row in self.rows if row.attention)

    @property
    def healthy(self) -> bool:
        """Return whether no unit and no tmux placement needs an operator."""
        return not self.needs_attention and not self.tmux_server.in_service_unit

    def to_json(self) -> dict[str, object]:
        """Return the report as plain data for a machine reader."""
        return {
            "hub_unit": self.hub_unit,
            "units": [asdict(row) for row in self.rows],
            "tmux_server": {
                "pid": self.tmux_server.pid,
                "unit": self.tmux_server.unit,
                "in_service_unit": self.tmux_server.in_service_unit,
            },
            "attention": [row.unit for row in self.needs_attention],
            "healthy": self.healthy,
        }


def listed_units(*, runner: CommandRunner, pattern: str = UNIT_PATTERN) -> tuple[str, ...]:
    """Return the service units whose name matches ``pattern``.

    Both loaded units and unit files are asked for: a unit file that is
    enabled or linked but was never started is not loaded, and would be
    missing from a list of loaded units alone. Template files, whose name
    ends in ``@.service``, are not units and are left out.
    """
    found: set[str] = set()
    for query in (
        ["list-units", "--all", "--plain", "--no-legend", "--type=service"],
        ["list-unit-files", "--plain", "--no-legend", "--type=service"],
    ):
        command = ["systemctl", "--user", *query, "--", pattern]
        for line in query_service_manager(command, runner=runner).splitlines():
            name = line.split(None, 1)[0] if line.strip() else ""
            if name.endswith(".service") and not name.endswith("@.service"):
                found.add(name)
    return tuple(sorted(found))


def _start_limit(burst: str, interval: str) -> str:
    """Return the loaded start budget as short text."""
    if interval in {"", "0", "infinity"} or burst in {"", "0"}:
        return "unlimited"
    return f"{burst} starts in {interval}"


def _owns(fragment: str) -> str | None:
    """Return the ``# Owns:`` statement of a unit file, or None without one."""
    if not fragment:
        return None
    try:
        text = Path(fragment).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    for line in text.splitlines():
        if line.startswith(OWNS_PREFIX):
            return line[len(OWNS_PREFIX) :].strip() or None
    return None


def _attention(wanted: bool, active: str, sub: str, restarts: int, limit: str) -> str:
    """Return why a unit needs an operator, or an empty string."""
    if active == "failed":
        if wanted:
            return "enabled but failed: it stopped trying; reset and start it"
        return "failed"
    if wanted and active == "inactive":
        return "enabled but not running"
    if active == "activating" and sub == "auto-restart":
        if limit == "unlimited":
            return f"restarting without a start limit ({restarts} restarts); regenerate the unit"
        return f"restarting ({restarts} restarts so far)"
    return ""


def collect(
    *,
    runner: CommandRunner,
    tmux_server: TmuxServerPlacement,
    hub_unit: str = HUB_UNIT,
    pattern: str = UNIT_PATTERN,
) -> ServiceReport:
    """Read the report from the service manager.

    Parameters
    ----------
    runner : CommandRunner
        Bounded command boundary; four read-only queries are made.
    tmux_server : TmuxServerPlacement
        Where the tmux server lives, as read by the caller.
    hub_unit : str
        Exact hub unit name.
    pattern : str
        Name pattern of the units to list besides the hub and its dependents.

    Returns
    -------
    ServiceReport
        The hub first, then every other unit by name.

    Raises
    ------
    ServiceQueryError
        The service manager did not answer, or answered for other units.
    """
    relations = {
        item.unit: item.relations for item in hub_dependents(runner=runner, hub_unit=hub_unit)
    }
    others = sorted(
        (set(listed_units(runner=runner, pattern=pattern)) | set(relations)) - {hub_unit}
    )
    wanted = [hub_unit, *others]
    command = [
        "systemctl",
        "--user",
        "show",
        *(f"--property={name}" for name in _PROPERTIES),
        "--",
        *wanted,
    ]
    found: dict[str, dict[str, str]] = {}
    for block in query_service_manager(command, runner=runner).split("\n\n"):
        fields = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        if fields.get("Id"):
            found[fields["Id"]] = fields
    if set(found) != set(wanted):
        raise ServiceQueryError(
            "service manager answered for other units than asked: "
            f"missing {sorted(set(wanted) - set(found))}, "
            f"unexpected {sorted(set(found) - set(wanted))}"
        )
    rows = []
    for unit in wanted:
        fields = found[unit]
        state = fields.get("UnitFileState", "")
        desired = state or "unknown"
        wants_running = state in _ENABLED_STATES
        active = fields.get("ActiveState", "unknown")
        sub = fields.get("SubState", "unknown")
        count = fields.get("NRestarts", "")
        restarts = int(count) if count.isdigit() else 0
        limit = _start_limit(
            fields.get("StartLimitBurst", ""), fields.get("StartLimitIntervalUSec", "")
        )
        rows.append(
            ServiceRow(
                unit=unit,
                hub_relations=relations.get(unit, ()),
                desired=desired,
                wanted_running=wants_running,
                active_state=active,
                sub_state=sub,
                restarts=restarts,
                result=fields.get("Result", "unknown"),
                start_limit=limit,
                owns=_owns(fields.get("FragmentPath", "")),
                attention=_attention(wants_running, active, sub, restarts, limit),
            )
        )
    return ServiceReport(hub_unit=hub_unit, rows=tuple(rows), tmux_server=tmux_server)


def recovery_commands(rows: Sequence[ServiceRow]) -> list[str]:
    """Return the commands that bring back units that are enabled but not running."""
    down = [
        row.unit
        for row in rows
        if row.wanted_running and row.active_state in {"failed", "inactive"}
    ]
    if not down:
        return []
    units = " ".join(shell_command_arg(unit) for unit in down)
    return [
        f"systemctl --user reset-failed -- {units}",
        f"systemctl --user start -- {units}",
    ]


def render(report: ServiceReport) -> list[str]:
    """Return the report as operator-facing lines."""
    lines = []
    for row in report.rows:
        relation = f" [{', '.join(row.hub_relations)}]" if row.hub_relations else ""
        lines.append(
            f"{terminal_text(row.unit)}{relation}: unit file {row.desired}, "
            f"is {row.active_state}/{row.sub_state}, restarts {row.restarts}, "
            f"result {row.result}, start limit {row.start_limit}"
        )
        if row.owns:
            lines.append(f"  owns: {terminal_text(row.owns)}")
        if row.attention:
            lines.append(f"  attention: {row.attention}")
    server = report.tmux_server
    if server.running:
        where = server.unit or "no service or scope unit"
        lines.append(f"tmux server pid {server.pid} is in {terminal_text(where)}")
        if server.in_service_unit:
            lines.append(
                f"  attention: stopping {terminal_text(where)} would kill every tmux session; "
                "run: synapse service tmux-server --move"
            )
    else:
        lines.append("no tmux server answers")
    attention = report.needs_attention
    lines.append(f"units: {len(report.rows)}, needing attention: {len(attention)}")
    commands = recovery_commands(report.rows)
    if commands:
        lines.append("to bring back units that are enabled but not running, after checking why:")
        lines.extend(f"  {command}" for command in commands)
    return lines
