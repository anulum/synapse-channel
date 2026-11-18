# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — a temporary host for the dashboard's setup apply, never the real one
"""A temporary host for the setup apply: a home, a fake service manager, its inspections.

The command line's executor runs for real against it: a temporary home and unit
directory, and the fake ``systemctl`` the executor's own tests use
(``test_setup_executor._MANAGER``), so no service of the machine running the
tests is touched. The dashboard's inspection and the executor's own fresh
inspection both read this host as planned, and all well once the executor ran.

The setup apply tests and the cockpit's browser journey
(``clients/cockpit/e2e/dashboard_harness.py``) share it.
"""

from __future__ import annotations

import json
from pathlib import Path

import setup_plan_fixture as fixture
from synapse_channel.dashboard_setup_contract import SetupPostureDecision
from synapse_channel.dashboard_setup_http import SetupServing
from synapse_channel.dashboard_setup_planner import SetupPlanStore
from synapse_channel.dashboard_setup_receipts import SetupReceipt, SetupReceiptStore
from synapse_channel.setup_profiles import SetupProfile
from test_dashboard_setup_http import _receipts
from test_setup_executor import _manager, _stop_manager_processes

MADE: list[Host] = []
"""Every host made and not yet stopped, so whoever made them can stop their unit processes."""


class Host:
    """A temporary home, its fake service manager, and what the inspections see of it."""

    def __init__(self, tmp_path: Path, statuses: dict[str, str], *, hub_pid: int | None = None):
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.systemctl = _manager(tmp_path)
        MADE.append(self)
        self.statuses = dict(statuses)
        self.hub_pid = hub_pid
        self.executor_statuses: dict[str, str] | None = None
        self.executor_hub_pid: int | None = None
        self.applied = False
        self.after_statuses: dict[str, str] = {}
        self.unreadable_after = False
        self.python = fixture.PYTHON
        self.receipts = _receipts(tmp_path)
        self.plans = SetupPlanStore()
        self.serving = SetupServing(
            SetupPostureDecision(True, "ready"), self.receipts, fixture.profile(), self.plans
        )

    def inspection(self, statuses: dict[str, str], hub_pid: int | None) -> dict[str, object]:
        return fixture.inspection(
            statuses, hub_pid=hub_pid, systemctl=str(self.systemctl), python=self.python
        )

    def inspect(self, _profile: SetupProfile) -> dict[str, object]:
        """The dashboard's inspection: the host as planned, and all well once the executor ran."""
        if self.applied and self.unreadable_after:
            raise RuntimeError("the host could not be read after the apply")
        if self.applied:
            return self.inspection(self.after_statuses, None)
        return self.inspection(self.statuses, self.hub_pid)

    async def executor_inspect(self, *_args: object, **_kwargs: object) -> dict[str, object]:
        """The executor's own fresh inspection, just before its first effect."""
        self.applied = True
        statuses = self.statuses if self.executor_statuses is None else self.executor_statuses
        hub_pid = self.hub_pid if self.executor_hub_pid is None else self.executor_hub_pid
        return self.inspection(statuses, hub_pid)

    def run_hub(self, pid: int) -> None:
        """Make the fake service manager hold the hub unit running, enabled, under ``pid``."""
        state = {"units": {"synapse-hub.service": {"active": True, "enabled": True, "pid": pid}}}
        (self.systemctl.parent / "manager-state.json").write_text(json.dumps(state), "utf-8")

    def stop_units(self) -> None:
        """Stop every process the fake service manager started for a unit."""
        _stop_manager_processes(self.systemctl)

    def chain(self) -> tuple[SetupReceipt, ...]:
        with SetupReceiptStore(self.receipts) as chain:
            return chain.read_all()


def stop_every_host() -> None:
    """Stop the unit processes of every host made since the last call."""
    while MADE:
        MADE.pop().stop_units()
