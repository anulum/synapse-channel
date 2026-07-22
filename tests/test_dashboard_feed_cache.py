# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — expensive dashboard feed single-flight tests

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus

import pytest

from synapse_channel.dashboard_feed_cache import DashboardFeedCache
from synapse_channel.dashboard_feed_serving import FeedResponse


def _response(body: bytes = b"ok", status: HTTPStatus = HTTPStatus.OK) -> FeedResponse:
    return FeedResponse(status=status, body=body, content_type="application/json")


def test_duplicate_requests_share_one_build_and_cached_value() -> None:
    cache = DashboardFeedCache()
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def build() -> FeedResponse:
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(timeout=2)
        return _response()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(cache.get_or_build, "reliability", build)
        assert started.wait(timeout=2)
        second = pool.submit(cache.get_or_build, "reliability", build)
        release.set()
        assert first.result(timeout=2) == _response()
        assert second.result(timeout=2) == _response()

    assert cache.get_or_build("reliability", build) == _response()
    assert calls == 1


def test_distinct_heavy_reports_use_one_build_slot() -> None:
    cache = DashboardFeedCache()
    active = 0
    peak = 0
    lock = threading.Lock()
    release = threading.Event()

    def build(body: bytes) -> FeedResponse:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        assert release.wait(timeout=2)
        with lock:
            active -= 1
        return _response(body)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(cache.get_or_build, "reliability", lambda: build(b"r"))
        second = pool.submit(cache.get_or_build, "health", lambda: build(b"h"))
        release.set()
        assert {first.result(timeout=2).body, second.result(timeout=2).body} == {b"r", b"h"}

    assert peak == 1


def test_expiry_lru_and_server_errors_are_not_reused() -> None:
    now = 0.0
    cache = DashboardFeedCache(ttl_seconds=1, max_entries=1, clock=lambda: now)
    calls = 0

    def build() -> FeedResponse:
        nonlocal calls
        calls += 1
        return _response(str(calls).encode())

    assert cache.get_or_build("a", build).body == b"1"
    assert cache.get_or_build("b", build).body == b"2"
    assert cache.get_or_build("a", build).body == b"3"
    now = 2.0
    assert cache.get_or_build("a", build).body == b"4"

    failures = 0

    def fail() -> FeedResponse:
        nonlocal failures
        failures += 1
        return _response(b"no", HTTPStatus.SERVICE_UNAVAILABLE)

    assert cache.get_or_build("error", fail).status == HTTPStatus.SERVICE_UNAVAILABLE
    assert cache.get_or_build("error", fail).status == HTTPStatus.SERVICE_UNAVAILABLE
    assert failures == 2


def test_failed_builder_wakes_waiters_and_allows_retry() -> None:
    cache = DashboardFeedCache()
    calls = 0

    def build() -> FeedResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("boom")
        return _response()

    with pytest.raises(RuntimeError, match="boom"):
        cache.get_or_build("report", build)
    assert cache.get_or_build("report", build) == _response()
