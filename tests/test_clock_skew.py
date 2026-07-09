# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — peer clock-skew helper regressions

from __future__ import annotations

import pytest

from synapse_channel.core.clock_skew import (
    clock_skew_warnings,
    finite_timestamp,
    format_clock_skew,
    measure_clock_skew,
    parse_clock_skew_spec,
)


def test_finite_timestamp_rejects_booleans_and_non_finite_values() -> None:
    assert finite_timestamp(True) is None
    assert finite_timestamp("nan") is None
    assert finite_timestamp("inf") is None
    assert finite_timestamp(object()) is None
    assert finite_timestamp("123.5") == 123.5


def test_measure_clock_skew_returns_local_minus_peer_seconds() -> None:
    skew = measure_clock_skew("90.25", observed_at=100.0)

    assert skew is not None
    assert skew.peer_timestamp == 90.25
    assert skew.observed_at == 100.0
    assert skew.seconds == pytest.approx(9.75)
    assert measure_clock_skew(True, observed_at=100.0) is None


def test_parse_clock_skew_spec_requires_hub_and_finite_seconds() -> None:
    assert parse_clock_skew_spec("east=-6.5") == ("east", -6.5)
    with pytest.raises(ValueError, match="HUB=SECONDS"):
        parse_clock_skew_spec("east")
    with pytest.raises(ValueError, match="HUB=SECONDS"):
        parse_clock_skew_spec("east=nan")


def test_clock_skew_warnings_are_thresholded_and_sorted() -> None:
    warnings = clock_skew_warnings({"west": 2.0, "east": -6.0}, threshold=5.0)

    assert [warning.hub_id for warning in warnings] == ["east"]
    assert warnings[0].seconds == -6.0
    assert format_clock_skew(warnings[0].seconds) == "-6.000s"
    with pytest.raises(ValueError, match="threshold"):
        clock_skew_warnings({"east": 1.0}, threshold=float("nan"))
