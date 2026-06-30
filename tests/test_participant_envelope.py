# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the Participant Fabric turn envelopes
"""Tests for :mod:`synapse_channel.participants.envelope`."""

from __future__ import annotations

import json

from synapse_channel.participants.envelope import (
    ENVELOPE_KIND,
    REQUEST_KIND,
    TurnRequest,
    build_turn_result,
    error_turn_result,
    turn_request_from_payload,
    turn_request_to_payload,
    turn_result_from_payload,
    turn_result_to_payload,
)
from synapse_channel.participants.participant import ParticipantChannel
from synapse_channel.participants.stream_json import StreamOutcome


def _outcome(
    *,
    answer: str = "the answer",
    rationale: str = "  because  ",
    session_id: str = "sess-1",
    is_error: bool = False,
    subtype: str = "success",
    cost_usd: float = 0.5,
    stop_reason: str = "end_turn",
) -> StreamOutcome:
    return StreamOutcome(
        answer=answer,
        rationale=rationale,
        session_id=session_id,
        is_error=is_error,
        subtype=subtype,
        cost_usd=cost_usd,
        num_turns=1,
        stop_reason=stop_reason,
    )


def _request() -> TurnRequest:
    return TurnRequest(topic_id="topic-9", prompt="ask", context="ctx")


def test_turn_request_defaults_are_empty() -> None:
    request = TurnRequest(topic_id="t", prompt="p")
    assert request.context == ""
    assert request.resume_session == ""


def test_build_turn_result_carries_answer_and_strips_whitespace() -> None:
    result = build_turn_result(
        participant="SC/claude-a",
        channel=ParticipantChannel.HEADLESS,
        request=_request(),
        outcome=_outcome(answer="  hello  "),
    )
    assert result["kind"] == ENVELOPE_KIND
    assert result["participant"] == "SC/claude-a"
    assert result["channel"] == "headless"
    assert result["topic_id"] == "topic-9"
    assert result["answer"] == "hello"
    assert result["rationale"] == "because"
    assert result["abstained"] is False
    assert result["is_error"] is False
    assert result["reason"] == ""
    assert result["session"] == "sess-1"
    assert result["cost_usd"] == 0.5
    assert result["stop_reason"] == "end_turn"


def test_build_turn_result_marks_abstain_when_no_answer_and_no_error() -> None:
    result = build_turn_result(
        participant="SC/claude-a",
        channel=ParticipantChannel.HEADLESS,
        request=_request(),
        outcome=_outcome(answer="   "),
    )
    assert result["abstained"] is True
    assert result["is_error"] is False
    assert result["reason"] == "no answer produced"


def test_build_turn_result_reports_error_subtype_as_reason() -> None:
    result = build_turn_result(
        participant="SC/claude-a",
        channel=ParticipantChannel.HEADLESS,
        request=_request(),
        outcome=_outcome(answer="", is_error=True, subtype="error_max_turns"),
    )
    assert result["is_error"] is True
    assert result["abstained"] is False
    assert result["reason"] == "error_max_turns"


def test_error_turn_result_is_a_typed_failure() -> None:
    result = error_turn_result(
        participant="SC/claude-a",
        channel=ParticipantChannel.PTY,
        request=_request(),
        reason="binary missing",
    )
    assert result["is_error"] is True
    assert result["abstained"] is False
    assert result["answer"] == ""
    assert result["rationale"] == ""
    assert result["channel"] == "pty"
    assert result["reason"] == "binary missing"
    assert result["stop_reason"] == "error"


def test_payload_round_trip_preserves_every_field() -> None:
    result = build_turn_result(
        participant="SC/claude-a",
        channel=ParticipantChannel.HEADLESS,
        request=_request(),
        outcome=_outcome(),
    )
    restored = turn_result_from_payload(turn_result_to_payload(result))
    assert restored == result


def test_to_payload_is_sorted_json() -> None:
    result = error_turn_result(
        participant="p",
        channel=ParticipantChannel.MCP,
        request=_request(),
        reason="r",
    )
    payload = turn_result_to_payload(result)
    assert json.loads(payload)["kind"] == ENVELOPE_KIND
    assert payload.index('"answer"') < payload.index('"participant"')


def test_from_payload_rejects_non_json() -> None:
    assert turn_result_from_payload("not json") is None


def test_from_payload_rejects_non_object() -> None:
    assert turn_result_from_payload(json.dumps([1, 2, 3])) is None


def test_from_payload_rejects_foreign_kind() -> None:
    assert turn_result_from_payload(json.dumps({"kind": "chat", "answer": "x"})) is None


def test_from_payload_coerces_field_types_defensively() -> None:
    raw = {
        "kind": ENVELOPE_KIND,
        "participant": 42,
        "channel": None,
        "topic_id": "t",
        "answer": 7,
        "abstained": 1,
        "is_error": 0,
        "cost_usd": "1.5",
        "stop_reason": "end_turn",
    }
    restored = turn_result_from_payload(json.dumps(raw))
    assert restored is not None
    assert restored["participant"] == "42"
    assert restored["channel"] == "None"
    assert restored["answer"] == "7"
    assert restored["abstained"] is True
    assert restored["is_error"] is False
    assert restored["cost_usd"] == 1.5


def test_from_payload_defaults_unparsable_cost_to_zero() -> None:
    raw = {"kind": ENVELOPE_KIND, "cost_usd": "not-a-number"}
    restored = turn_result_from_payload(json.dumps(raw))
    assert restored is not None
    assert restored["cost_usd"] == 0.0


# --- turn request serialisation -------------------------------------------------------


def test_turn_request_round_trip_preserves_every_field() -> None:
    request = TurnRequest(
        topic_id="t-1", prompt="answer this", context="role: tester", resume_session="sess-9"
    )
    restored = turn_request_from_payload(turn_request_to_payload(request))
    assert restored == request


def test_turn_request_payload_is_sorted_json_with_discriminator() -> None:
    payload = turn_request_to_payload(TurnRequest(topic_id="t", prompt="p"))
    parsed = json.loads(payload)
    assert parsed["kind"] == REQUEST_KIND
    assert list(parsed.keys()) == sorted(parsed.keys())


def test_turn_request_from_payload_rejects_non_json() -> None:
    assert turn_request_from_payload("not json") is None


def test_turn_request_from_payload_rejects_non_object() -> None:
    assert turn_request_from_payload(json.dumps([1, 2, 3])) is None


def test_turn_request_from_payload_rejects_foreign_kind() -> None:
    assert turn_request_from_payload(json.dumps({"kind": "chat", "prompt": "x"})) is None


def test_turn_request_from_payload_coerces_field_types_defensively() -> None:
    raw = {"kind": REQUEST_KIND, "topic_id": 7, "prompt": None, "context": 3.5}
    restored = turn_request_from_payload(json.dumps(raw))
    assert restored is not None
    assert restored.topic_id == "7"
    assert restored.prompt == "None"
    assert restored.context == "3.5"
    assert restored.resume_session == ""
