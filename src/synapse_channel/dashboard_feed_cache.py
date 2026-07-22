# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded single-flight cache for expensive dashboard reports
"""Keep duplicate whole-log dashboard reports from starving interactive reads."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context

from synapse_channel.dashboard_feed_serving import FeedResponse


class DashboardFeedCache:
    """TTL cache with per-key single-flight and one whole-log build slot.

    Reliability and causal-health reports are deterministic read projections but
    expensive on a long-lived journal. Multiple browser tabs must share one
    build, and different heavy reports must not occupy every Python worker at
    once. Server-error responses are deliberately not cached.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = 60.0,
        max_entries: int = 8,
        clock: Callable[[], float] = time.monotonic,
        process_isolation: bool = False,
    ) -> None:
        self._ttl_seconds = max(0.0, float(ttl_seconds))
        self._max_entries = max(1, int(max_entries))
        self._clock = clock
        self._executor = (
            ProcessPoolExecutor(max_workers=1, mp_context=get_context("spawn"))
            if process_isolation
            else None
        )
        self._condition = threading.Condition()
        self._build_slot = threading.Lock()
        self._inflight: set[str] = set()
        self._entries: OrderedDict[str, tuple[float, FeedResponse]] = OrderedDict()

    def get_or_build(self, key: str, build: Callable[[], FeedResponse]) -> FeedResponse:
        """Return a fresh cached response or perform one shared bounded build."""
        while True:
            with self._condition:
                cached = self._entries.get(key)
                if cached is not None and cached[0] > self._clock():
                    self._entries.move_to_end(key)
                    return cached[1]
                if cached is not None:
                    del self._entries[key]
                if key not in self._inflight:
                    self._inflight.add(key)
                    break
                self._condition.wait()

        response: FeedResponse | None = None
        try:
            with self._build_slot:
                response = (
                    build() if self._executor is None else self._executor.submit(build).result()
                )
            return response
        finally:
            with self._condition:
                self._inflight.discard(key)
                if response is not None and int(response.status) < 500:
                    self._entries[key] = (self._clock() + self._ttl_seconds, response)
                    self._entries.move_to_end(key)
                    while len(self._entries) > self._max_entries:
                        self._entries.popitem(last=False)
                self._condition.notify_all()

    def close(self) -> None:
        """Release the optional report worker process."""
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
