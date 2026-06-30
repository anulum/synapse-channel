# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the Claude headless stream-json parser
"""Tests for :mod:`synapse_channel.participants.stream_json`.

The fixture lines mirror the schema captured from a real
``claude -p … --output-format stream-json --verbose`` invocation (Claude Code 2.1.x):
an ``init`` system event, ``thinking_tokens`` progress noise, a ``rate_limit_event``,
``assistant`` events carrying ``thinking`` and ``text`` blocks, and the terminal
``result`` event that is the authoritative source of the answer.
"""

from __future__ import annotations

import json

from synapse_channel.participants.stream_json import (
    NO_RESULT_SUBTYPE,
    parse_claude_stream,
)


def _init(session_id: str = "sess-abc") -> str:
    return json.dumps({"type": "system", "subtype": "init", "session_id": session_id, "model": "x"})


def _thinking_noise() -> str:
    return json.dumps({"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 5})


def _rate_limit() -> str:
    return json.dumps({"type": "rate_limit_event", "rate_limit_info": {"utilization": 0.5}})


def _assistant_thinking(text: str, session_id: str = "sess-abc") -> str:
    return json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "thinking", "thinking": text}]},
            "session_id": session_id,
        }
    )


def _assistant_text(text: str, session_id: str = "sess-abc") -> str:
    return json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": text}]},
            "session_id": session_id,
        }
    )


def _result(
    *,
    answer: str = "pong",
    session_id: str = "sess-abc",
    is_error: bool = False,
    subtype: str = "success",
    cost: float = 0.0238,
    num_turns: int = 1,
    stop_reason: str = "end_turn",
) -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": subtype,
            "is_error": is_error,
            "result": answer,
            "session_id": session_id,
            "total_cost_usd": cost,
            "num_turns": num_turns,
            "stop_reason": stop_reason,
        }
    )


def test_parses_full_stream_from_terminal_result() -> None:
    lines = [
        _init(),
        _thinking_noise(),
        _rate_limit(),
        _assistant_thinking("let me think"),
        _assistant_text("pong"),
        _result(),
    ]
    outcome = parse_claude_stream(lines)
    assert outcome.answer == "pong"
    assert outcome.rationale == "let me think"
    assert outcome.session_id == "sess-abc"
    assert outcome.is_error is False
    assert outcome.subtype == "success"
    assert outcome.cost_usd == 0.0238
    assert outcome.num_turns == 1
    assert outcome.stop_reason == "end_turn"


def test_concatenates_multiple_thinking_blocks() -> None:
    lines = [
        _init(),
        _assistant_thinking("first"),
        _assistant_thinking("second"),
        _result(),
    ]
    outcome = parse_claude_stream(lines)
    assert outcome.rationale == "first\nsecond"


def test_blank_and_malformed_lines_are_skipped() -> None:
    lines = ["", "   ", "not json at all", "[1,2,3]", _init(), _result()]
    outcome = parse_claude_stream(lines)
    assert outcome.answer == "pong"
    assert outcome.is_error is False


def test_missing_result_event_is_an_error_with_streamed_fallback() -> None:
    lines = [_init(), _assistant_text("partial answer")]
    outcome = parse_claude_stream(lines)
    assert outcome.is_error is True
    assert outcome.subtype == NO_RESULT_SUBTYPE
    assert outcome.answer == "partial answer"
    assert outcome.session_id == "sess-abc"


def test_error_result_event_is_reported() -> None:
    lines = [_init(), _result(answer="", is_error=True, subtype="error_during_execution")]
    outcome = parse_claude_stream(lines)
    assert outcome.is_error is True
    assert outcome.subtype == "error_during_execution"


def test_non_string_result_falls_back_to_streamed_text() -> None:
    lines = [
        _init(),
        _assistant_text("recovered"),
        json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": None}),
    ]
    outcome = parse_claude_stream(lines)
    assert outcome.answer == "recovered"


def test_session_id_taken_from_first_event_carrying_it() -> None:
    lines = [
        json.dumps({"type": "system", "subtype": "init"}),
        _assistant_text("hi", session_id="late-session"),
        json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "hi"}),
    ]
    outcome = parse_claude_stream(lines)
    assert outcome.session_id == "late-session"


def test_non_numeric_cost_and_turns_default_safely() -> None:
    lines = [
        _init(),
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "ok",
                "total_cost_usd": "free",
                "num_turns": "many",
            }
        ),
    ]
    outcome = parse_claude_stream(lines)
    assert outcome.cost_usd == 0.0
    assert outcome.num_turns == 0


def test_boolean_is_not_accepted_as_numeric_cost_or_turns() -> None:
    # bool is a subclass of int; the parser must not let True leak in as 1.
    lines = [
        _init(),
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "ok",
                "total_cost_usd": True,
                "num_turns": True,
            }
        ),
    ]
    outcome = parse_claude_stream(lines)
    assert outcome.cost_usd == 0.0
    assert outcome.num_turns == 0


def test_assistant_event_without_content_list_is_ignored() -> None:
    lines = [
        _init(),
        json.dumps({"type": "assistant", "message": {"content": "oops not a list"}}),
        json.dumps({"type": "assistant", "message": "not a dict"}),
        json.dumps({"type": "assistant", "message": {"content": ["not a dict block", 5]}}),
        _result(),
    ]
    outcome = parse_claude_stream(lines)
    assert outcome.answer == "pong"
    assert outcome.rationale == ""


def test_unknown_block_types_are_skipped_without_affecting_answer() -> None:
    # A dict block that is neither thinking nor text (e.g. a tool_use block) is passed over.
    lines = [
        _init(),
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "id": "abc", "name": "Read"},
                        {"type": "text", "text": "done"},
                    ]
                },
            }
        ),
        _result(answer="done"),
    ]
    outcome = parse_claude_stream(lines)
    assert outcome.answer == "done"
    assert outcome.rationale == ""
