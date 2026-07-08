# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tolerant numeric coercion helpers

from __future__ import annotations

import math

from synapse_channel.core.numeric_coercion import safe_float, safe_int


class TestSafeInt:
    def test_coerces_a_plain_int(self) -> None:
        assert safe_int(42) == 42

    def test_parses_a_numeric_string(self) -> None:
        assert safe_int("42") == 42

    def test_truncates_a_float_like_int(self) -> None:
        # int() truncates toward zero; the helper is a drop-in and does the same.
        assert safe_int(5.9) == 5
        assert safe_int(-5.9) == -5

    def test_accepts_bool_as_int_like_the_builtin(self) -> None:
        # A tolerant coercer mirrors int(True) == 1; the strict guard-field readers reject bool.
        assert safe_int(True) == 1
        assert safe_int(False) == 0

    def test_can_treat_bool_as_absent(self) -> None:
        assert safe_int(True, default=9, allow_bool=False) == 9
        assert safe_int(False, default=9, allow_bool=False) == 9

    def test_missing_value_returns_the_default(self) -> None:
        assert safe_int(None) is None
        assert safe_int(None, default=7) == 7

    def test_non_numeric_returns_the_default(self) -> None:
        assert safe_int("not-a-number", default=20) == 20
        assert safe_int(["x"], default=20) == 20
        assert safe_int({"a": 1}, default=20) == 20

    def test_non_finite_float_returns_the_default(self) -> None:
        # A JSON 1e400 decodes to inf; int(inf) raises OverflowError, int(nan) raises ValueError.
        assert safe_int(float("inf"), default=0) == 0
        assert safe_int(float("-inf"), default=0) == 0
        assert safe_int(float("nan"), default=0) == 0

    def test_clamps_below_the_minimum(self) -> None:
        assert safe_int(-5, default=0, min_value=0) == 0

    def test_clamps_above_the_maximum(self) -> None:
        assert safe_int(1000, default=0, max_value=100) == 100

    def test_a_value_within_bounds_is_unchanged(self) -> None:
        assert safe_int(50, default=0, min_value=0, max_value=100) == 50

    def test_the_default_is_returned_unclamped(self) -> None:
        # Bounds apply only to a successfully coerced value; the caller's default is trusted.
        assert safe_int("bad", default=-1, min_value=0) == -1


class TestSafeFloat:
    def test_coerces_a_plain_float(self) -> None:
        assert safe_float(1.5) == 1.5

    def test_coerces_an_int_to_float(self) -> None:
        assert safe_float(3) == 3.0

    def test_parses_a_numeric_string(self) -> None:
        assert safe_float("1.5") == 1.5

    def test_missing_value_returns_the_default(self) -> None:
        assert safe_float(None) is None
        assert safe_float(None, default=2.5) == 2.5

    def test_non_numeric_returns_the_default(self) -> None:
        assert safe_float("nope", default=0.0) == 0.0
        assert safe_float(object(), default=0.0) == 0.0

    def test_overflowing_int_returns_the_default(self) -> None:
        # A JSON integer too large for a double raises OverflowError on float().
        assert safe_float(10**400, default=0.0) == 0.0

    def test_non_finite_rejected_when_finite_true(self) -> None:
        assert safe_float(float("inf"), default=0.0) == 0.0
        assert safe_float(float("nan"), default=0.0) == 0.0
        assert safe_float("1e400", default=0.0) == 0.0  # string parses to inf, then rejected

    def test_non_finite_kept_when_finite_false(self) -> None:
        assert safe_float(float("inf"), default=0.0, finite=False) == float("inf")
        assert math.isnan(safe_float(float("nan"), default=0.0, finite=False))

    def test_can_treat_bool_as_absent(self) -> None:
        assert safe_float(True, default=2.5, allow_bool=False) == 2.5
        assert safe_float(False, default=2.5, allow_bool=False) == 2.5
