# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded host coordination through its public monitor
"""Exercise bound coordination limits and degraded source reporting."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from synapse_channel.dashboard import DashboardSnapshot, DashboardUnavailable
from synapse_channel.dashboard_handler_binding import (
    DashboardSnapshotSource,
    bind_host_monitor,
    bind_snapshot_gate,
)


@pytest.mark.parametrize(
    ("agents", "claims", "status"),
    [
        (["worker"] * 1025, [], "unavailable"),
        ([], [{}] * 1025, "unavailable"),
        ([], "malformed", "complete"),
        (
            ["worker"],
            [
                None,
                {"owner": "worker", "task_id": "expired", "lease_expires_at": 0},
                {"owner": 7, "task_id": "wrong-owner", "lease_expires_at": 1e20},
                {"owner": "worker", "task_id": "valid", "lease_expires_at": 1e20},
            ],
            "complete",
        ),
    ],
)
def test_bound_host_monitor_reports_bounded_coordination(
    agents: list[str], claims: object, status: str, tmp_path: Path
) -> None:
    """Oversized observations degrade; malformed claims cannot break monitoring."""
    snapshot = DashboardSnapshot(
        online_agents=agents, state={"active_claims": claims}, board={}, manifest=[]
    )
    calls: list[dict[str, object]] = []

    async def fetch(**kwargs: object) -> DashboardSnapshot:
        """Supply one actual snapshot through the public async owner contract."""
        calls.append(kwargs)
        return snapshot

    source = DashboardSnapshotSource(
        uri="ws://127.0.0.1:1",
        name="BINDING/observer",
        token=None,
        ready_timeout=1.0,
        response_timeout=1.0,
        observed_peers=(),
        observed_token=None,
        observed_timeout=1.0,
        observed_pins=None,
    )
    gate = bind_snapshot_gate(source, lambda: fetch)
    monitor = bind_host_monitor(source, gate, lambda: fetch, DashboardUnavailable)
    monitor.pids = (os.getpid(),)
    monitor.tmux_socket = str(tmp_path / "absent.sock")
    before = time.time()
    observation = monitor.snapshot()
    assert observation.coordination_status == status
    assert calls == [
        {
            "uri": source.uri,
            "name": source.name,
            "token": None,
            "ready_timeout": 0.5,
            "response_timeout": 0.5,
        }
    ]
    if status == "complete":
        assert observation.coordination_observed_at is not None
        assert observation.coordination_observed_at >= before
    else:
        assert observation.coordination_observed_at is None
    assert observation.to_json()
