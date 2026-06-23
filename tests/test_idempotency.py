# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the bounded idempotency cache

from __future__ import annotations

from synapse_channel.core.idempotency import IdempotencyCache


def test_put_then_get_returns_response() -> None:
    cache = IdempotencyCache()
    cache.put("k1", {"type": "claim_granted", "task_id": "T1"})
    assert "k1" in cache
    assert cache.get("k1") == {"type": "claim_granted", "task_id": "T1"}


def test_get_unknown_key_returns_none() -> None:
    cache = IdempotencyCache()
    assert cache.get("missing") is None
    assert "missing" not in cache


def test_max_keys_is_clamped_to_one() -> None:
    cache = IdempotencyCache(max_keys=0)
    assert cache.max_keys == 1


def test_eviction_drops_least_recently_used() -> None:
    cache = IdempotencyCache(max_keys=2)
    cache.put("a", {"v": 1})
    cache.put("b", {"v": 2})
    # Touch "a" so "b" becomes least-recently-used.
    assert cache.get("a") == {"v": 1}
    cache.put("c", {"v": 3})  # exceeds capacity -> evict "b"
    assert "b" not in cache
    assert "a" in cache
    assert "c" in cache
    assert len(cache) == 2


def test_reput_existing_key_refreshes_recency_and_value() -> None:
    cache = IdempotencyCache(max_keys=2)
    cache.put("a", {"v": 1})
    cache.put("b", {"v": 2})
    cache.put("a", {"v": 11})  # update + move to most-recent
    cache.put("c", {"v": 3})  # evict least-recent, which is now "b"
    assert cache.get("a") == {"v": 11}
    assert "b" not in cache
    assert "c" in cache
