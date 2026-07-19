# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL - unit tests for chat hub/client timestamp separation

"""Chat frames separate hub-authoritative time from advisory client time.

``timestamp`` is always the hub clock (history and dead-letter ordering key).
A finite client-supplied instant is retained only as ``client_timestamp``.
Missing, falsy, non-numeric, non-finite, or double-overflowing client values
are discarded rather than raised on or used for ordering.
"""

from __future__ import annotations

import math

import pytest

from synapse_channel.core.handlers.messaging import _client_timestamp, _stamp_chat_times

NOW = 1_700_000_000.5


@pytest.mark.parametrize("raw", [0, 0.0, None, "", [], {}, False, True])
def test_falsy_or_boolean_client_timestamp_is_discarded(raw: object) -> None:
    assert _client_timestamp(raw) is None


@pytest.mark.parametrize("raw", ["not-a-number", "inf", "nan", [1, 2, 3], {"a": 1}, object()])
def test_non_numeric_client_timestamp_is_discarded_without_raising(raw: object) -> None:
    assert _client_timestamp(raw) is None


def test_double_overflowing_integer_client_timestamp_is_discarded() -> None:
    assert _client_timestamp(10**400) is None


@pytest.mark.parametrize("raw", [float("inf"), float("-inf"), float("nan")])
def test_non_finite_client_timestamp_is_discarded(raw: float) -> None:
    assert _client_timestamp(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(1700.0, 1700.0), (42, 42.0), (0.5, 0.5), (1_700_000_000, 1_700_000_000.0)],
)
def test_finite_client_timestamp_is_retained_as_advisory(raw: object, expected: float) -> None:
    result = _client_timestamp(raw)
    assert result == expected
    assert result is not None
    assert math.isfinite(result)


def test_stamp_overwrites_envelope_with_hub_time_and_keeps_client_advisory() -> None:
    frame = {"timestamp": 42.0, "client_timestamp": 999.0, "payload": "hi"}
    hub_ts = _stamp_chat_times(frame, now=NOW)
    assert hub_ts == NOW
    assert frame["timestamp"] == NOW
    assert frame["client_timestamp"] == 42.0  # re-derived; spoofed 999 dropped


def test_stamp_omits_client_timestamp_when_client_value_is_unusable() -> None:
    frame = {"timestamp": "not-a-number", "client_timestamp": 123.0}
    _stamp_chat_times(frame, now=NOW)
    assert frame["timestamp"] == NOW
    assert "client_timestamp" not in frame


def test_byzantine_future_client_time_cannot_become_the_ordering_key() -> None:
    future = NOW + 86_400.0 * 365.0
    frame = {"timestamp": future, "payload": "poison"}
    _stamp_chat_times(frame, now=NOW)
    assert frame["timestamp"] == NOW
    assert frame["client_timestamp"] == future


def test_backdated_client_time_cannot_become_the_ordering_key() -> None:
    past = 1.0
    frame = {"timestamp": past}
    _stamp_chat_times(frame, now=NOW)
    assert frame["timestamp"] == NOW
    assert frame["client_timestamp"] == past
