# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dashboard snapshot identity serialization tests
"""Pin cross-thread exclusion and exception-safe release for snapshot fetches."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from synapse_channel.dashboard_snapshot_gate import DashboardSnapshotGate


def test_snapshot_gate_serializes_overlapping_callers() -> None:
    """A second HTTP thread cannot enter while the shared identity is live."""
    first_entered = threading.Event()
    second_attempted = threading.Event()
    release_first = threading.Event()
    state_lock = threading.Lock()
    calls = 0
    active = 0
    peak_active = 0

    def fetcher() -> int:
        nonlocal active, calls, peak_active
        with state_lock:
            calls += 1
            call = calls
            active += 1
            peak_active = max(peak_active, active)
        if call == 1:
            first_entered.set()
            assert release_first.wait(timeout=1.0)
        with state_lock:
            active -= 1
        return call

    gate = DashboardSnapshotGate(fetcher)

    def second_fetch() -> int:
        second_attempted.set()
        return gate.fetch()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(gate.fetch)
        assert first_entered.wait(timeout=1.0)
        second = pool.submit(second_fetch)
        assert second_attempted.wait(timeout=1.0)
        with state_lock:
            assert calls == 1
        release_first.set()
        assert first.result(timeout=1.0) == 1
        assert second.result(timeout=1.0) == 2

    assert peak_active == 1


def test_snapshot_gate_releases_identity_after_fetch_failure() -> None:
    """An exception cannot strand the gate or suppress the next caller."""
    calls = 0

    def fetcher() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("snapshot failed")
        return "recovered"

    gate = DashboardSnapshotGate(fetcher)

    with pytest.raises(RuntimeError, match="snapshot failed"):
        gate.fetch()
    assert gate.fetch() == "recovered"
