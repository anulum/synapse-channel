# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — lease-expiry index for coordination state
"""Lease-expiry heap owned by the coordination state."""

from __future__ import annotations

import heapq
from collections.abc import Iterator, Mapping

from synapse_channel.core.state_models import TaskClaim

LEASE_HEAP_COMPACT_FLOOR = 16
"""Slack before the lease heap is rebuilt to shed superseded entries."""

LeaseEntry = tuple[float, str, int]


class LeaseIndex:
    """Maintain the min-heap used to expire task leases."""

    def __init__(self, entries: list[LeaseEntry] | None = None) -> None:
        self.entries: list[LeaseEntry] = [] if entries is None else entries
        heapq.heapify(self.entries)

    def track(self, claim: TaskClaim, claims: Mapping[str, TaskClaim]) -> None:
        """Index a claim lease and compact the heap when renewal churn grows it."""
        heapq.heappush(self.entries, (claim.lease_expires_at, claim.task_id, claim.epoch))
        if len(self.entries) > 2 * len(claims) + LEASE_HEAP_COMPACT_FLOOR:
            self.rebuild(claims)

    def rebuild(self, claims: Mapping[str, TaskClaim]) -> None:
        """Rebuild the heap from the currently live claims."""
        self.entries = [
            (claim.lease_expires_at, task, claim.epoch) for task, claim in claims.items()
        ]
        heapq.heapify(self.entries)

    def pop_due(self, now: float) -> Iterator[tuple[str, int]]:
        """Yield due ``(task_id, epoch)`` entries, including stale heap entries."""
        while self.entries and self.entries[0][0] <= now:
            _expires_at, task, epoch = heapq.heappop(self.entries)
            yield task, epoch
