# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — direct hub dark-seat monitor contract tests
"""Exercise work discovery, waiter grace, episode dedupe, and monitor lifetime."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from synapse_channel.core.dark_seat import DarkSeatMonitor, OwnedWork, owned_live_work
from synapse_channel.core.ledger import LedgerTask
from synapse_channel.core.protocol import MessageType, system_message
from synapse_channel.core.state_models import TaskClaim


@dataclass
class _Clock:
    """Mutable deterministic clock shared by monitor checks."""

    now: float

    def __call__(self) -> float:
        return self.now


def _claim(task_id: str, owner: str, *, expires_at: float) -> TaskClaim:
    """Build one claim with a controlled wall-clock expiry."""
    return TaskClaim(
        task_id=task_id,
        owner=owner,
        note="",
        claimed_at=0.0,
        lease_expires_at=expires_at,
    )


def _task(
    task_id: str,
    *,
    owner: str,
    status: str = "open",
    created_by: str = "PLANNER",
) -> LedgerTask:
    """Build one board task with explicit assignment and status."""
    return LedgerTask(
        task_id=task_id,
        title=task_id,
        created_at=0.0,
        updated_at=0.0,
        status=status,
        suggested_owner=owner,
        created_by=created_by,
    )


def _system(payload: str, **extra: Any) -> dict[str, Any]:
    """Build deterministic hub frames for monitor assertions."""
    return system_message(payload, hub_id="syn-dark-test", now=0.0, **extra)


def test_owned_live_work_filters_and_groups_only_current_responsibility() -> None:
    """Expired claims, terminal tasks, blank owners, and creators are not ownership."""
    claims = {
        "LIVE-B": _claim("LIVE-B", " BRAVO ", expires_at=101.0),
        "EXPIRED-A": _claim("EXPIRED-A", "ALPHA", expires_at=100.0),
        "NO-OWNER": _claim("NO-OWNER", " ", expires_at=101.0),
    }
    tasks = {
        "OPEN-A": _task("OPEN-A", owner=" ALPHA ", created_by="BRAVO"),
        "BLOCKED-B": _task("BLOCKED-B", owner="BRAVO", status="blocked"),
        "DONE-A": _task("DONE-A", owner="ALPHA", status="done"),
        "CANCELLED-C": _task("CANCELLED-C", owner="CHARLIE", status="cancelled"),
        "UNASSIGNED": _task("UNASSIGNED", owner="", created_by="ALPHA"),
    }

    assert owned_live_work(claims, tasks, wall_now=100.0) == (
        OwnedWork(identity="ALPHA", tasks=("OPEN-A",)),
        OwnedWork(identity="BRAVO", claims=("LIVE-B",), tasks=("BLOCKED-B",)),
    )


async def test_monitor_waits_for_grace_deduplicates_and_rearms_after_recovery() -> None:
    """One continuous dark episode alerts once; waiter recovery permits a later alert."""
    clock = _Clock(0.0)
    wall_clock = _Clock(100.0)
    claims = {"WORK": _claim("WORK", "ALPHA", expires_at=1000.0)}
    waiters: set[str] = set()
    broadcasts: list[dict[str, Any]] = []
    monitor = DarkSeatMonitor(
        claims=lambda: claims,
        tasks=lambda: {},
        has_live_waiter=lambda identity: identity in waiters,
        broadcast=_append_async(broadcasts),
        system=_system,
        grace_seconds=10.0,
        clock=clock,
        wall_clock=wall_clock,
    )

    assert await monitor.check() == ()
    clock.now = 9.0
    assert await monitor.check() == ()
    clock.now = 10.0
    assert await monitor.check() == ("ALPHA",)
    clock.now = 20.0
    assert await monitor.check() == ()

    first = broadcasts[0]
    assert first["type"] == MessageType.DARK_SEAT_ALERT
    assert first["target"] == "all"
    assert first["identity"] == "ALPHA"
    assert first["claims"] == ["WORK"]
    assert first["tasks"] == []
    assert first["missing_for_seconds"] == 10.0
    assert "--name ALPHA --for ALPHA" in first["remedy"]

    waiters.add("ALPHA")
    assert await monitor.check() == ()
    waiters.clear()
    clock.now = 21.0
    assert await monitor.check() == ()
    clock.now = 31.0
    assert await monitor.check() == ("ALPHA",)
    assert len(broadcasts) == 2


async def test_monitor_orders_alerts_and_handles_a_backwards_grace_clock() -> None:
    """Alerts are identity-sorted and a backwards clock safely restarts the grace."""
    clock = _Clock(10.0)
    broadcasts: list[dict[str, Any]] = []
    tasks = {
        "Z": _task("Z", owner="ZETA", status="in_progress"),
        "A": _task("A", owner="ALPHA", status="in_progress"),
    }
    monitor = DarkSeatMonitor(
        claims=lambda: {},
        tasks=lambda: tasks,
        has_live_waiter=lambda _identity: False,
        broadcast=_append_async(broadcasts),
        system=_system,
        grace_seconds=1.0,
        clock=clock,
        wall_clock=lambda: 100.0,
    )

    assert await monitor.check() == ()
    clock.now = 5.0
    assert await monitor.check() == ()
    clock.now = 6.0
    assert await monitor.check() == ("ALPHA", "ZETA")
    assert [frame["identity"] for frame in broadcasts] == ["ALPHA", "ZETA"]
    assert broadcasts[0]["tasks"] == ["A"]
    assert broadcasts[1]["tasks"] == ["Z"]


async def test_running_context_checks_periodically_and_stops_cleanly() -> None:
    """The monitor lifecycle starts with the hub context and exits without a stray task."""
    broadcasts: list[dict[str, Any]] = []
    claims = {"T": _claim("T", "ALPHA", expires_at=1000.0)}
    monitor = DarkSeatMonitor(
        claims=lambda: claims,
        tasks=lambda: {},
        has_live_waiter=lambda _identity: False,
        broadcast=_append_async(broadcasts),
        system=_system,
        grace_seconds=0.0,
        poll_seconds=0.001,
        wall_clock=lambda: 100.0,
    )

    async with monitor.running():
        await _wait_for_count(broadcasts, 1)
        await asyncio.sleep(0.03)
        assert len(broadcasts) == 1

    claims.clear()
    claims["U"] = _claim("U", "BRAVO", expires_at=1000.0)
    await asyncio.sleep(0.03)
    assert len(broadcasts) == 1


def _append_async(
    target: list[dict[str, Any]],
) -> Callable[[dict[str, Any]], Awaitable[None]]:
    """Return an async broadcaster that appends frames to ``target``."""

    async def append(frame: dict[str, Any]) -> None:
        target.append(frame)

    return append


async def _wait_for_count(target: list[dict[str, Any]], count: int) -> None:
    """Wait briefly for an async monitor loop to append ``count`` frames."""
    for _ in range(100):
        if len(target) >= count:
            return
        await asyncio.sleep(0.005)
    raise TimeoutError(f"expected {count} frame(s), got {len(target)}")
