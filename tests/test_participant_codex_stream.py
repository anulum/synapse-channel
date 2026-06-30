# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the Codex exec --json parser
"""Tests for :mod:`synapse_channel.participants.codex_stream`.

The fixture lines mirror the schema captured from a real ``codex exec --json`` invocation
(Codex CLI 0.142.4): ``thread.started`` (carrying ``thread_id``), ``turn.started``,
``item.completed`` items (``agent_message`` / ``reasoning``), and the terminal
``turn.completed``.
"""

from __future__ import annotations

import json

from synapse_channel.participants.codex_stream import parse_codex_stream
from synapse_channel.participants.stream_json import NO_RESULT_SUBTYPE


def _thread(thread_id: str = "thread-abc") -> str:
    return json.dumps({"type": "thread.started", "thread_id": thread_id})


def _turn_started() -> str:
    return json.dumps({"type": "turn.started"})


def _agent_message(text: str) -> str:
    return json.dumps(
        {"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": text}}
    )


def _reasoning(text: str) -> str:
    return json.dumps({"type": "item.completed", "item": {"type": "reasoning", "text": text}})


def _turn_completed() -> str:
    return json.dumps({"type": "turn.completed", "usage": {"input_tokens": 20, "output_tokens": 4}})


def test_parses_full_stream() -> None:
    lines = [_thread(), _turn_started(), _agent_message("pong"), _turn_completed()]
    outcome = parse_codex_stream(lines)
    assert outcome.answer == "pong"
    assert outcome.session_id == "thread-abc"
    assert outcome.is_error is False
    assert outcome.subtype == "success"
    assert outcome.cost_usd == 0.0
    assert outcome.stop_reason == "completed"


def test_last_agent_message_is_the_answer() -> None:
    lines = [
        _thread(),
        _agent_message("working on it"),
        _agent_message("final answer"),
        _turn_completed(),
    ]
    outcome = parse_codex_stream(lines)
    assert outcome.answer == "final answer"


def test_reasoning_items_become_rationale() -> None:
    lines = [
        _thread(),
        _reasoning("step one"),
        _reasoning("step two"),
        _agent_message("a"),
        _turn_completed(),
    ]
    outcome = parse_codex_stream(lines)
    assert outcome.rationale == "step one\nstep two"


def test_blank_and_malformed_lines_skipped() -> None:
    lines = ["", "   ", "not json", "[1,2]", _thread(), _agent_message("ok"), _turn_completed()]
    outcome = parse_codex_stream(lines)
    assert outcome.answer == "ok"
    assert outcome.is_error is False


def test_no_completion_and_no_message_is_error() -> None:
    outcome = parse_codex_stream([_thread(), _turn_started()])
    assert outcome.is_error is True
    assert outcome.subtype == NO_RESULT_SUBTYPE
    assert outcome.answer == ""
    assert outcome.session_id == "thread-abc"


def test_completion_without_message_is_not_error_but_empty() -> None:
    outcome = parse_codex_stream([_thread(), _turn_completed()])
    assert outcome.is_error is False
    assert outcome.answer == ""


def test_failure_event_is_reported_as_error() -> None:
    lines = [_thread(), json.dumps({"type": "turn.failed", "error": {"message": "boom"}})]
    outcome = parse_codex_stream(lines)
    assert outcome.is_error is True
    assert outcome.subtype == "turn.failed"


def test_error_typed_event_is_reported() -> None:
    lines = [_thread(), json.dumps({"type": "error", "message": "bad"})]
    outcome = parse_codex_stream(lines)
    assert outcome.is_error is True
    assert outcome.subtype == "error"


def test_item_without_text_or_non_dict_is_skipped() -> None:
    lines = [
        _thread(),
        json.dumps({"type": "item.completed", "item": "not a dict"}),
        json.dumps({"type": "item.completed", "item": {"type": "agent_message"}}),
        json.dumps({"type": "item.completed", "item": {"type": "command_execution", "text": "ls"}}),
        _agent_message("real"),
        _turn_completed(),
    ]
    outcome = parse_codex_stream(lines)
    assert outcome.answer == "real"
    assert outcome.rationale == ""


def test_cost_is_always_zero() -> None:
    # Codex reports usage but no monetary cost; the outcome must not fabricate one.
    outcome = parse_codex_stream([_thread(), _agent_message("x"), _turn_completed()])
    assert outcome.cost_usd == 0.0


def test_thread_started_without_id_leaves_session_empty() -> None:
    lines = [
        json.dumps({"type": "thread.started"}),
        _agent_message("ok"),
        _turn_completed(),
    ]
    outcome = parse_codex_stream(lines)
    assert outcome.session_id == ""
    assert outcome.answer == "ok"


def test_usage_tokens_are_captured_from_turn_completed() -> None:
    # The terminal usage block must be recorded so the Fabric can account for it.
    outcome = parse_codex_stream([_thread(), _agent_message("x"), _turn_completed()])
    assert outcome.input_tokens == 20
    assert outcome.output_tokens == 4


def test_usage_missing_or_malformed_yields_zero_tokens() -> None:
    no_usage = json.dumps({"type": "turn.completed"})
    non_dict = json.dumps({"type": "turn.completed", "usage": "lots"})
    bad_counts = json.dumps(
        {"type": "turn.completed", "usage": {"input_tokens": -5, "output_tokens": True}}
    )
    for completed in (no_usage, non_dict, bad_counts):
        outcome = parse_codex_stream([_thread(), _agent_message("x"), completed])
        assert outcome.input_tokens == 0
        assert outcome.output_tokens == 0
