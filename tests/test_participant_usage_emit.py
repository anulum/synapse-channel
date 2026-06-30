# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the opt-in usage-note bridge
"""Tests for :mod:`synapse_channel.participants.usage_emit`.

A recording poster captures what :func:`emit_usage` would append to the progress ledger, so the
suite asserts the canonical usage-note body round-trips through the core parser, that a positive
cost is recorded while a zero cost is omitted for a pricing table to estimate, and that a turn
without a usable model id is skipped rather than raising.
"""

from __future__ import annotations

from synapse_channel.core.accounting import USAGE_NOTE_KIND, parse_usage_note
from synapse_channel.participants.envelope import (
    TurnRequest,
    TurnResult,
    build_turn_result,
    error_turn_result,
)
from synapse_channel.participants.participant import ParticipantChannel
from synapse_channel.participants.stream_json import StreamOutcome
from synapse_channel.participants.usage_emit import emit_usage


class _RecordingPoster:
    """Capture every progress note posted, mimicking ``SynapseAgent.post_progress``."""

    def __init__(self) -> None:
        self.notes: list[tuple[str, str, str]] = []

    async def __call__(self, task_id: str, text: str, *, kind: str = "note") -> None:
        self.notes.append((task_id, text, kind))


def _result(
    *,
    model: str,
    cost_usd: float = 0.0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    is_error: bool = False,
) -> TurnResult:
    request = TurnRequest(topic_id="topic-42", prompt="ask", model=model)
    if is_error:
        return error_turn_result(
            participant="SC/claude-a",
            channel=ParticipantChannel.HEADLESS,
            request=request,
            reason="boom",
        )
    return build_turn_result(
        participant="SC/claude-a",
        channel=ParticipantChannel.HEADLESS,
        request=request,
        outcome=StreamOutcome(
            answer="ok",
            rationale="",
            session_id="s",
            is_error=False,
            subtype="success",
            cost_usd=cost_usd,
            num_turns=1,
            stop_reason="end_turn",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        ),
    )


async def test_emits_a_parseable_usage_note_with_cost() -> None:
    poster = _RecordingPoster()
    result = _result(model="claude-opus-4-8", cost_usd=0.25, input_tokens=120, output_tokens=34)
    emitted = await emit_usage(result, post_progress=poster)
    assert emitted is True
    assert len(poster.notes) == 1
    task_id, text, kind = poster.notes[0]
    assert task_id == "topic-42"
    assert kind == USAGE_NOTE_KIND
    parsed = parse_usage_note(text)
    assert parsed is not None
    assert parsed["model"] == "claude-opus-4-8"
    assert parsed["input_tokens"] == 120
    assert parsed["output_tokens"] == 34
    assert parsed["calls"] == 1
    assert parsed["cost"] == 0.25


async def test_zero_cost_is_omitted_so_a_price_table_can_estimate() -> None:
    poster = _RecordingPoster()
    result = _result(model="gemma3:1b", cost_usd=0.0, input_tokens=10, output_tokens=2)
    assert await emit_usage(result, post_progress=poster) is True
    parsed = parse_usage_note(poster.notes[0][1])
    assert parsed is not None
    # No recorder cost recorded when the provider reports none.
    assert "cost" not in parsed
    assert parsed["input_tokens"] == 10


async def test_error_turn_with_model_is_still_recorded_with_zero_tokens() -> None:
    poster = _RecordingPoster()
    result = _result(model="gpt-x", is_error=True)
    assert await emit_usage(result, post_progress=poster) is True
    parsed = parse_usage_note(poster.notes[0][1])
    assert parsed is not None
    assert parsed["model"] == "gpt-x"
    assert parsed["input_tokens"] == 0
    assert parsed["output_tokens"] == 0


async def test_turn_without_model_is_skipped() -> None:
    poster = _RecordingPoster()
    result = _result(model="", cost_usd=0.5, input_tokens=5, output_tokens=1)
    assert await emit_usage(result, post_progress=poster) is False
    assert poster.notes == []


async def test_model_with_whitespace_is_skipped() -> None:
    # The accounting note format forbids whitespace in a model id; emission must not raise.
    poster = _RecordingPoster()
    result = _result(model="not a model", input_tokens=5)
    assert await emit_usage(result, post_progress=poster) is False
    assert poster.notes == []
