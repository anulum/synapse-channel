# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the per-agent token-bucket rate limiter

from __future__ import annotations

from synapse_channel.core.ratelimit import MINIMUM_BURST, MINIMUM_RATE, RateLimiter


def test_rate_and_burst_are_clamped() -> None:
    limiter = RateLimiter(rate_per_second=0.0, burst=0.0)
    assert limiter.rate_per_second == MINIMUM_RATE
    assert limiter.burst == MINIMUM_BURST


def test_first_calls_use_the_burst_allowance() -> None:
    limiter = RateLimiter(rate_per_second=1.0, burst=3.0)
    assert limiter.allow("A", now=1000.0) is True
    assert limiter.allow("A", now=1000.0) is True
    assert limiter.allow("A", now=1000.0) is True
    # Burst exhausted at the same instant.
    assert limiter.allow("A", now=1000.0) is False


def test_bucket_refills_over_time() -> None:
    limiter = RateLimiter(rate_per_second=2.0, burst=2.0)
    assert limiter.allow("A", now=1000.0) is True
    assert limiter.allow("A", now=1000.0) is True
    assert limiter.allow("A", now=1000.0) is False
    # One second later, two tokens have refilled.
    assert limiter.allow("A", now=1001.0) is True
    assert limiter.allow("A", now=1001.0) is True
    assert limiter.allow("A", now=1001.0) is False


def test_refill_is_capped_at_burst() -> None:
    limiter = RateLimiter(rate_per_second=5.0, burst=2.0)
    limiter.allow("A", now=1000.0)  # creates bucket, consumes 1 (1 left)
    # A long idle gap must not let tokens exceed the burst cap.
    assert limiter.allow("A", now=2000.0) is True
    assert limiter.allow("A", now=2000.0) is True
    assert limiter.allow("A", now=2000.0) is False


def test_limits_are_per_agent() -> None:
    limiter = RateLimiter(rate_per_second=1.0, burst=1.0)
    assert limiter.allow("A", now=1000.0) is True
    assert limiter.allow("A", now=1000.0) is False
    # A different agent has its own bucket.
    assert limiter.allow("B", now=1000.0) is True


def test_forget_resets_an_agent_bucket() -> None:
    limiter = RateLimiter(rate_per_second=1.0, burst=1.0)
    assert limiter.allow("A", now=1000.0) is True
    assert limiter.allow("A", now=1000.0) is False
    limiter.forget("A")
    # A fresh bucket starts full again.
    assert limiter.allow("A", now=1000.0) is True


def test_cost_consumes_multiple_tokens() -> None:
    limiter = RateLimiter(rate_per_second=1.0, burst=5.0)
    assert limiter.allow("A", now=1000.0, cost=3.0) is True
    assert limiter.allow("A", now=1000.0, cost=3.0) is False  # only 2 left


def test_uses_wall_clock_when_now_is_none() -> None:
    limiter = RateLimiter(rate_per_second=1.0, burst=1.0)
    assert limiter.allow("A") is True
    assert limiter.allow("A") is False
