# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the desired-versus-effective service report
"""The service report on real transient units, and on recorded enabled units.

A transient unit cannot be enabled, so the rows that depend on enablement
replay what ``systemctl --user show`` printed for installed units. Everything
else runs against the real user manager with units the test owns, selected by
a name pattern only the test matches.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from functools import partial
from pathlib import Path

import pytest

from synapse_channel.cli import main as cli_main
from synapse_channel.service_dependencies import ServiceQueryError
from synapse_channel.service_status import (
    ServiceReport,
    ServiceRow,
    collect,
    listed_units,
    recovery_commands,
    render,
)
from synapse_channel.tmux_server import TmuxServerPlacement
from synapse_channel.waker_commands import run_waker_command

_RUNNER = partial(run_waker_command, timeout=30.0)
_NO_TMUX = TmuxServerPlacement(None, None)


def _manager_available() -> bool:
    if not sys.platform.startswith("linux") or shutil.which("systemd-run") is None:
        return False
    completed = subprocess.run(
        ["systemctl", "--user", "show", "--property=Version", "--value"],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0 and bool(completed.stdout.strip())


class Owned:
    """Transient units of one test, all under one name prefix."""

    def __init__(self) -> None:
        self.tag = uuid.uuid4().hex[:10]
        self.units: list[str] = []

    @property
    def pattern(self) -> str:
        return f"synapse-test-{self.tag}-*"

    def unit(self, name: str) -> str:
        return f"synapse-test-{self.tag}-{name}.service"

    def start(self, name: str, properties: list[str], command: list[str]) -> str:
        unit = self.unit(name)
        completed = subprocess.run(
            [
                "systemd-run",
                "--user",
                "--quiet",
                f"--unit={unit}",
                *(f"--property={item}" for item in properties),
                *command,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        self.units.append(unit)
        return unit


@pytest.fixture
def owned() -> Iterator[Owned]:
    if not _manager_available():
        pytest.skip("no systemd user manager answers here")
    units = Owned()
    try:
        yield units
    finally:
        for unit in reversed(units.units):
            subprocess.run(["systemctl", "--user", "stop", unit], capture_output=True, check=False)
            subprocess.run(
                ["systemctl", "--user", "reset-failed", unit], capture_output=True, check=False
            )


def _wait_for(unit: str, active_state: str) -> None:
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        shown = subprocess.run(
            ["systemctl", "--user", "show", unit, "--property=ActiveState", "--value"],
            capture_output=True,
            text=True,
            check=False,
        )
        if shown.stdout.strip() == active_state:
            return
        time.sleep(0.2)
    raise AssertionError(f"{unit} did not become {active_state}")


def test_real_units_are_reported_with_their_state_limit_and_relation(owned: Owned) -> None:
    sleep = ["/usr/bin/sleep", "infinity"]
    hub = owned.start("hub", [], sleep)
    ordered = owned.start("ordered", [f"After={hub}"], sleep)
    requiring = owned.start("requiring", [f"Requires={hub}", f"After={hub}"], sleep)
    spent = owned.start(
        "spent",
        ["Restart=always", "RestartSec=1", "StartLimitIntervalSec=60", "StartLimitBurst=2"],
        ["/bin/false"],
    )
    looping = owned.start(
        "looping", ["Restart=always", "RestartSec=20", "StartLimitIntervalSec=0"], ["/bin/false"]
    )
    limited = owned.start(
        "limited",
        ["Restart=always", "RestartSec=20", "StartLimitIntervalSec=600", "StartLimitBurst=9"],
        ["/bin/false"],
    )
    _wait_for(spent, "failed")
    _wait_for(looping, "activating")
    _wait_for(limited, "activating")

    report = collect(runner=_RUNNER, tmux_server=_NO_TMUX, hub_unit=hub, pattern=owned.pattern)

    rows = {row.unit: row for row in report.rows}
    assert [row.unit for row in report.rows] == [hub, *sorted(rows.keys() - {hub})]
    assert set(rows) == {hub, ordered, requiring, spent, looping, limited}
    assert rows[hub].hub_relations == () and rows[hub].active_state == "active"
    assert rows[ordered].hub_relations == ("Before",)
    assert rows[requiring].hub_relations == ("RequiredBy", "Before")
    assert all(row.desired == "transient" and row.owns is None for row in report.rows)
    assert rows[hub].attention == "" and rows[ordered].attention == ""

    assert (rows[spent].active_state, rows[spent].restarts) == ("failed", 2)
    assert rows[spent].start_limit == "2 starts in 1min"
    assert rows[spent].attention == "failed"

    assert rows[looping].sub_state == "auto-restart"
    assert rows[looping].start_limit == "unlimited"
    assert rows[looping].attention == (
        "restarting without a start limit (0 restarts); regenerate the unit"
    )
    assert rows[limited].start_limit == "9 starts in 10min"
    assert rows[limited].attention == "restarting (0 restarts so far)"

    assert [row.unit for row in report.needs_attention] == sorted([limited, looping, spent])
    assert listed_units(runner=_RUNNER, pattern=owned.pattern) == tuple(sorted(rows))
    # Transient units are not enabled, so no recovery command is offered for them.
    assert recovery_commands(report.rows) == []
    assert render(report)[-1] == "units: 6, needing attention: 3"


def test_the_command_prints_text_or_json_and_signals_attention(
    owned: Owned, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    hub = owned.start("hub", [], ["/usr/bin/sleep", "infinity"])
    base = [
        "service",
        "status",
        f"--hub-unit={hub}",
        f"--pattern={owned.pattern}",
        f"--tmux-bin={tmp_path / 'no-tmux-here'}",
    ]

    assert cli_main(base) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"{hub}: wanted transient, is active/running, restarts 0, result success, "
        "start limit 5 starts in 10s",
        "no tmux server answers",
        "units: 1, needing attention: 0",
    ]

    spent = owned.start(
        "spent",
        ["Restart=always", "RestartSec=1", "StartLimitIntervalSec=60", "StartLimitBurst=1"],
        ["/bin/false"],
    )
    _wait_for(spent, "failed")

    assert cli_main([*base, "--json"]) == 1
    document = json.loads(capsys.readouterr().out)
    assert document["hub_unit"] == hub
    assert document["attention"] == [spent] and document["healthy"] is False
    assert document["tmux_server"] == {"pid": None, "unit": None, "in_service_unit": False}
    assert [unit["unit"] for unit in document["units"]] == [hub, spent]
    assert document["units"][1] == {
        "unit": spent,
        "hub_relations": [],
        "desired": "transient",
        "active_state": "failed",
        "sub_state": "failed",
        "restarts": 1,
        "result": "exit-code",
        "start_limit": "1 starts in 1min",
        "owns": None,
        "attention": "failed",
    }


def test_the_command_returns_two_when_the_service_manager_does_not_answer(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)

    assert cli_main(["service", "status", f"--tmux-bin={tmp_path / 'no-tmux-here'}"]) == 2
    assert capsys.readouterr().out.startswith("synapse service status: failed: systemctl --user")


class Recorded:
    """Replay recorded ``systemctl`` output by the kind of question asked."""

    def __init__(self, *, relations: str, listing: str, shown: str) -> None:
        self.relations, self.listing, self.shown = relations, listing, shown

    def __call__(
        self,
        args: list[str],
        *,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        if "list-units" in args:
            return subprocess.CompletedProcess(args, 0, self.listing, "")
        if "--property=RequiredBy" in args:
            return subprocess.CompletedProcess(args, 0, self.relations, "")
        return subprocess.CompletedProcess(args, 0, self.shown, "")


def _shown(unit: str, state: str, active: str, sub: str, **extra: str) -> str:
    fields = {
        "Id": unit,
        "UnitFileState": state,
        "ActiveState": active,
        "SubState": sub,
        "NRestarts": "0",
        "Result": "success",
        "FragmentPath": "",
        "StartLimitBurst": "30",
        "StartLimitIntervalUSec": "30min",
    }
    fields.update(extra)
    return "".join(f"{key}={value}\n" for key, value in fields.items())


def test_enabled_units_that_are_down_get_recovery_commands(tmp_path: Path) -> None:
    fragment = tmp_path / "synapse-presence@.service"
    fragment.write_text(
        "# SPDX-License-Identifier: AGPL-3.0-or-later\n"
        "# Owns: one presence listener process for %I\n\n[Unit]\n",
        encoding="utf-8",
    )
    spent = "synapse-presence@SC\\x2dNEUROCORE.service"
    runner = Recorded(
        relations='WantedBy="synapse-presence@SC\\\\x2dNEUROCORE.service" default.target\n'
        'Before="synapse-presence@SC\\\\x2dNEUROCORE.service" synapse-arm@a.service\n',
        listing=(
            "synapse-hub.service loaded active running hub\n"
            "synapse-arm@a.service loaded inactive dead arm\n"
            "synapse-arm@b.service loaded failed failed arm\n"
            "synapse-old.service loaded active running old\n"
            "synapse-odd.timer loaded active waiting timer\n"
        ),
        shown="\n".join(
            [
                _shown("synapse-hub.service", "enabled", "active", "running"),
                _shown("synapse-arm@a.service", "enabled", "inactive", "dead"),
                _shown("synapse-arm@b.service", "disabled", "failed", "failed"),
                _shown(
                    "synapse-old.service",
                    "linked",
                    "active",
                    "running",
                    StartLimitIntervalUSec="0",
                    NRestarts="389706",
                    FragmentPath=str(tmp_path / "missing.service"),
                ),
                _shown(
                    spent,
                    "enabled",
                    "failed",
                    "failed",
                    NRestarts="30",
                    Result="exit-code",
                    FragmentPath=str(fragment),
                ),
            ]
        ),
    )

    report = collect(runner=runner, tmux_server=TmuxServerPlacement(41, "bridge.service"))

    # The hub leads although other names sort before it.
    assert [row.unit for row in report.rows] == [
        "synapse-hub.service",
        "synapse-arm@a.service",
        "synapse-arm@b.service",
        "synapse-old.service",
        spent,
    ]
    rows = {row.unit: row for row in report.rows}
    assert rows[spent] == ServiceRow(
        unit=spent,
        hub_relations=("WantedBy", "Before"),
        desired="enabled",
        active_state="failed",
        sub_state="failed",
        restarts=30,
        result="exit-code",
        start_limit="30 starts in 30min",
        owns="one presence listener process for %I",
        attention="enabled but failed: it stopped trying; reset and start it",
    )
    assert rows["synapse-arm@a.service"].attention == "enabled but not running"
    assert rows["synapse-arm@b.service"].attention == "failed"
    assert rows["synapse-arm@b.service"].desired == "disabled"
    assert rows["synapse-old.service"].desired == "enabled"
    assert rows["synapse-old.service"].start_limit == "unlimited"
    assert rows["synapse-old.service"].attention == "" and rows["synapse-old.service"].owns is None
    assert rows["synapse-hub.service"].attention == ""
    assert "synapse-odd.timer" not in rows

    assert recovery_commands(report.rows) == [
        "systemctl --user reset-failed -- synapse-arm@a.service "
        "'synapse-presence@SC\\x2dNEUROCORE.service'",
        "systemctl --user start -- synapse-arm@a.service "
        "'synapse-presence@SC\\x2dNEUROCORE.service'",
    ]
    lines = render(report)
    assert (
        f"{spent} [WantedBy, Before]: wanted enabled, is failed/failed, restarts 30, "
        in lines[lines.index("  owns: one presence listener process for %I") - 1]
    )
    assert "  attention: enabled but failed: it stopped trying; reset and start it" in lines
    assert "tmux server pid 41 is in bridge.service" in lines
    assert (
        "  attention: stopping bridge.service would kill every tmux session; "
        "run: synapse service tmux-server --move"
    ) in lines
    assert "units: 5, needing attention: 3" in lines
    assert lines[-3] == (
        "to bring back units that are enabled but not running, after checking why:"
    )
    assert report.to_json()["tmux_server"] == {
        "pid": 41,
        "unit": "bridge.service",
        "in_service_unit": True,
    }
    assert report.to_json()["attention"] == sorted(
        ["synapse-arm@a.service", "synapse-arm@b.service", spent]
    )
    assert not report.healthy and report.to_json()["healthy"] is False


def test_a_tmux_server_inside_a_service_unit_alone_makes_the_report_unhealthy() -> None:
    row = ServiceRow(
        "synapse-hub.service",
        (),
        "enabled",
        "active",
        "running",
        0,
        "success",
        "unlimited",
        None,
        "",
    )

    inside = ServiceReport("synapse-hub.service", (row,), TmuxServerPlacement(41, "bridge.service"))
    outside = ServiceReport("synapse-hub.service", (row,), TmuxServerPlacement(41, "tmux.scope"))

    assert inside.needs_attention == () and not inside.healthy
    assert outside.healthy and outside.to_json()["healthy"] is True


def test_a_tmux_server_outside_every_unit_is_named_as_such() -> None:
    report = ServiceReport("synapse-hub.service", (), TmuxServerPlacement(41, None))

    assert render(report) == [
        "tmux server pid 41 is in no service or scope unit",
        "units: 0, needing attention: 0",
    ]


@pytest.mark.parametrize(
    ("fields", "limit"),
    [
        ({"StartLimitIntervalUSec": "infinity"}, "unlimited"),
        ({"StartLimitIntervalUSec": ""}, "unlimited"),
        ({"StartLimitBurst": "0"}, "unlimited"),
        ({"StartLimitBurst": ""}, "unlimited"),
        ({"NRestarts": "[not set]", "UnitFileState": ""}, "30 starts in 30min"),
    ],
)
def test_unusual_property_values_are_read_without_guessing(
    fields: dict[str, str], limit: str, tmp_path: Path
) -> None:
    unreadable = tmp_path / "binary.service"
    unreadable.write_bytes(b"\xff\xfe# Owns: nothing\n")
    silent = tmp_path / "silent.service"
    silent.write_text("# Owns:   \n[Unit]\n", encoding="utf-8")
    for fragment in (unreadable, silent):
        runner = Recorded(
            relations="",
            listing="",
            shown=_shown(
                "synapse-hub.service",
                "enabled",
                "active",
                "running",
                FragmentPath=str(fragment),
                **fields,
            ),
        )

        row = collect(runner=runner, tmux_server=_NO_TMUX).rows[0]

        assert row.start_limit == limit
        assert row.owns is None and row.restarts == 0
        assert row.desired == ("unknown" if "UnitFileState" in fields else "enabled")


def test_an_answer_for_other_units_than_asked_is_refused() -> None:
    runner = Recorded(
        relations="",
        listing="synapse-a.service loaded active running a\n",
        shown=_shown("synapse-hub.service", "enabled", "active", "running") + "\nnoise\n",
    )

    with pytest.raises(
        ServiceQueryError, match=r"missing \['synapse-a.service'\], unexpected \[\]"
    ):
        collect(runner=runner, tmux_server=_NO_TMUX)
