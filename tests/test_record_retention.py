# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — calendar retention observations on retained logs
"""Exercise month ends, leap years, empty journals and clock anomalies."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from synapse_channel.core.persistence import EventStore
from synapse_channel.core.record_keeping import build_export
from synapse_channel.core.record_retention import (
    assess_retention,
    render_retention,
    retention_to_json,
)


@pytest.mark.parametrize(
    "oldest,anniversary",
    [
        ("2025-08-31T12:30:00+00:00", "2026-02-28T12:30:00+00:00"),
        ("2023-08-30T12:30:00+00:00", "2024-02-29T12:30:00+00:00"),
        ("2024-02-29T12:30:00+00:00", "2024-08-29T12:30:00+00:00"),
        ("2026-01-15T12:30:00+00:00", "2026-07-15T12:30:00+00:00"),
    ],
)
def test_calendar_anniversary_uses_original_day_and_exact_boundary(
    tmp_path: Path,
    oldest: str,
    anniversary: str,
) -> None:
    path = tmp_path / "calendar.db"
    first, boundary = (
        datetime.fromisoformat(oldest).timestamp(),
        datetime.fromisoformat(anniversary).timestamp(),
    )
    store = EventStore(path)
    store.append("claim", {"task_id": "T"}, ts=first + 1)
    store.append("release", {"task_id": "T"}, ts=first)
    store.close()
    export = build_export(path)
    before = assess_retention(export, now=boundary - 0.000001)
    reached = assess_retention(export, now=boundary)
    assert not before.reaches_floor
    assert reached.reaches_floor and reached.anniversary == anniversary
    assert reached.retained_span_days == pytest.approx(1 / 86400)
    assert "6 calendar months" in render_retention(reached)
    assert retention_to_json(reached)["minimum_months"] == 6


def test_empty_window_future_clock_and_calendar_overflow_are_explicit(tmp_path: Path) -> None:
    path = tmp_path / "clock.db"
    store = EventStore(path)
    try:
        empty = assess_retention(build_export(path), now=0)
        assert not empty.reaches_floor and empty.anniversary is None
        assert "empty" in render_retention(empty)
        with pytest.raises(ValueError, match="finite"):
            assess_retention(build_export(path), now=float("nan"))
        store.append("claim", {"task_id": "T"}, ts=0)
        store.append("release", {"task_id": "T"}, ts=30_000_000)
        future = assess_retention(build_export(path), now=20_000_000)
        assert future.future_events and not future.reaches_floor
        assert "after the reference clock" in future.note
    finally:
        store.close()
    overflow = tmp_path / "overflow.db"
    store = EventStore(overflow)
    store.append(
        "claim",
        {"task_id": "T"},
        ts=datetime.fromisoformat("9999-12-01T00:00:00+00:00").timestamp(),
    )
    store.close()
    with pytest.raises(ValueError, match="anniversary"):
        assess_retention(build_export(overflow), now=0)
