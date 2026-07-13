# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li

import json

from synapse_channel.participants.opencode_stream import (
    OPENCODE_SCHEMA_VERIFIED,
    OPENCODE_SCHEMA_VERSION,
    parse_opencode_api_response,
    parse_opencode_stream,
)


def _line(event_type: str, **extra: object) -> str:
    return json.dumps({"type": event_type, "timestamp": 1, "sessionID": "ses-1", **extra})


def test_source_pinned_jsonl_success_is_normalized() -> None:
    outcome = parse_opencode_stream(
        [
            _line("step_start", part={"type": "step-start"}),
            _line("reasoning", part={"type": "reasoning", "text": "why"}),
            _line("text", part={"type": "text", "text": "hello"}),
            _line(
                "step_finish",
                part={
                    "type": "step-finish",
                    "reason": "stop",
                    "cost": 0.25,
                    "tokens": {"input": 7, "output": 3, "reasoning": 1},
                },
            ),
        ]
    )
    assert OPENCODE_SCHEMA_VERIFIED is True
    assert OPENCODE_SCHEMA_VERSION == "1.17.20"
    assert outcome.answer == "hello"
    assert outcome.rationale == "why"
    assert outcome.session_id == "ses-1"
    assert outcome.input_tokens == 7
    assert outcome.output_tokens == 3
    assert outcome.cost_usd == 0.25
    assert outcome.is_error is False


def test_jsonl_schema_drift_incomplete_and_session_mismatch_fail_closed() -> None:
    assert parse_opencode_stream(["not-json"]).subtype == "malformed_event"
    assert parse_opencode_stream([_line("text", part={"text": "x"})]).is_error is True
    mismatch = json.dumps(
        {"type": "text", "timestamp": 2, "sessionID": "other", "part": {"text": "x"}}
    )
    assert parse_opencode_stream([_line("step_start"), mismatch]).subtype == "session_mismatch"
    assert parse_opencode_stream([_line("future")]).subtype == "schema_drift"


def test_jsonl_invalid_session_parts_and_usage_fail_closed_or_zero() -> None:
    assert parse_opencode_stream([json.dumps({"type": "text"})]).subtype == "schema_drift"
    assert (
        parse_opencode_stream([_line("text", part={"type": "text", "text": 4})]).subtype
        == "schema_drift"
    )
    assert (
        parse_opencode_stream([_line("step_finish", part={"type": "wrong"})]).subtype
        == "schema_drift"
    )
    outcome = parse_opencode_stream(
        [
            "",
            _line("tool_use", part={"type": "tool"}),
            _line(
                "step_finish",
                part={
                    "type": "step-finish",
                    "reason": 9,
                    "cost": -1,
                    "tokens": {"input": True, "output": -3},
                },
            ),
        ]
    )
    assert outcome.is_error is False
    assert outcome.stop_reason == ""
    assert outcome.cost_usd == 0
    assert outcome.input_tokens == 0


def test_jsonl_provider_error_preserves_only_error_name() -> None:
    outcome = parse_opencode_stream(
        [_line("error", error={"name": "ProviderAuthError", "message": "secret"})]
    )
    assert outcome.is_error is True
    assert outcome.subtype == "ProviderAuthError"


def test_api_response_collects_text_reasoning_usage_and_session() -> None:
    outcome = parse_opencode_api_response(
        {
            "info": {
                "role": "assistant",
                "sessionID": "ses-api",
                "cost": 0.5,
                "finish": "stop",
                "tokens": {"input": 11, "output": 4},
            },
            "parts": [
                {"type": "reasoning", "text": "r"},
                {"type": "text", "text": "answer"},
                {"type": "tool", "state": {}},
            ],
        }
    )
    assert outcome.answer == "answer"
    assert outcome.rationale == "r"
    assert outcome.session_id == "ses-api"
    assert outcome.input_tokens == 11
    assert outcome.is_error is False


def test_api_response_fails_closed_on_shape_or_provider_error() -> None:
    assert parse_opencode_api_response({}).subtype == "schema_drift"
    error = parse_opencode_api_response(
        {
            "info": {
                "role": "assistant",
                "sessionID": "s",
                "error": {"name": "ApiError", "message": "not exposed"},
            },
            "parts": [],
        }
    )
    assert error.is_error is True
    assert error.subtype == "ApiError"


def test_api_response_refuses_invalid_session_and_part_shapes() -> None:
    invalid_session = {
        "info": {"role": "user", "sessionID": "s"},
        "parts": [],
    }
    assert parse_opencode_api_response(invalid_session).subtype == "schema_drift"
    invalid_part = {
        "info": {"role": "assistant", "sessionID": "s"},
        "parts": ["bad"],
    }
    assert parse_opencode_api_response(invalid_part).session_id == "s"
    invalid_text = {
        "info": {"role": "assistant", "sessionID": "s"},
        "parts": [{"type": "text", "text": 7}],
    }
    assert parse_opencode_api_response(invalid_text).is_error is True
