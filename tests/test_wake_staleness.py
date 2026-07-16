# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — wake staleness marker regressions (DEL-INT-B)

from __future__ import annotations

import math
import time

from synapse_channel.wake_staleness import (
    STALE_AFTER_SECONDS,
    format_age,
    message_age_seconds,
    stale_marker,
)


class TestMessageAge:
    def test_age_is_now_minus_the_send_timestamp(self) -> None:
        assert message_age_seconds({"timestamp": 1000.0}, now=1090.0) == 90.0

    def test_future_stamped_clock_skew_reads_as_fresh_not_negative(self) -> None:
        assert message_age_seconds({"timestamp": 2000.0}, now=1990.0) == 0.0

    def test_missing_timestamp_is_unknowable(self) -> None:
        assert message_age_seconds({}, now=1000.0) is None

    def test_boolean_timestamp_is_unknowable(self) -> None:
        assert message_age_seconds({"timestamp": True}, now=1000.0) is None

    def test_non_numeric_timestamp_is_unknowable(self) -> None:
        assert message_age_seconds({"timestamp": "yesterday"}, now=1000.0) is None

    def test_non_finite_timestamp_is_unknowable(self) -> None:
        assert message_age_seconds({"timestamp": math.inf}, now=1000.0) is None

    def test_non_positive_timestamp_is_unknowable(self) -> None:
        assert message_age_seconds({"timestamp": 0}, now=1000.0) is None

    def test_default_clock_is_the_wall_clock(self) -> None:
        age = message_age_seconds({"timestamp": time.time() - 5.0})
        assert age is not None
        assert 0.0 <= age < 60.0


class TestFormatAge:
    def test_units_scale_from_seconds_to_days(self) -> None:
        assert format_age(45.0) == "45s"
        assert format_age(720.0) == "12m"
        assert format_age(5.0 * 3600) == "5h"
        assert format_age(33.0 * 86400) == "33d"

    def test_negative_input_clamps_to_zero(self) -> None:
        assert format_age(-3.0) == "0s"


class TestStaleMarker:
    def test_a_fresh_message_carries_no_marker(self) -> None:
        assert stale_marker(STALE_AFTER_SECONDS - 1.0) == ""

    def test_an_unknown_age_is_not_evidence_of_staleness(self) -> None:
        assert stale_marker(None) == ""

    def test_a_stale_message_carries_an_unambiguous_replay_marker(self) -> None:
        assert stale_marker(90.0 * 86400) == "[replayed 90d ago] "

    def test_the_horizon_boundary_is_stale(self) -> None:
        assert stale_marker(STALE_AFTER_SECONDS) == "[replayed 15m ago] "

    def test_a_caller_can_tighten_the_horizon(self) -> None:
        assert stale_marker(10.0, stale_after=5.0) == "[replayed 10s ago] "
