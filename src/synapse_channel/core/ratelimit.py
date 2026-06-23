# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — per-agent token-bucket rate limiting for the hub
"""Per-agent token-bucket rate limiting.

The hub is a single process and a single source of truth; one runaway agent
spinning in a tight chat or claim loop can swamp it. A token bucket per agent
caps the sustained rate while still allowing a short burst, at the natural
choke point where every message already passes.

Each agent has a bucket that refills at ``rate_per_second`` tokens and holds at
most ``burst`` tokens; a message costs one token. When the bucket is empty the
message is refused. Buckets are dropped with :meth:`RateLimiter.forget` when an
agent disconnects, so the limiter's memory is bounded by the number of connected
agents. The limiter is deterministic — pass ``now`` to test it without sleeping.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

MINIMUM_RATE = 0.01
MINIMUM_BURST = 1.0


@dataclass
class _Bucket:
    """Mutable token-bucket state for one agent."""

    tokens: float
    updated_at: float


class RateLimiter:
    """Token-bucket limiter keyed by agent name.

    Parameters
    ----------
    rate_per_second : float
        Sustained refill rate in tokens per second, clamped up to
        :data:`MINIMUM_RATE`.
    burst : float
        Bucket capacity (maximum burst), clamped up to :data:`MINIMUM_BURST`.
    """

    def __init__(self, *, rate_per_second: float, burst: float) -> None:
        self.rate_per_second = max(float(rate_per_second), MINIMUM_RATE)
        self.burst = max(float(burst), MINIMUM_BURST)
        self._buckets: dict[str, _Bucket] = {}

    def allow(self, agent: str, now: float | None = None, cost: float = 1.0) -> bool:
        """Refill the agent's bucket and consume ``cost`` tokens if available.

        Parameters
        ----------
        agent : str
            Name of the agent whose bucket is charged.
        now : float or None, optional
            Override for the current wall-clock time, in seconds.
        cost : float, optional
            Tokens this action costs. Defaults to ``1.0``.

        Returns
        -------
        bool
            ``True`` if the bucket had enough tokens (which are then consumed),
            ``False`` when the agent is over its limit.
        """
        ts = time.time() if now is None else float(now)
        bucket = self._buckets.get(agent)
        if bucket is None:
            bucket = _Bucket(tokens=self.burst, updated_at=ts)
            self._buckets[agent] = bucket
        else:
            elapsed = max(0.0, ts - bucket.updated_at)
            bucket.tokens = min(self.burst, bucket.tokens + elapsed * self.rate_per_second)
            bucket.updated_at = ts

        if bucket.tokens >= cost:
            bucket.tokens -= cost
            return True
        return False

    def forget(self, agent: str) -> None:
        """Drop an agent's bucket, e.g. when it disconnects."""
        self._buckets.pop(agent, None)
