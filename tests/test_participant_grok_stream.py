# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the verified Grok streaming-json parser
"""Tests for :mod:`synapse_channel.participants.grok_stream`."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from synapse_channel.participants.grok_stream import (
    GROK_SCHEMA_VERIFIED,
    parse_grok_stream,
)
from synapse_channel.participants.stream_json import NO_RESULT_SUBTYPE

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "grok_stream" / "real_single_pong.ndjson"


def test_schema_is_flagged_verified_after_real_capture() -> None:
    assert GROK_SCHEMA_VERIFIED is True


def test_real_capture_fixture_yields_pong_and_session() -> None:
    assert (
        hashlib.sha256(_FIXTURE.read_bytes()).hexdigest()
        == "71ffaeaa567aa59290318afa7284804c3bd7c264a7fec1907edfc15cc0f5e44c"
    )
    lines = _FIXTURE.read_text(encoding="utf-8").splitlines()
    outcome = parse_grok_stream(lines)
    assert outcome.is_error is False
    assert outcome.subtype == "success"
    assert outcome.answer == "pong"
    assert "pong" in outcome.rationale.lower()
    assert outcome.session_id == "019f544b-1fa8-7ae2-8aa5-b7bc232f9476"
    assert outcome.stop_reason in {"EndTurn", "end_turn"}


def _synthetic_stream(answer: str = "ok", *, session: str = "sess-1") -> list[str]:
    return [
        json.dumps({"type": "thought", "data": "think "}),
        json.dumps({"type": "thought", "data": "more"}),
        json.dumps({"type": "text", "data": answer[:2]}),
        json.dumps({"type": "text", "data": answer[2:]}),
        json.dumps(
            {
                "type": "end",
                "stopReason": "EndTurn",
                "sessionId": session,
                "requestId": "req-1",
            }
        ),
    ]


def test_concatenates_text_and_thought_tokens() -> None:
    outcome = parse_grok_stream(_synthetic_stream("hello", session="abc"))
    assert outcome.answer == "hello"
    assert outcome.rationale == "think more"
    assert outcome.session_id == "abc"
    assert outcome.is_error is False


def test_stream_without_end_is_error_with_best_effort_text() -> None:
    lines = [
        json.dumps({"type": "text", "data": "partial"}),
    ]
    outcome = parse_grok_stream(lines)
    assert outcome.is_error is True
    assert outcome.subtype == NO_RESULT_SUBTYPE
    assert outcome.answer == "partial"


def test_blank_and_non_json_lines_are_skipped() -> None:
    lines = [
        "",
        "not-json",
        json.dumps({"type": "text", "data": "x"}),
        json.dumps({"type": "end", "sessionId": "s", "stopReason": "EndTurn"}),
    ]
    outcome = parse_grok_stream(lines)
    assert outcome.answer == "x"
    assert outcome.session_id == "s"
    assert outcome.is_error is False


def test_snake_case_session_keys_on_end_are_accepted() -> None:
    lines = [
        json.dumps({"type": "text", "data": "y"}),
        json.dumps({"type": "end", "session_id": "snake", "stop_reason": "end_turn"}),
    ]
    outcome = parse_grok_stream(lines)
    assert outcome.session_id == "snake"
    assert outcome.stop_reason == "end_turn"


def test_end_event_final_data_blob_is_used_when_no_text_tokens() -> None:
    """Some Grok builds put the full answer only on the end event's data field."""
    lines = [
        json.dumps({"type": "thought", "data": "planning"}),
        json.dumps(
            {
                "type": "end",
                "data": "final-only-answer",
                "sessionId": "end-blob",
                "stopReason": "EndTurn",
            }
        ),
    ]
    outcome = parse_grok_stream(lines)
    assert outcome.is_error is False
    assert outcome.answer == "final-only-answer"
    assert outcome.session_id == "end-blob"
    assert outcome.rationale == "planning"


def test_end_blob_does_not_override_streamed_text() -> None:
    lines = [
        json.dumps({"type": "text", "data": "streamed"}),
        json.dumps(
            {
                "type": "end",
                "data": "should-not-replace",
                "sessionId": "prefer-stream",
                "stopReason": "EndTurn",
            }
        ),
    ]
    outcome = parse_grok_stream(lines)
    assert outcome.answer == "streamed"


def test_str_field_skips_empty_and_non_string_values() -> None:
    from synapse_channel.participants.grok_stream import _str_field

    event = {
        "sessionId": "",
        "session_id": 123,
        "other": " keep-me ",
    }
    assert _str_field(event, "sessionId", "session_id", "other") == "keep-me"
    assert _str_field(event, "missing", "also_missing") == ""


def test_empty_and_non_string_fragments_are_ignored() -> None:
    lines = [
        json.dumps({"type": "thought", "data": ""}),
        json.dumps({"type": "thought", "data": 42}),
        json.dumps({"type": "text", "data": ""}),
        json.dumps({"type": "text", "data": None}),
        json.dumps({"type": "text", "data": "kept"}),
        json.dumps({"type": "end", "data": "", "sessionId": "frag", "stopReason": "EndTurn"}),
        json.dumps({"type": "unknown", "data": "noise"}),
    ]
    outcome = parse_grok_stream(lines)
    assert outcome.answer == "kept"
    assert outcome.rationale == ""
    assert outcome.session_id == "frag"
    assert outcome.is_error is False
