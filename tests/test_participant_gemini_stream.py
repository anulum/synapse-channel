# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Gemini stream-json parser regressions

from __future__ import annotations

import json

from synapse_channel.participants.gemini_stream import (
    GEMINI_SCHEMA_VERIFIED,
    parse_gemini_stream,
)
from synapse_channel.participants.stream_json import NO_RESULT_SUBTYPE


def _line(payload: dict[str, object]) -> str:
    return json.dumps(payload)


def _full_stream() -> list[str]:
    """One synthetic turn matching the installed 0.47.0 bundle emitters.

    Synthetic by necessity — a live capture is blocked by ``IneligibleTierError`` on
    this workstation — and shaped field-for-field on the ``StreamJsonFormatter``
    ``emitEvent`` call sites; it is NOT a real capture, which is exactly why
    :data:`GEMINI_SCHEMA_VERIFIED` stays false.
    """
    return [
        _line(
            {
                "type": "init",
                "timestamp": "2026-07-12T15:30:00.000Z",
                "session_id": "session-uuid-1",
                "model": "gemini-2.5-pro",
            }
        ),
        _line(
            {
                "type": "message",
                "timestamp": "2026-07-12T15:30:00.100Z",
                "role": "user",
                "content": "Reply with exactly one word: pong",
            }
        ),
        _line(
            {
                "type": "message",
                "timestamp": "2026-07-12T15:30:01.000Z",
                "role": "assistant",
                "content": "po",
                "delta": True,
            }
        ),
        _line(
            {
                "type": "message",
                "timestamp": "2026-07-12T15:30:01.050Z",
                "role": "assistant",
                "content": "ng",
                "delta": True,
            }
        ),
        _line(
            {
                "type": "result",
                "timestamp": "2026-07-12T15:30:02.000Z",
                "status": "success",
                "stats": {"total_tokens": 10},
            }
        ),
    ]


def test_schema_flag_stays_false_until_behavioural_capture() -> None:
    """Source-level bundle reading is not a live capture; turns must stay refused."""
    assert GEMINI_SCHEMA_VERIFIED is False


def test_parse_full_stream_concatenates_assistant_deltas() -> None:
    outcome = parse_gemini_stream(_full_stream())
    assert outcome.answer == "pong"
    assert outcome.rationale == ""
    assert outcome.session_id == "session-uuid-1"
    assert not outcome.is_error
    assert outcome.subtype == "success"
    assert outcome.num_turns == 1
    assert outcome.stop_reason == "end_turn"


def test_parse_ignores_user_echo_and_tool_telemetry() -> None:
    lines = _full_stream()
    lines.insert(
        2,
        _line(
            {
                "type": "tool_use",
                "timestamp": "2026-07-12T15:30:00.500Z",
                "tool_name": "read_file",
                "tool_id": "call-1",
                "parameters": {"file_path": "/tmp/x"},
            }
        ),
    )
    lines.insert(
        3,
        _line(
            {
                "type": "tool_result",
                "timestamp": "2026-07-12T15:30:00.600Z",
                "tool_id": "call-1",
                "status": "success",
                "output": "…",
            }
        ),
    )
    outcome = parse_gemini_stream(lines)
    assert outcome.answer == "pong"
    assert not outcome.is_error


def test_parse_without_result_is_no_result_error() -> None:
    outcome = parse_gemini_stream(_full_stream()[:-1])
    assert outcome.is_error
    assert outcome.subtype == NO_RESULT_SUBTYPE
    assert outcome.answer == "pong"
    assert outcome.session_id == "session-uuid-1"
    assert outcome.num_turns == 0


def test_parse_error_result_carries_status_and_message() -> None:
    lines = _full_stream()[:-1]
    lines.append(
        _line(
            {
                "type": "result",
                "timestamp": "2026-07-12T15:30:02.000Z",
                "status": "error",
                "error": {"type": "FatalToolExecutionError", "message": "tool exploded"},
                "stats": {"total_tokens": 10},
            }
        )
    )
    outcome = parse_gemini_stream(lines)
    assert outcome.is_error
    assert outcome.subtype == "error"
    assert outcome.stop_reason == "tool exploded"


def test_parse_error_events_feed_stop_reason_when_stream_truncates() -> None:
    lines = [
        _full_stream()[0],
        _line(
            {
                "type": "error",
                "timestamp": "2026-07-12T15:30:01.000Z",
                "severity": "warning",
                "message": "stream aborted mid-flight",
            }
        ),
    ]
    outcome = parse_gemini_stream(lines)
    assert outcome.is_error
    assert outcome.subtype == NO_RESULT_SUBTYPE
    assert outcome.stop_reason == "stream aborted mid-flight"


def test_parse_ignores_error_event_with_blank_message() -> None:
    lines = _full_stream()
    lines.insert(
        2,
        _line({"type": "error", "timestamp": "2026-07-12T15:30:00.700Z", "severity": "warning"}),
    )
    outcome = parse_gemini_stream(lines)
    assert outcome.answer == "pong"
    assert not outcome.is_error


def test_parse_skips_blank_non_json_and_non_object_lines() -> None:
    lines = ["", "   ", "not-json", json.dumps([1, 2, 3]), json.dumps("plain"), *_full_stream()]
    outcome = parse_gemini_stream(lines)
    assert outcome.answer == "pong"
    assert not outcome.is_error


def test_parse_skips_empty_and_non_string_assistant_content() -> None:
    lines = _full_stream()
    lines.insert(
        2,
        _line(
            {
                "type": "message",
                "timestamp": "2026-07-12T15:30:00.900Z",
                "role": "assistant",
                "content": "",
                "delta": True,
            }
        ),
    )
    lines.insert(
        3,
        _line(
            {
                "type": "message",
                "timestamp": "2026-07-12T15:30:00.950Z",
                "role": "assistant",
                "content": {"unexpected": "object"},
                "delta": True,
            }
        ),
    )
    outcome = parse_gemini_stream(lines)
    assert outcome.answer == "pong"


def test_parse_result_with_blank_status_is_error() -> None:
    lines = _full_stream()[:-1]
    lines.append(_line({"type": "result", "timestamp": "2026-07-12T15:30:02.000Z", "status": ""}))
    outcome = parse_gemini_stream(lines)
    assert outcome.is_error
    assert outcome.subtype == "error"
