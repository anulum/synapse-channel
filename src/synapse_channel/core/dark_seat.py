# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — hub-side dark-seat monitoring for owned live work
"""Detect identities that own live work without a fresh wake waiter."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from synapse_channel.core.ledger import TERMINAL_LEDGER_STATUSES, LedgerTask
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.state_models import TaskClaim
from synapse_channel.core.terminal_text import shell_long_option

DEFAULT_DARK_SEAT_GRACE_SECONDS = 30.0
"""Continuous missing-waiter time allowed before the hub broadcasts an alert."""

DEFAULT_DARK_SEAT_POLL_SECONDS = 5.0
"""Seconds between dark-seat evaluations while the hub server is running."""

MIN_DARK_SEAT_POLL_SECONDS = 0.01
"""Lower poll bound preventing an accidentally configured busy loop."""

ClaimSource = Callable[[], Mapping[str, TaskClaim]]
TaskSource = Callable[[], Mapping[str, LedgerTask]]
WaiterProbe = Callable[[str], bool]
Broadcaster = Callable[[dict[str, Any]], Awaitable[None]]
SystemFactory = Callable[..., dict[str, Any]]
Clock = Callable[[], float]


@dataclass(frozen=True)
class OwnedWork:
    """Sorted live work ids held by one exact identity."""

    identity: str
    claims: tuple[str, ...] = ()
    tasks: tuple[str, ...] = ()


def owned_live_work(
    claims: Mapping[str, TaskClaim],
    tasks: Mapping[str, LedgerTask],
    *,
    wall_now: float,
) -> tuple[OwnedWork, ...]:
    """Return live claim and assigned-board work grouped by exact identity.

    Expired claims are not work. A board task is attributed only through its
    non-empty ``suggested_owner`` field; ``created_by`` remains provenance, not
    ownership. Terminal tasks likewise require no live waiter.
    """
    claims_by_owner: dict[str, set[str]] = {}
    for claim in claims.values():
        owner = claim.owner.strip()
        if owner and claim.lease_expires_at > wall_now:
            claims_by_owner.setdefault(owner, set()).add(claim.task_id)

    tasks_by_owner: dict[str, set[str]] = {}
    for task in tasks.values():
        owner = task.suggested_owner.strip()
        if owner and task.status not in TERMINAL_LEDGER_STATUSES:
            tasks_by_owner.setdefault(owner, set()).add(task.task_id)

    identities = sorted(claims_by_owner.keys() | tasks_by_owner.keys())
    return tuple(
        OwnedWork(
            identity=identity,
            claims=tuple(sorted(claims_by_owner.get(identity, ()))),
            tasks=tuple(sorted(tasks_by_owner.get(identity, ()))),
        )
        for identity in identities
    )


class DarkSeatMonitor:
    """Broadcast one alert per continuous missing-waiter work episode."""

    def __init__(
        self,
        *,
        claims: ClaimSource,
        tasks: TaskSource,
        has_live_waiter: WaiterProbe,
        broadcast: Broadcaster,
        system: SystemFactory,
        grace_seconds: float = DEFAULT_DARK_SEAT_GRACE_SECONDS,
        poll_seconds: float = DEFAULT_DARK_SEAT_POLL_SECONDS,
        clock: Clock = time.monotonic,
        wall_clock: Clock = time.time,
    ) -> None:
        self._claims = claims
        self._tasks = tasks
        self._has_live_waiter = has_live_waiter
        self._broadcast = broadcast
        self._system = system
        self._grace_seconds = max(float(grace_seconds), 0.0)
        self._poll_seconds = max(float(poll_seconds), MIN_DARK_SEAT_POLL_SECONDS)
        self._clock = clock
        self._wall_clock = wall_clock
        self._missing_since: dict[str, float] = {}
        self._alerted: set[str] = set()

    async def check(self) -> tuple[str, ...]:
        """Evaluate current work and broadcast newly mature dark-seat episodes."""
        now = self._clock()
        work = owned_live_work(self._claims(), self._tasks(), wall_now=self._wall_clock())
        dark = {item.identity: item for item in work if not self._has_live_waiter(item.identity)}

        active_dark = set(dark)
        self._missing_since = {
            identity: since
            for identity, since in self._missing_since.items()
            if identity in active_dark
        }
        self._alerted.intersection_update(active_dark)

        emitted: list[str] = []
        for identity in sorted(dark):
            item = dark[identity]
            since = self._missing_since.setdefault(identity, now)
            if now < since:
                since = now
                self._missing_since[identity] = now
            missing_for = max(0.0, now - since)
            if identity in self._alerted or missing_for < self._grace_seconds:
                continue
            await self._broadcast(
                self._system(
                    f"Dark seat: {identity} owns live work without a fresh -rx waiter.",
                    msg_type=MessageType.DARK_SEAT_ALERT,
                    target="all",
                    identity=identity,
                    claims=list(item.claims),
                    tasks=list(item.tasks),
                    missing_for_seconds=round(missing_for, 3),
                    remedy=(
                        "arm a permanent waiter: synapse arm "
                        f"{shell_long_option('--name', identity)} "
                        f"{shell_long_option('--for', identity)} --directed-only"
                    ),
                )
            )
            self._alerted.add(identity)
            emitted.append(identity)
        return tuple(emitted)

    async def run(self, stop: asyncio.Event) -> None:
        """Check until ``stop`` is set, sleeping interruptibly between scans."""
        while not stop.is_set():
            await self.check()
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._poll_seconds)
            except asyncio.TimeoutError:
                continue

    @asynccontextmanager
    async def running(self) -> AsyncIterator[None]:
        """Run the monitor for the lifetime of an enclosing hub server context."""
        stop = asyncio.Event()
        task = asyncio.create_task(self.run(stop), name="synapse-dark-seat-monitor")
        try:
            yield
        finally:
            stop.set()
            await task
