# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the order of wake adapters by measured time
"""Samples on a real file system, also written by several threads at once."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.wake_marks import WakeMarkError, seat_key
from synapse_channel.wake_ranking import KEEP, medians, order, record, samples

SEAT = "PROJECT/seat-aaaa"
CONFIGURED = ("native_queue", "pane_bridge", "harness_waiter")


def _fill(directory: Path, adapter: str, *seconds: float, seat: str = SEAT) -> None:
    for value in seconds:
        record(directory, seat, adapter, value)


def test_the_configuration_decides_until_an_adapter_has_enough_samples(tmp_path: Path) -> None:
    assert order(tmp_path, SEAT, CONFIGURED) == CONFIGURED and samples(tmp_path, SEAT) == {}
    _fill(tmp_path, "pane_bridge", 0.2, 0.2, 0.2, 0.2)
    _fill(tmp_path, "harness_waiter", 0.1, 0.1, 0.1, 0.1, 0.1)

    # Four samples do not count yet; one measured adapter alone has nobody to pass.
    assert order(tmp_path, SEAT, CONFIGURED) == CONFIGURED
    assert medians(tmp_path, SEAT) == {"harness_waiter": 0.1}
    assert order(tmp_path, SEAT, CONFIGURED, min_samples=4) == (
        "native_queue",
        "harness_waiter",
        "pane_bridge",
    )


def test_measured_adapters_are_sorted_among_themselves_and_the_unmeasured_keep_their_place(
    tmp_path: Path,
) -> None:
    _fill(tmp_path, "pane_bridge", 3.0, 1.0, 2.0, 9.0, 2.0)
    _fill(tmp_path, "harness_waiter", 0.5, 0.4, 0.6, 0.5, 0.5)
    _fill(tmp_path, "native_queue", 5.0, seat="PROJECT/other")

    assert medians(tmp_path, SEAT) == {"pane_bridge": 2.0, "harness_waiter": 0.5}
    assert order(tmp_path, SEAT, CONFIGURED) == ("native_queue", "harness_waiter", "pane_bridge")
    assert order(tmp_path, "PROJECT/other", CONFIGURED) == CONFIGURED

    _fill(tmp_path, "native_queue", 0.1, 0.1, 0.1, 0.1, 0.1)
    assert order(tmp_path, SEAT, CONFIGURED) == ("native_queue", "harness_waiter", "pane_bridge")
    assert order(tmp_path, SEAT, ("pane_bridge", "native_queue")) == ("native_queue", "pane_bridge")
    # A tie keeps the configured order; an adapter that is not configured is not added.
    _fill(tmp_path, "pane_bridge", 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5)
    assert medians(tmp_path, SEAT)["pane_bridge"] == 0.5
    assert order(tmp_path, SEAT, ("pane_bridge", "harness_waiter")) == (
        "pane_bridge",
        "harness_waiter",
    )
    assert order(tmp_path, SEAT, ("harness_waiter", "pane_bridge")) == (
        "harness_waiter",
        "pane_bridge",
    )


def test_only_the_newest_samples_are_kept_and_min_samples_is_at_least_one(tmp_path: Path) -> None:
    _fill(tmp_path, "pane_bridge", *([9.0] * 10), *([1.0] * KEEP))

    assert samples(tmp_path, SEAT) == {"pane_bridge": (1.0,) * KEEP}
    assert medians(tmp_path, SEAT, min_samples=0) == {"pane_bridge": 1.0}


def test_samples_recorded_at_once_are_all_kept(tmp_path: Path) -> None:
    start = threading.Barrier(8)

    def one(number: int) -> None:
        start.wait()
        for step in range(5):
            record(tmp_path, SEAT, "pane_bridge" if number % 2 else "native_queue", number + step)

    threads = [threading.Thread(target=one, args=(number,)) for number in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    found = samples(tmp_path, SEAT)
    assert {name: len(values) for name, values in found.items()} == {
        "native_queue": 20,
        "pane_bridge": 20,
    }
    assert sorted(p.name for p in (tmp_path / seat_key(SEAT)).iterdir()) == [
        ".lock",
        "ranking.json",
    ]


def test_values_that_are_not_measurements_are_refused_and_a_damaged_file_counts_as_empty(
    tmp_path: Path,
) -> None:
    bad: list[Any] = [-0.1, float("nan"), float("inf"), True, "1", None]
    for value in bad:
        with pytest.raises(WakeMarkError, match="measured time"):
            record(tmp_path, SEAT, "pane_bridge", value)
    with pytest.raises(WakeMarkError, match="adapter kind"):
        record(tmp_path, SEAT, "Pane Bridge", 1.0)
    assert samples(tmp_path, SEAT) == {}

    record(tmp_path, SEAT, "pane_bridge", 2)
    path = tmp_path / seat_key(SEAT) / "ranking.json"
    path.write_text(
        '{"pane_bridge": [1, "x", true, -1, 2.5], "Bad Name": [1], "native_queue": 3, '
        '"harness_waiter": ["only text"]}',
        encoding="utf-8",
    )
    assert samples(tmp_path, SEAT) == {"pane_bridge": (1.0, 2.5)}
    for content in ("not json", "[1, 2]"):
        path.write_text(content, encoding="utf-8")
        assert samples(tmp_path, SEAT) == {} and order(tmp_path, SEAT, CONFIGURED) == CONFIGURED
    # Recording over a damaged file starts again from what can be read.
    record(tmp_path, SEAT, "pane_bridge", 4.0)
    assert samples(tmp_path, SEAT) == {"pane_bridge": (4.0,)}
