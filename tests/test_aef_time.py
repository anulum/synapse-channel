# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Agent Evidence Format integer timestamp regressions

from __future__ import annotations

import time
from typing import cast

import pytest

from synapse_channel.core.aef_canonical import IJSON_MAX_INTEGER, IJSON_MIN_INTEGER
from synapse_channel.core.aef_time import (
    AefTimestampError,
    current_epoch_ms,
    epoch_ms_to_legacy_seconds,
    legacy_seconds_to_epoch_ms,
)


@pytest.mark.parametrize(
    ("seconds", "expected_ms"),
    [
        (0, 0),
        (1, 1000),
        (0.1, 100),
        (0.29, 290),
        (1.234, 1234),
        (1.234999, 1234),
        (-0.0, 0),
        (-0.0001, -1),
        (-1.234001, -1235),
    ],
)
def test_legacy_seconds_project_to_the_containing_millisecond(
    seconds: int | float, expected_ms: int
) -> None:
    assert legacy_seconds_to_epoch_ms(seconds) == expected_ms


def test_integer_seconds_at_the_i_json_boundary_are_checked_after_scaling() -> None:
    largest_whole_seconds = IJSON_MAX_INTEGER // 1000
    smallest_whole_seconds = -((-IJSON_MIN_INTEGER) // 1000)

    assert legacy_seconds_to_epoch_ms(largest_whole_seconds) == largest_whole_seconds * 1000
    assert legacy_seconds_to_epoch_ms(smallest_whole_seconds) == smallest_whole_seconds * 1000
    with pytest.raises(AefTimestampError, match="I-JSON exact integer range"):
        legacy_seconds_to_epoch_ms(largest_whole_seconds + 1)
    with pytest.raises(AefTimestampError, match="I-JSON exact integer range"):
        legacy_seconds_to_epoch_ms(smallest_whole_seconds - 1)


@pytest.mark.parametrize("seconds", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_legacy_timestamps_are_rejected(seconds: float) -> None:
    with pytest.raises(AefTimestampError, match="finite"):
        legacy_seconds_to_epoch_ms(seconds)


@pytest.mark.parametrize("seconds", [True, False, "1.25", None])
def test_non_numeric_legacy_timestamps_are_rejected(seconds: object) -> None:
    with pytest.raises(AefTimestampError, match="integer or float"):
        legacy_seconds_to_epoch_ms(cast("int | float", seconds))


def test_huge_integer_is_rejected_without_decimal_string_conversion() -> None:
    with pytest.raises(AefTimestampError, match="I-JSON exact integer range"):
        legacy_seconds_to_epoch_ms(10**5000)


@pytest.mark.parametrize(
    "timestamp_ms",
    [IJSON_MIN_INTEGER - 1, IJSON_MAX_INTEGER + 1],
)
def test_epoch_millisecond_range_is_enforced(timestamp_ms: int) -> None:
    with pytest.raises(AefTimestampError, match="I-JSON exact integer range"):
        epoch_ms_to_legacy_seconds(timestamp_ms)


@pytest.mark.parametrize("timestamp_ms", [True, 1.5, "1000", None])
def test_legacy_projection_requires_an_integer_millisecond(timestamp_ms: object) -> None:
    with pytest.raises(AefTimestampError, match="integer number of milliseconds"):
        epoch_ms_to_legacy_seconds(cast(int, timestamp_ms))


@pytest.mark.parametrize("timestamp_ms", [-1_234_567, -1, 0, 1, 1_234_567])
def test_normal_range_milliseconds_roundtrip_through_legacy_float(timestamp_ms: int) -> None:
    seconds = epoch_ms_to_legacy_seconds(timestamp_ms)

    assert legacy_seconds_to_epoch_ms(seconds) == timestamp_ms


def test_current_epoch_ms_uses_nanoseconds_without_float_conversion() -> None:
    assert current_epoch_ms(clock_ns=lambda: 1_234_999_999) == 1234


def test_current_epoch_ms_defaults_to_the_host_nanosecond_clock() -> None:
    before = time.time_ns() // 1_000_000
    observed = current_epoch_ms()
    after = time.time_ns() // 1_000_000

    assert before <= observed <= after


def test_current_epoch_ms_rejects_an_out_of_range_clock() -> None:
    with pytest.raises(AefTimestampError, match="I-JSON exact integer range"):
        current_epoch_ms(clock_ns=lambda: (IJSON_MAX_INTEGER + 1) * 1_000_000)
