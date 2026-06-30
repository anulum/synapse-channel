# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the session-metric note codec
"""Tests for :mod:`synapse_channel.participants.session_metric_note`.

The suite asserts that a :class:`SessionMetrics` snapshot round-trips through the canonical body,
that the optional rate-limit utilisation is present only when observed, that an invalid snapshot
is rejected at format time, and that the parser coerces malformed or absent fields to safe
defaults and rejects a non-session-metric body.
"""

from __future__ import annotations

import pytest

from synapse_channel.participants.session_metric_note import (
    SESSION_METRIC_PREFIX,
    format_session_metric_note,
    parse_session_metric_note,
)
from synapse_channel.participants.session_telemetry import SessionMetrics


def _metrics(**overrides: object) -> SessionMetrics:
    base: dict[str, object] = {
        "turns": 4,
        "errors": 1,
        "abstentions": 1,
        "input_tokens": 1200,
        "output_tokens": 340,
        "cost_usd": 0.25,
        "total_latency_seconds": 8.5,
        "max_rate_limit_utilisation": 0.6,
        "last_input_tokens": 410,
    }
    base.update(overrides)
    return SessionMetrics(**base)  # type: ignore[arg-type]


def test_snapshot_round_trips_through_the_body() -> None:
    note = format_session_metric_note(_metrics())
    assert note.split()[0] == SESSION_METRIC_PREFIX
    parsed = parse_session_metric_note(note)
    assert parsed is not None
    assert parsed["turns"] == 4
    assert parsed["errors"] == 1
    assert parsed["abstentions"] == 1
    assert parsed["input_tokens"] == 1200
    assert parsed["output_tokens"] == 340
    assert parsed["cost_usd"] == pytest.approx(0.25)
    assert parsed["total_latency_seconds"] == pytest.approx(8.5)
    assert parsed["last_input_tokens"] == 410
    assert parsed["max_rate_limit_utilisation"] == pytest.approx(0.6)


def test_absent_utilisation_is_omitted_and_parses_back_as_none() -> None:
    note = format_session_metric_note(_metrics(max_rate_limit_utilisation=None))
    assert "max_rate_limit_utilisation" not in note
    parsed = parse_session_metric_note(note)
    assert parsed is not None
    assert parsed["max_rate_limit_utilisation"] is None


def test_zero_utilisation_is_recorded_distinct_from_absent() -> None:
    note = format_session_metric_note(_metrics(max_rate_limit_utilisation=0.0))
    assert "max_rate_limit_utilisation=0.000000" in note
    parsed = parse_session_metric_note(note)
    assert parsed is not None
    assert parsed["max_rate_limit_utilisation"] == pytest.approx(0.0)


def test_negative_count_is_rejected() -> None:
    with pytest.raises(ValueError, match="counts must not be negative"):
        format_session_metric_note(_metrics(errors=-1))


def test_negative_spend_or_latency_is_rejected() -> None:
    with pytest.raises(ValueError, match="spend and latency must not be negative"):
        format_session_metric_note(_metrics(cost_usd=-0.01))
    with pytest.raises(ValueError, match="spend and latency must not be negative"):
        format_session_metric_note(_metrics(total_latency_seconds=-1.0))


def test_out_of_range_utilisation_is_rejected_at_format() -> None:
    with pytest.raises(ValueError, match="utilisation must be within"):
        format_session_metric_note(_metrics(max_rate_limit_utilisation=1.5))


def test_non_session_metric_body_returns_none() -> None:
    assert parse_session_metric_note("usage model=x") is None
    assert parse_session_metric_note("") is None


def test_parser_coerces_malformed_and_absent_fields_to_defaults() -> None:
    # Bare prefix: every numeric field defaults; a malformed token is ignored; a key without
    # a separator is skipped entirely.
    parsed = parse_session_metric_note(
        f"{SESSION_METRIC_PREFIX} turns=oops cost_usd=NaNish bareword output_tokens=7"
    )
    assert parsed is not None
    assert parsed["turns"] == 0
    assert parsed["cost_usd"] == pytest.approx(0.0)
    assert parsed["output_tokens"] == 7
    assert parsed["max_rate_limit_utilisation"] is None


def test_parser_rejects_negative_and_out_of_range_values() -> None:
    parsed = parse_session_metric_note(
        f"{SESSION_METRIC_PREFIX} turns=-3 cost_usd=-2 max_rate_limit_utilisation=2.0"
    )
    assert parsed is not None
    # A negative int clamps to zero; a negative float falls back to the default; an
    # out-of-range utilisation parses back as absent.
    assert parsed["turns"] == 0
    assert parsed["cost_usd"] == pytest.approx(0.0)
    assert parsed["max_rate_limit_utilisation"] is None


def test_parser_reads_an_invalid_utilisation_token_as_absent() -> None:
    parsed = parse_session_metric_note(
        f"{SESSION_METRIC_PREFIX} turns=1 max_rate_limit_utilisation=high"
    )
    assert parsed is not None
    assert parsed["max_rate_limit_utilisation"] is None
