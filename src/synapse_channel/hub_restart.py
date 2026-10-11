# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — restart the hub unit as one job and report what it did to other units
"""Restart the hub as one service-manager job and compare every dependent unit.

A hub restart on a busy host has two ways to do damage. Stopping the hub as a
separate step lets units that want the hub start it again in the gap, and
stops every unit that requires it. And a unit that requires the hub is
restarted with it by systemd, together with whatever lives in its control
group. This module therefore:

* plans first, read-only: the hub's state, every dependent unit, its relation
  and its state;
* refuses to act without the exact current hub process id, and refuses when a
  restart would propagate to another unit unless that is explicitly allowed;
* sends exactly one ``restart`` job for the hub unit, never ``stop`` and
  ``start``, under a host-local lock shared with the redeploy checklist;
* waits for the hub to answer, lets the dependents settle, and reports for
  each one whether it is unchanged, restarted, stopped, started or changed.

It never starts, stops or resets another unit. Recovery commands are printed
for the operator.

The module imports on every platform, so command discovery and ``--help`` work
everywhere. The restart itself needs Linux with systemd and is refused
elsewhere before anything is asked or changed.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.service_dependencies import (
    HUB_UNIT,
    CommandRunner,
    Dependent,
    ServiceQueryError,
    StateChange,
    UnitState,
    compare_states,
    hub_dependents,
    unit_states,
)
from synapse_channel.terminal_text import shell_command_arg, terminal_text

LOCK_FILENAME = "synapse-channel-redeploy.lock"
"""Host-local lock file name, the same one the redeploy checklist command takes."""

DEFAULT_ANSWER_TIMEOUT = 60.0
"""Seconds to wait for the restarted hub to answer."""

DEFAULT_SETTLE_TIMEOUT = 45.0
"""Seconds to wait for dependents that were running to be running again."""


@dataclass(frozen=True, slots=True)
class HubRestartPlan:
    """What a hub restart would touch, observed before anything is changed."""

    hub_unit: str
    hub: UnitState
    dependents: tuple[Dependent, ...]
    states: dict[str, UnitState]

    @property
    def restarted_with_hub(self) -> tuple[Dependent, ...]:
        """Return the dependents systemd would stop or restart with the hub."""
        return tuple(item for item in self.dependents if item.restarted_with_hub)


@dataclass(frozen=True, slots=True)
class HubRestartReport:
    """Outcome of one restart request.

    ``restarted`` is true only when the single restart job was sent and
    returned success. ``ok`` is true only when, in addition, the hub answered
    and no dependent that was running is left not running.
    """

    ok: bool
    restarted: bool
    lines: tuple[str, ...]
    changes: tuple[StateChange, ...] = ()


def default_lock_path() -> Path:
    """Return the host-local lock path under the user's runtime directory.

    Raises
    ------
    ServiceQueryError
        ``XDG_RUNTIME_DIR`` is not set, so no host-local lock can be taken.
    """
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    if not runtime:
        raise ServiceQueryError("XDG_RUNTIME_DIR is required for the host-local restart lock")
    return Path(runtime) / LOCK_FILENAME


def plan_hub_restart(*, runner: CommandRunner, hub_unit: str = HUB_UNIT) -> HubRestartPlan:
    """Observe the hub and every dependent unit without changing anything.

    Raises
    ------
    ServiceQueryError
        The service manager did not answer completely.
    """
    dependents = hub_dependents(runner=runner, hub_unit=hub_unit)
    states = unit_states([hub_unit, *(item.unit for item in dependents)], runner=runner)
    hub = states.pop(hub_unit)
    return HubRestartPlan(hub_unit=hub_unit, hub=hub, dependents=dependents, states=states)


def _state_text(state: UnitState) -> str:
    """Return one unit state as short operator text."""
    return f"{state.active_state}/{state.sub_state} pid {state.main_pid}"


def render_plan(plan: HubRestartPlan) -> list[str]:
    """Return the plan as operator-facing lines."""
    lines = [
        f"hub unit: {terminal_text(plan.hub_unit)} — {_state_text(plan.hub)}, "
        f"restarts {plan.hub.n_restarts}",
        f"dependent service units: {len(plan.dependents)}",
    ]
    propagating = plan.restarted_with_hub
    lines.append(
        f"systemd would stop or restart with the hub: {len(propagating)}"
        + (":" if propagating else "")
    )
    for item in propagating:
        lines.append(
            f"  {terminal_text(item.unit)} [{', '.join(item.relations)}] "
            f"{_state_text(plan.states[item.unit])}"
        )
    running = sum(1 for state in plan.states.values() if state.active)
    lines.append(
        f"left alone by systemd: {len(plan.dependents) - len(propagating)} "
        f"({running} of all dependents running now)"
    )
    return lines


def _recovery_lines(changes: tuple[StateChange, ...]) -> list[str]:
    """Return the commands that bring back units the restart left not running."""
    stopped = [change.unit for change in changes if change.kind == "stopped"]
    if not stopped:
        return []
    units = " ".join(shell_command_arg(unit) for unit in stopped)
    return [
        "to bring them back, after checking why they stopped:",
        f"  systemctl --user reset-failed -- {units}",
        f"  systemctl --user start -- {units}",
    ]


def _refusal(
    plan: HubRestartPlan, authorized_hub_pid: int, allow_dependent_restart: bool
) -> str | None:
    """Return why the plan must not be carried out, or None when it may be."""
    if not plan.hub.active:
        return "refused: the hub unit is not running; there is nothing to restart"
    if plan.hub.main_pid != authorized_hub_pid:
        return (
            f"refused: the hub runs as pid {plan.hub.main_pid}, "
            f"the authorisation names pid {authorized_hub_pid}"
        )
    if plan.restarted_with_hub and not allow_dependent_restart:
        return (
            "refused: systemd would stop or restart the units listed above with the hub; "
            "remove their requirement on the hub or pass --allow-dependent-restart"
        )
    return None


def restart_hub(
    *,
    authorized_hub_pid: int,
    runner: CommandRunner,
    hub_answers: Callable[[], bool],
    lock_path: Path,
    allow_dependent_restart: bool = False,
    hub_unit: str = HUB_UNIT,
    answer_timeout: float = DEFAULT_ANSWER_TIMEOUT,
    settle_timeout: float = DEFAULT_SETTLE_TIMEOUT,
    poll_interval: float = 1.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    platform: str = sys.platform,
) -> HubRestartReport:
    """Restart the hub unit with one job and report every dependent's change.

    Parameters
    ----------
    authorized_hub_pid : int
        The hub's main process id for which the operator authorised the
        disruption. The restart is refused when the hub now runs as another
        process.
    runner : CommandRunner
        Bounded command boundary for every service-manager call.
    hub_answers : callable
        Returns true when the hub accepts a client. Called until it does or
        ``answer_timeout`` passes.
    lock_path : pathlib.Path
        Host-local lock file. A second restart or a redeploy holding it makes
        this call refuse at once.
    allow_dependent_restart : bool
        Proceed although systemd would stop or restart other units with the
        hub. False refuses and names them.
    hub_unit : str
        Exact hub unit name.
    answer_timeout, settle_timeout, poll_interval : float
        Waits in seconds: for the hub to answer, for dependents that were
        running to run again, and between two checks.
    clock, sleep : callable
        Monotonic time source and sleep function.
    platform : str
        Platform selector. Anything but Linux is refused before any query.

    Returns
    -------
    HubRestartReport
        See the class. A refusal has ``restarted`` false and changed nothing.

    Raises
    ------
    ServiceQueryError
        The service manager did not answer a read-only query. When this is
        raised after the job was sent, the message says so.
    """
    if not platform.startswith("linux"):
        return HubRestartReport(
            False,
            False,
            ("refused: restarting the hub unit requires Linux with systemd",),
        )
    import fcntl

    plan = plan_hub_restart(runner=runner, hub_unit=hub_unit)
    lines = render_plan(plan)
    refusal = _refusal(plan, authorized_hub_pid, allow_dependent_restart)
    if refusal:
        lines.append(refusal)
        return HubRestartReport(False, False, tuple(lines))

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lines.append(f"refused: another restart or redeploy holds {lock_path}")
            return HubRestartReport(False, False, tuple(lines))
        current = unit_states([hub_unit], runner=runner)[hub_unit]
        if current.main_pid != authorized_hub_pid:
            lines.append(
                f"refused: the hub changed to pid {current.main_pid} before the lock was held"
            )
            return HubRestartReport(False, False, tuple(lines))
        command = ["systemctl", "--user", "restart", "--", hub_unit]
        rendered = " ".join(command)
        try:
            completed = runner(command, capture_output=True, text=True, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            lines.append(f"uncertain: {rendered} — {exc}; the job may still run")
            return HubRestartReport(False, False, tuple(lines))
        if completed.returncode != 0:
            detail = terminal_text((completed.stderr or completed.stdout).strip())
            lines.append(f"failed: {rendered}" + (f" — {detail}" if detail else ""))
            return HubRestartReport(False, False, tuple(lines))
        lines.append(f"ok: {rendered} (one job)")
        return _verify(
            plan,
            lines,
            runner=runner,
            hub_answers=hub_answers,
            answer_timeout=answer_timeout,
            settle_timeout=settle_timeout,
            poll_interval=poll_interval,
            clock=clock,
            sleep=sleep,
        )
    finally:
        os.close(descriptor)


def _verify(
    plan: HubRestartPlan,
    lines: list[str],
    *,
    runner: CommandRunner,
    hub_answers: Callable[[], bool],
    answer_timeout: float,
    settle_timeout: float,
    poll_interval: float,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> HubRestartReport:
    """Wait for the hub and the dependents, then compare with the plan."""
    deadline = clock() + answer_timeout
    answered = hub_answers()
    while not answered and clock() < deadline:
        sleep(poll_interval)
        answered = hub_answers()
    lines.append("hub answers" if answered else f"hub did not answer within {answer_timeout:.0f} s")

    was_running = {unit for unit, state in plan.states.items() if state.active}
    units = [plan.hub_unit, *plan.states]
    try:
        deadline = clock() + settle_timeout
        after = unit_states(units, runner=runner)
        while clock() < deadline and any(not after[unit].active for unit in was_running):
            sleep(poll_interval)
            after = unit_states(units, runner=runner)
    except ServiceQueryError as exc:
        raise ServiceQueryError(f"the restart job was sent, then: {exc}") from exc

    hub_after = after.pop(plan.hub_unit)
    lines.append(f"hub unit now: {_state_text(hub_after)}, was pid {plan.hub.main_pid}")
    changes = compare_states(plan.states, after)
    counts: dict[str, int] = {}
    for change in changes:
        counts[change.kind] = counts.get(change.kind, 0) + 1
    summary = ", ".join(f"{count} {kind}" for kind, count in sorted(counts.items()))
    lines.append(f"dependents: {summary or 'none'}")
    for change in changes:
        if change.kind != "unchanged":
            lines.append(
                f"  {change.kind}: {terminal_text(change.unit)} — "
                f"{_state_text(change.before)} -> {_state_text(change.after)}"
            )
    lines.extend(_recovery_lines(changes))
    ok = answered and hub_after.active and counts.get("stopped", 0) == 0
    return HubRestartReport(ok, True, tuple(lines), changes)
