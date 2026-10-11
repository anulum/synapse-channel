# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — which user units depend on the hub, and what state they are in
"""Read from systemd which user units depend on the hub and how they are running.

The service manager is the only source: the functions ask it for the hub's
reverse dependencies and for unit states, and never guess from unit names or
file text. Two kinds of relation are kept apart because they behave differently
when the hub is restarted:

* ``RequiredBy``, ``BoundBy`` and ``ConsistsOf`` are **restart-propagating**.
  systemd stops or restarts such a unit together with the hub.
* ``WantedBy`` and ``Before`` are **ordering or pull-in only**. systemd leaves
  such a unit alone; its process notices the hub going away and reconnects or
  exits by its own logic.

Everything here is read-only.
"""

from __future__ import annotations

import shlex
import subprocess  # nosec B404
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol

from synapse_channel.terminal_text import terminal_text

HUB_UNIT = "synapse-hub.service"
"""The user unit that runs the coordination hub."""

RESTART_PROPAGATING = ("RequiredBy", "BoundBy", "ConsistsOf")
"""Reverse relations through which systemd stops or restarts a unit with the hub."""

ORDER_ONLY = ("WantedBy", "Before")
"""Reverse relations that order or pull in a unit without stopping it."""

_STATE_PROPERTIES = ("Id", "ActiveState", "SubState", "MainPID", "NRestarts", "Result")


class CommandRunner(Protocol):
    """Callable compatible with a captured, bounded service-manager query."""

    def __call__(
        self,
        args: list[str],
        *,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        """Run ``args`` and return its completed process."""


class ServiceQueryError(RuntimeError):
    """The service manager did not answer a query, or answered incompletely."""


@dataclass(frozen=True, slots=True)
class Dependent:
    """One service unit that names the hub, and how it is related to it."""

    unit: str
    relations: tuple[str, ...]

    @property
    def restarted_with_hub(self) -> bool:
        """Return whether systemd stops or restarts this unit with the hub."""
        return any(relation in RESTART_PROPAGATING for relation in self.relations)


@dataclass(frozen=True, slots=True)
class UnitState:
    """The service manager's view of one unit at one moment."""

    unit: str
    active_state: str
    sub_state: str
    main_pid: int
    n_restarts: int
    result: str

    @property
    def active(self) -> bool:
        """Return whether the unit is running."""
        return self.active_state == "active"


@dataclass(frozen=True, slots=True)
class StateChange:
    """What happened to one unit between two observations.

    ``kind`` is ``unchanged`` (same process still running, or the same
    non-running state), ``restarted`` (running before and after with another
    main process), ``stopped`` (running before, not running after),
    ``started`` (not running before, running after) or ``changed`` (not
    running at either time, in a different state).
    """

    unit: str
    kind: str
    before: UnitState
    after: UnitState


def _query(command: list[str], *, runner: CommandRunner) -> str:
    """Run one read-only service-manager query and return its output."""
    rendered = " ".join(command)
    try:
        completed = runner(command, capture_output=True, text=True, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ServiceQueryError(f"timed out: {rendered}") from exc
    except OSError as exc:
        raise ServiceQueryError(f"failed: {rendered} — {exc}") from exc
    if completed.returncode != 0:
        detail = terminal_text((completed.stderr or completed.stdout).strip())
        raise ServiceQueryError(f"failed: {rendered}" + (f" — {detail}" if detail else ""))
    return completed.stdout


def parse_unit_list(value: str) -> tuple[str, ...]:
    """Split one unit-list property value as the service manager wrote it.

    systemd separates names with spaces and puts a name that contains a
    backslash, as every escaped instance name does, in double quotes with the
    backslash doubled.
    """
    try:
        return tuple(shlex.split(value))
    except ValueError as exc:
        raise ServiceQueryError(f"unreadable unit list: {terminal_text(value)}") from exc


def hub_dependents(*, runner: CommandRunner, hub_unit: str = HUB_UNIT) -> tuple[Dependent, ...]:
    """Return every service unit the service manager relates to the hub.

    Parameters
    ----------
    runner : CommandRunner
        Bounded command boundary for the one ``systemctl --user show`` query.
    hub_unit : str
        Exact name of the hub unit.

    Returns
    -------
    tuple of Dependent
        Service units only, sorted by name, each with every relation it has.
        Targets and other unit types are left out.

    Raises
    ------
    ServiceQueryError
        The query failed, timed out or returned an unreadable list.
    """
    relations = (*RESTART_PROPAGATING, *ORDER_ONLY)
    command = [
        "systemctl",
        "--user",
        "show",
        *(f"--property={relation}" for relation in relations),
        "--",
        hub_unit,
    ]
    found: dict[str, list[str]] = {}
    for line in _query(command, runner=runner).splitlines():
        relation, separator, value = line.partition("=")
        if not separator or relation not in relations:
            continue
        for unit in parse_unit_list(value):
            if unit.endswith(".service") and unit != hub_unit:
                found.setdefault(unit, []).append(relation)
    return tuple(
        Dependent(unit, tuple(relation for relation in relations if relation in found[unit]))
        for unit in sorted(found)
    )


def _integer(value: str) -> int:
    """Return a service-manager integer property, or 0 when it is not a number."""
    return int(value) if value.isdigit() else 0


def unit_states(units: Iterable[str], *, runner: CommandRunner) -> dict[str, UnitState]:
    """Return the current state of each exact unit name.

    Parameters
    ----------
    units : iterable of str
        Exact unit names. Duplicates are asked for once.
    runner : CommandRunner
        Bounded command boundary for the one ``systemctl --user show`` query.

    Returns
    -------
    dict
        One state per requested unit, keyed by unit name. An empty request
        returns an empty mapping without a query.

    Raises
    ------
    ServiceQueryError
        The query failed or timed out, or the answer does not cover exactly
        the requested units.
    """
    wanted = tuple(dict.fromkeys(units))
    if not wanted:
        return {}
    command = [
        "systemctl",
        "--user",
        "show",
        *(f"--property={name}" for name in _STATE_PROPERTIES),
        "--",
        *wanted,
    ]
    states: dict[str, UnitState] = {}
    for block in _query(command, runner=runner).split("\n\n"):
        fields = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        unit = fields.get("Id", "")
        if not unit:
            continue
        states[unit] = UnitState(
            unit=unit,
            active_state=fields.get("ActiveState", "unknown"),
            sub_state=fields.get("SubState", "unknown"),
            main_pid=_integer(fields.get("MainPID", "")),
            n_restarts=_integer(fields.get("NRestarts", "")),
            result=fields.get("Result", "unknown"),
        )
    if set(states) != set(wanted):
        missing = sorted(set(wanted) - set(states))
        extra = sorted(set(states) - set(wanted))
        raise ServiceQueryError(
            "service manager answered for other units than asked: "
            f"missing {missing}, unexpected {extra}"
        )
    return states


def compare_states(
    before: Mapping[str, UnitState], after: Mapping[str, UnitState]
) -> tuple[StateChange, ...]:
    """Return what happened to each unit between two observations.

    Raises
    ------
    ServiceQueryError
        The two observations do not cover the same units.
    """
    if set(before) != set(after):
        raise ServiceQueryError("the two observations do not cover the same units")
    changes: list[StateChange] = []
    for unit in sorted(before):
        earlier, later = before[unit], after[unit]
        if earlier.active and later.active:
            kind = "unchanged" if earlier.main_pid == later.main_pid else "restarted"
        elif earlier.active:
            kind = "stopped"
        elif later.active:
            kind = "started"
        elif (earlier.active_state, earlier.sub_state) == (later.active_state, later.sub_state):
            kind = "unchanged"
        else:
            kind = "changed"
        changes.append(StateChange(unit, kind, earlier, later))
    return tuple(changes)
