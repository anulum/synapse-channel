# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for reading hub dependents and unit states
"""Reading answers of the service manager, and the answers it can fail to give.

The real service manager is exercised in ``test_hub_restart.py``. The cases
here are the ones a healthy host does not produce on request: a failed, slow or
absent ``systemctl``, an incomplete answer, and the exact text of escaped unit
names, which is replayed as the service manager wrote it.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

from synapse_channel.hub_restart import restart_hub
from synapse_channel.service_dependencies import (
    HUB_UNIT,
    Dependent,
    ServiceQueryError,
    UnitState,
    compare_states,
    hub_dependents,
    parse_unit_list,
    unit_states,
)


class Answer:
    """Replay one recorded ``systemctl`` answer and keep the command asked."""

    def __init__(
        self, stdout: str = "", *, returncode: int = 0, stderr: str = "", error: object = None
    ) -> None:
        self.stdout, self.returncode, self.stderr, self.error = stdout, returncode, stderr, error
        self.commands: list[list[str]] = []

    def __call__(
        self,
        args: list[str],
        *,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        assert capture_output and text and not check
        self.commands.append(args)
        if isinstance(self.error, BaseException):
            raise self.error
        return subprocess.CompletedProcess(args, self.returncode, self.stdout, self.stderr)


def _state(unit: str, active: str = "active", sub: str = "running", pid: int = 10) -> UnitState:
    return UnitState(unit, active, sub, pid, 0, "success")


# Recorded from `systemctl --user show` on systemd 255: a name with a backslash
# is double-quoted and its backslash doubled.
_RECORDED = (
    "RequiredBy=synapse-agent-tmux-a.service\n"
    "BoundBy=\n"
    "ConsistsOf=\n"
    'WantedBy="synapse-presence@SC\\\\x2dNEUROCORE.service" default.target '
    "synapse-presence@CEO.service\n"
    'Before=shutdown.target "synapse-presence@SC\\\\x2dNEUROCORE.service" '
    "synapse-agent-tmux-a.service synapse-arm@x.service synapse-hub.service\n"
)


def test_escaped_names_are_read_back_exactly_and_non_services_left_out() -> None:
    answer = Answer(_RECORDED)

    dependents = hub_dependents(runner=answer)

    assert dependents == (
        Dependent("synapse-agent-tmux-a.service", ("RequiredBy", "Before")),
        Dependent("synapse-arm@x.service", ("Before",)),
        Dependent("synapse-presence@CEO.service", ("WantedBy",)),
        Dependent("synapse-presence@SC\\x2dNEUROCORE.service", ("WantedBy", "Before")),
    )
    assert [item.restarted_with_hub for item in dependents] == [True, False, False, False]
    assert answer.commands == [
        [
            "systemctl",
            "--user",
            "show",
            "--property=RequiredBy",
            "--property=BoundBy",
            "--property=ConsistsOf",
            "--property=WantedBy",
            "--property=Before",
            "--",
            HUB_UNIT,
        ]
    ]


@pytest.mark.parametrize("relation", ["BoundBy", "ConsistsOf"])
def test_bound_and_part_of_units_are_restarted_with_the_hub(relation: str) -> None:
    dependents = hub_dependents(runner=Answer(f"{relation}=a.service\nOther=b.service\nnoise\n"))

    assert dependents == (Dependent("a.service", (relation,)),)
    assert dependents[0].restarted_with_hub


def test_an_unreadable_unit_list_is_an_error_not_a_guess() -> None:
    with pytest.raises(ServiceQueryError, match="unreadable unit list"):
        parse_unit_list('a.service "unterminated')
    assert parse_unit_list("") == ()


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        (Answer(returncode=1, stderr="Failed to connect to bus\n"), "Failed to connect to bus"),
        (Answer(returncode=1), r"failed: systemctl --user show .* synapse-hub\.service$"),
        (Answer(error=subprocess.TimeoutExpired("systemctl", 30)), "timed out: systemctl"),
        (Answer(error=FileNotFoundError("systemctl")), "failed: systemctl .* — systemctl"),
    ],
)
def test_a_service_manager_that_does_not_answer_is_reported(answer: Answer, message: str) -> None:
    with pytest.raises(ServiceQueryError, match=message):
        hub_dependents(runner=answer)
    with pytest.raises(ServiceQueryError):
        unit_states(["a.service"], runner=answer)


def test_states_are_read_per_unit_and_duplicates_asked_once() -> None:
    answer = Answer(
        "Id=a.service\nActiveState=active\nSubState=running\nMainPID=41\nNRestarts=3\n"
        "Result=success\n\n"
        "Id=b.service\nActiveState=activating\nSubState=auto-restart\nMainPID=0\n"
        "NRestarts=389706\nResult=exit-code\n"
    )

    states = unit_states(["a.service", "b.service", "a.service"], runner=answer)

    assert states == {
        "a.service": UnitState("a.service", "active", "running", 41, 3, "success"),
        "b.service": UnitState("b.service", "activating", "auto-restart", 0, 389706, "exit-code"),
    }
    assert states["a.service"].active and not states["b.service"].active
    assert answer.commands[0][-3:] == ["--", "a.service", "b.service"]


def test_missing_fields_are_unknown_and_non_numbers_are_zero() -> None:
    states = unit_states(["a.service"], runner=Answer("Id=a.service\nMainPID=[not set]\n"))

    assert states["a.service"] == UnitState("a.service", "unknown", "unknown", 0, 0, "unknown")


def test_no_unit_asks_nothing() -> None:
    answer = Answer(returncode=1)

    assert unit_states([], runner=answer) == {}
    assert answer.commands == []


@pytest.mark.parametrize(
    "stdout",
    [
        "Id=a.service\nActiveState=active\n",
        "Id=a.service\n\nId=b.service\n\nId=c.service\n",
        "ActiveState=active\n",
    ],
)
def test_an_answer_for_other_units_than_asked_is_refused(stdout: str) -> None:
    with pytest.raises(ServiceQueryError, match="other units than asked"):
        unit_states(["a.service", "b.service"], runner=Answer(stdout))


def test_each_kind_of_change_is_named() -> None:
    before = {
        "same.service": _state("same.service", pid=1),
        "again.service": _state("again.service", pid=2),
        "gone.service": _state("gone.service", pid=3),
        "new.service": _state("new.service", "inactive", "dead", 0),
        "down.service": _state("down.service", "failed", "failed", 0),
        "moved.service": _state("moved.service", "activating", "auto-restart", 0),
    }
    after = {
        "same.service": _state("same.service", pid=1),
        "again.service": _state("again.service", pid=22),
        "gone.service": _state("gone.service", "activating", "auto-restart", 0),
        "new.service": _state("new.service", pid=4),
        "down.service": _state("down.service", "failed", "failed", 0),
        "moved.service": _state("moved.service", "failed", "failed", 0),
    }

    changes = compare_states(before, after)

    assert [(change.unit, change.kind) for change in changes] == [
        ("again.service", "restarted"),
        ("down.service", "unchanged"),
        ("gone.service", "stopped"),
        ("moved.service", "changed"),
        ("new.service", "started"),
        ("same.service", "unchanged"),
    ]
    assert changes[0].before.main_pid == 2 and changes[0].after.main_pid == 22


def test_observations_of_different_units_cannot_be_compared() -> None:
    with pytest.raises(ServiceQueryError, match="do not cover the same units"):
        compare_states({"a.service": _state("a.service")}, {"b.service": _state("b.service")})


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_off_linux_the_restart_is_refused_before_anything_is_asked(
    platform: str, tmp_path: Path
) -> None:
    answer = Answer(returncode=1)

    report = restart_hub(
        authorized_hub_pid=4242,
        runner=answer,
        hub_answers=lambda: True,
        lock_path=tmp_path / "restart.lock",
        platform=platform,
    )

    assert not report.ok and not report.restarted
    assert report.lines == ("refused: restarting the hub unit requires Linux with systemd",)
    assert answer.commands == []
    assert not (tmp_path / "restart.lock").exists()


def test_the_command_modules_import_where_the_lock_primitive_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Command discovery imports these modules on every platform. A None entry
    # makes ``import fcntl`` raise ImportError, as on a platform without it.
    monkeypatch.setitem(sys.modules, "fcntl", None)
    for name in ("synapse_channel.hub_restart", "synapse_channel.cli_service"):
        monkeypatch.delitem(sys.modules, name, raising=False)

    for name in ("synapse_channel.hub_restart", "synapse_channel.cli_service"):
        assert importlib.import_module(name).__name__ == name
