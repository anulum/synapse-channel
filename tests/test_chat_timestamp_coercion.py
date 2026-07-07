# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL - unit tests for the chat handler's client-timestamp coercion

"""The chat handler treats a frame's ``timestamp`` as advisory client metadata.

A missing, falsy, non-numeric, non-finite, or double-overflowing value must fall
back to the hub's authoritative clock rather than crash the frame handler (a bare
``float`` raises on a string, list, or huge integer) or admit ``inf``/``nan`` into
the retained history, the broadcast, and the dead-letter ledger's ordering key.
"""

from __future__ import annotations

import math

import pytest

from synapse_channel.core.handlers.messaging import _client_timestamp

NOW = 1_700_000_000.5


@pytest.mark.parametrize("raw", [0, 0.0, None, "", [], {}, False, True])
def test_falsy_or_boolean_timestamp_falls_back_to_the_hub_clock(raw: object) -> None:
    # A falsy value keeps the original ``or time.time()`` behaviour; a bool is
    # rejected outright (``isinstance(True, int)`` is true, so it is guarded first).
    assert _client_timestamp(raw, NOW) == NOW


@pytest.mark.parametrize("raw", ["not-a-number", "inf", "nan", [1, 2, 3], {"a": 1}, object()])
def test_non_numeric_timestamp_falls_back_without_raising(raw: object) -> None:
    # The old bare ``float(raw)`` raised ValueError/TypeError here, dropping the
    # sender's connection out of the frame handler; the coercion returns the clock.
    assert _client_timestamp(raw, NOW) == NOW


def test_double_overflowing_integer_timestamp_falls_back() -> None:
    # A 400-digit JSON integer is finite but ``float()`` of it raises OverflowError.
    assert _client_timestamp(10**400, NOW) == NOW


@pytest.mark.parametrize("raw", [float("inf"), float("-inf"), float("nan")])
def test_non_finite_timestamp_falls_back_to_the_hub_clock(raw: float) -> None:
    # ``1e400`` decodes to ``inf`` through the JSON number grammar (bypassing the
    # bareword-constant guard), so a finite fallback must be enforced here too.
    assert _client_timestamp(raw, NOW) == NOW


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(1700.0, 1700.0), (42, 42.0), (0.5, 0.5), (1_700_000_000, 1_700_000_000.0)],
)
def test_a_finite_number_is_kept_as_the_client_timestamp(raw: object, expected: float) -> None:
    result = _client_timestamp(raw, NOW)
    assert result == expected
    assert math.isfinite(result)
