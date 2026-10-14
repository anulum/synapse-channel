# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for unit templates and their start budgets
"""Start budgets: the arithmetic, the unit text, the deploy mirror, real systemd.

Four protections:

1. **Arithmetic** — the delays follow systemd's rule and a budget that could
   never be used up is refused when it is built.
2. **Unit text** — every generated unit carries its budget in the right
   sections, states what it owns, and never requires the hub.
3. **Deploy mirror** — the checked-in ``deploy/*.service`` templates carry the
   same budget and ownership lines as the renderers.
4. **Real systemd** — a transient user unit that fails at once, started with
   the lines a budget renders, becomes failed after exactly the allowed
   restarts and in the time the budget predicts.
"""

from __future__ import annotations

import shutil
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from synapse_channel.service_templates import (
    ARM_OWNS,
    HUB_OWNS,
    HUB_START_BUDGET,
    LISTENER_START_BUDGET,
    PRESENCE_OWNS,
    WAKER_OWNS,
    StartBudget,
    render_arm_unit,
    render_hub_unit,
    render_presence_unit,
    render_waker_unit,
)

_DEPLOY = Path(__file__).resolve().parent.parent / "deploy"
_SYN_BIN = "/home/test/.local/bin/synapse"

_UNITS: dict[str, tuple[Callable[..., str], StartBudget, str]] = {
    "synapse-hub.service": (render_hub_unit, HUB_START_BUDGET, HUB_OWNS),
    "synapse-presence@.service": (render_presence_unit, LISTENER_START_BUDGET, PRESENCE_OWNS),
    "synapse-arm@.service": (render_arm_unit, LISTENER_START_BUDGET, ARM_OWNS),
    "synapse-waker@.service": (render_waker_unit, LISTENER_START_BUDGET, WAKER_OWNS),
}


def _section(unit_text: str, name: str) -> list[str]:
    """Return the directive lines of one section of a unit file."""
    lines = unit_text.splitlines()
    start = lines.index(f"[{name}]") + 1
    body: list[str] = []
    for line in lines[start:]:
        if line.startswith("["):
            break
        if line and not line.startswith("#"):
            body.append(line)
    return body


# ---------------------------------------------------------------------------
# 1. Arithmetic
# ---------------------------------------------------------------------------


def test_delays_grow_by_the_systemd_rule_and_stop_at_the_maximum() -> None:
    budget = StartBudget(restart_sec=1, max_delay_sec=8, steps=3, burst=6, interval_sec=60)

    assert budget.restart_delays() == pytest.approx((1.0, 2.0, 4.0, 8.0, 8.0, 8.0))
    assert budget.seconds_to_failed() == pytest.approx(31.0)


def test_without_backoff_every_delay_is_the_first_one() -> None:
    budget = StartBudget(restart_sec=5, max_delay_sec=60, steps=5, burst=30, interval_sec=1800)

    assert budget.restart_delays(backoff=False) == (5.0,) * 30
    assert budget.seconds_to_failed(backoff=False) == 150.0


def test_equal_first_and_maximum_delay_needs_no_growth() -> None:
    budget = StartBudget(restart_sec=3, max_delay_sec=3, steps=2, burst=4, interval_sec=60)

    assert budget.restart_delays() == (3.0, 3.0, 3.0, 3.0)


def test_the_shipped_budgets_end_in_the_documented_time() -> None:
    assert HUB_START_BUDGET.seconds_to_failed() == pytest.approx(208.9, abs=0.1)
    assert HUB_START_BUDGET.seconds_to_failed(backoff=False) == 20.0
    assert LISTENER_START_BUDGET.seconds_to_failed() == pytest.approx(1585.4, abs=0.1)
    assert LISTENER_START_BUDGET.seconds_to_failed(backoff=False) == 150.0
    # A presence holder must outlast an ordinary hub restart or upgrade.
    assert LISTENER_START_BUDGET.seconds_to_failed() > 20 * 60


def test_a_budget_that_can_never_be_used_up_is_refused() -> None:
    # 30 starts spaced up to 60 s apart need about 1585 s; a 600 s window never
    # sees 30 of them, so systemd would restart the unit without end.
    with pytest.raises(ValueError, match="would never become failed"):
        StartBudget(restart_sec=5, max_delay_sec=60, steps=5, burst=30, interval_sec=600)


def test_a_budget_that_exactly_fills_its_window_is_refused() -> None:
    with pytest.raises(ValueError, match="would never become failed"):
        StartBudget(restart_sec=2, max_delay_sec=2, steps=1, burst=5, interval_sec=10)
    assert StartBudget(restart_sec=2, max_delay_sec=2, steps=1, burst=5, interval_sec=11)


@pytest.mark.parametrize(
    "fields",
    [
        {"restart_sec": 0},
        {"max_delay_sec": 1, "restart_sec": 2},
        {"steps": 0},
        {"burst": 1},
        {"interval_sec": 0},
    ],
)
def test_out_of_range_values_are_refused(fields: dict[str, int]) -> None:
    values = {"restart_sec": 2, "max_delay_sec": 8, "steps": 2, "burst": 3, "interval_sec": 60}
    values.update(fields)

    with pytest.raises(ValueError, match="at least"):
        StartBudget(**values)


def test_directive_lines_name_every_field_once() -> None:
    budget = StartBudget(restart_sec=2, max_delay_sec=30, steps=4, burst=10, interval_sec=600)

    assert budget.unit_directives() == "StartLimitIntervalSec=600\nStartLimitBurst=10\n"
    assert budget.service_directives() == (
        "Restart=always\nRestartSec=2\nRestartSteps=4\nRestartMaxDelaySec=30\n"
    )


# ---------------------------------------------------------------------------
# 2. Unit text
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("filename", sorted(_UNITS))
def test_generated_unit_carries_its_budget_in_the_right_sections(filename: str) -> None:
    renderer, budget, _owns = _UNITS[filename]
    unit = renderer(synapse_bin=_SYN_BIN)
    unit_lines = _section(unit, "Unit")
    service_lines = _section(unit, "Service")

    for line in budget.unit_directives().splitlines():
        assert unit_lines.count(line) == 1, (filename, line)
        assert line not in service_lines, (filename, line)
    for line in budget.service_directives().splitlines():
        assert service_lines.count(line) == 1, (filename, line)
        assert line not in unit_lines, (filename, line)
    assert "StartLimitIntervalSec=0" not in unit
    assert sum(line.startswith("StartLimit") for line in unit.splitlines()) == 2
    assert sum(line.startswith("RestartSec=") for line in unit.splitlines()) == 1


@pytest.mark.parametrize("filename", sorted(_UNITS))
def test_generated_unit_states_what_it_owns_and_never_requires_the_hub(filename: str) -> None:
    renderer, _budget, owns = _UNITS[filename]
    unit = renderer(synapse_bin=_SYN_BIN)

    assert unit.splitlines()[7] == f"# Owns: {owns}"
    # A unit that requires the hub is stopped whenever the hub is stopped.
    for forbidden in ("Requires=", "BindsTo=", "PartOf="):
        assert forbidden not in unit, (filename, forbidden)


def test_waker_owns_its_control_group_but_not_the_tmux_server() -> None:
    unit = render_waker_unit(synapse_bin=_SYN_BIN)

    assert "KillMode=control-group" in _section(unit, "Service")
    assert "never the tmux server" in WAKER_OWNS
    assert "tmux" not in "\n".join(
        line for line in _section(unit, "Service") if line.startswith("Exec")
    )


def test_exit_78_still_prevents_a_restart_where_it_did_before() -> None:
    assert "RestartPreventExitStatus=78" in _section(
        render_arm_unit(synapse_bin=_SYN_BIN), "Service"
    )
    assert "RestartPreventExitStatus=78" in _section(
        render_waker_unit(synapse_bin=_SYN_BIN), "Service"
    )


def test_systemd_accepts_every_generated_unit_without_a_complaint(tmp_path: Path) -> None:
    analyzer = shutil.which("systemd-analyze")
    if analyzer is None:
        pytest.skip("systemd-analyze is not installed")
    for filename, (renderer, _budget, _owns) in _UNITS.items():
        unit_path = tmp_path / filename
        unit_path.write_text(renderer(synapse_bin="/bin/true"), encoding="utf-8")
        completed = subprocess.run(
            [analyzer, "--user", "verify", str(unit_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, (filename, completed.stderr)
        assert "Unknown key" not in completed.stderr, (filename, completed.stderr)
        assert "Failed to parse" not in completed.stderr, (filename, completed.stderr)


# ---------------------------------------------------------------------------
# 3. Deploy mirror
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("filename", sorted(_UNITS))
def test_deploy_template_carries_the_same_budget_and_ownership(filename: str) -> None:
    _renderer, budget, owns = _UNITS[filename]
    template = (_DEPLOY / filename).read_text(encoding="utf-8")
    unit_lines = _section(template, "Unit")
    service_lines = _section(template, "Service")

    assert [line for line in unit_lines if line.startswith("StartLimit")] == (
        budget.unit_directives().splitlines()
    )
    assert [line for line in service_lines if line.startswith("Restart")] == [
        line
        for line in _section(_UNITS[filename][0](synapse_bin=_SYN_BIN), "Service")
        if line.startswith("Restart")
    ]
    assert f"# Owns: {owns}" in template.splitlines()
    for forbidden in ("Requires=", "BindsTo=", "PartOf="):
        assert forbidden not in "\n".join(unit_lines), (filename, forbidden)


# ---------------------------------------------------------------------------
# 4. Real systemd
# ---------------------------------------------------------------------------


def _user_manager_available() -> bool:
    """Return whether a systemd user manager answers on this host."""
    if shutil.which("systemd-run") is None or shutil.which("systemctl") is None:
        return False
    completed = subprocess.run(
        ["systemctl", "--user", "show", "--property=Version", "--value"],
        capture_output=True,
        text=True,
        check=False,
    )
    version = completed.stdout.strip().split(".")[0].split("~")[0]
    return completed.returncode == 0 and version.isdigit() and int(version) >= 254


def _show(unit: str, prop: str) -> str:
    """Return one property of a user unit."""
    completed = subprocess.run(
        ["systemctl", "--user", "show", unit, f"--property={prop}", "--value"],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip()


@pytest.fixture
def owned_unit() -> Iterator[str]:
    """Yield the name of a transient unit only this test owns, and remove it."""
    if not _user_manager_available():
        pytest.skip("no systemd user manager of version 254 or later answers here")
    unit = f"synapse-test-budget-{uuid.uuid4().hex[:12]}.service"
    try:
        yield unit
    finally:
        subprocess.run(["systemctl", "--user", "stop", unit], capture_output=True, check=False)
        subprocess.run(
            ["systemctl", "--user", "reset-failed", unit], capture_output=True, check=False
        )


def _start_failing_unit(unit: str, budget: StartBudget) -> float:
    """Start ``/bin/false`` under ``budget`` and return the start time."""
    properties = [
        f"--property={line}"
        for line in (budget.unit_directives() + budget.service_directives()).splitlines()
    ]
    started = time.monotonic()
    completed = subprocess.run(
        ["systemd-run", "--user", "--quiet", f"--unit={unit}", *properties, "/bin/false"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return started


def test_a_failing_unit_becomes_failed_when_the_budget_is_used_up(owned_unit: str) -> None:
    budget = StartBudget(restart_sec=1, max_delay_sec=4, steps=2, burst=4, interval_sec=60)
    assert budget.restart_delays() == pytest.approx((1.0, 2.0, 4.0, 4.0))
    started = _start_failing_unit(owned_unit, budget)

    # Before the budget is used up the unit is still trying.
    time.sleep(budget.seconds_to_failed() / 2)
    assert _show(owned_unit, "ActiveState") != "failed"

    deadline = started + budget.seconds_to_failed() + 10.0
    while time.monotonic() < deadline and _show(owned_unit, "ActiveState") != "failed":
        time.sleep(0.2)
    elapsed = time.monotonic() - started

    assert _show(owned_unit, "ActiveState") == "failed"
    assert _show(owned_unit, "NRestarts") == str(budget.burst)
    # systemd measured the same schedule the budget predicts.
    assert budget.seconds_to_failed() - 1.0 <= elapsed <= budget.seconds_to_failed() + 4.0

    # The failed state is terminal: nothing restarts the unit afterwards.
    time.sleep(budget.max_delay_sec + 1.0)
    assert _show(owned_unit, "ActiveState") == "failed"
    assert _show(owned_unit, "NRestarts") == str(budget.burst)


def test_without_a_start_limit_the_same_unit_restarts_past_the_budget(owned_unit: str) -> None:
    # Control for the test above: with the limit switched off, as the units
    # shipped before, the unit is still restarting after the budget's time.
    budget = StartBudget(restart_sec=1, max_delay_sec=1, steps=1, burst=3, interval_sec=60)
    properties = [
        "--property=StartLimitIntervalSec=0",
        *(f"--property={line}" for line in budget.service_directives().splitlines()),
    ]
    completed = subprocess.run(
        ["systemd-run", "--user", "--quiet", f"--unit={owned_unit}", *properties, "/bin/false"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr

    time.sleep(budget.seconds_to_failed() + 3.0)

    # A sampled `active/running` execution is part of the restart loop too.
    # Prove that it progresses beyond the budget rather than pinning a phase.
    restarts = int(_show(owned_unit, "NRestarts"))
    assert restarts > budget.burst
    deadline = time.monotonic() + budget.max_delay_sec + 5.0
    while time.monotonic() < deadline:
        assert _show(owned_unit, "ActiveState") != "failed"
        if int(_show(owned_unit, "NRestarts")) > restarts:
            break
        time.sleep(0.05)
    assert int(_show(owned_unit, "NRestarts")) > restarts
