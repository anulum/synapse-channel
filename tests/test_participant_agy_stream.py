# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the Antigravity CLI stream-json parser against real captures
"""Parse Antigravity CLI turns captured from 1.5.10, and every malformed edge."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from synapse_channel.participants.agy_stream import (
    AGY_SCHEMA_VERIFIED,
    NO_ANSWER_SUBTYPE,
    parse_agy_stream,
)
from synapse_channel.participants.stream_json import NO_RESULT_SUBTYPE

_FIXTURES = Path(__file__).parent / "fixtures" / "agy_stream"
_PINNED = {
    "cli_1_5_10_reply.jsonl": "cda6070bb6436198ae05c4d332b50e2c90c21d33e568d57b3a93c3b1e6fc5484",
    "cli_1_5_10_resumed.jsonl": "51825dbe0ed6882784197cc55d294fa66889667c0ba5abf4dd8d5cd81e40c1be",
    "cli_1_5_10_denied_command.jsonl": (
        "678fc01e6977a9a9ddf555bce51883aab59c0d713a3840f427d1fb4188e26e28"
    ),
}


def _capture(name: str) -> list[str]:
    data = (_FIXTURES / name).read_bytes()
    assert hashlib.sha256(data).hexdigest() == _PINNED[name]
    return data.decode("utf-8").splitlines()


def _line(payload: object) -> str:
    return json.dumps(payload)


def _result(**fields: object) -> str:
    return _line({"event": "result", "result": {"conversation_id": "conv-1", **fields}})


def test_schema_flag_is_true_after_real_captures() -> None:
    assert AGY_SCHEMA_VERIFIED is True


def test_a_captured_reply_parses_with_its_conversation_and_usage() -> None:
    outcome = parse_agy_stream(_capture("cli_1_5_10_reply.jsonl"))
    assert outcome.answer == "AGY-OK"
    assert not outcome.is_error
    assert outcome.subtype == "success"
    assert outcome.session_id == "ba770272-6683-4b63-b4ea-2a5e2a065078"
    assert outcome.num_turns == 1
    assert (outcome.input_tokens, outcome.output_tokens) == (15667, 1080)
    assert outcome.stop_reason == "end_turn"


def test_a_captured_resumed_turn_keeps_the_conversation_and_counts_turns() -> None:
    outcome = parse_agy_stream(_capture("cli_1_5_10_resumed.jsonl"))
    assert outcome.answer == "AGY-OK"
    assert outcome.session_id == "ba770272-6683-4b63-b4ea-2a5e2a065078"
    assert outcome.num_turns == 2


def test_a_captured_turn_ended_by_a_denied_command_is_an_error_with_its_tokens() -> None:
    outcome = parse_agy_stream(_capture("cli_1_5_10_denied_command.jsonl"))
    assert outcome.is_error
    assert outcome.answer == ""
    assert outcome.subtype == NO_ANSWER_SUBTYPE
    assert outcome.stop_reason == "the provider produced no answer; denied actions: command"
    assert (outcome.input_tokens, outcome.output_tokens) == (69832, 2827)


def test_a_stream_without_a_result_keeps_the_streamed_text() -> None:
    lines = [
        _line({"event": "init", "conversation_id": "conv-1", "init": {}}),
        _line(
            {
                "event": "step_update",
                "step_update": {"step_type": "agent_response", "text_delta": "partial "},
            }
        ),
        _line({"event": "step_update", "step_update": {"step_type": "tool", "text_delta": "x"}}),
        _line({"event": "step_update", "step_update": "not an object"}),
        _line(
            {
                "event": "step_update",
                "step_update": {"step_type": "agent_response", "text_delta": 7},
            }
        ),
        _line(
            {
                "event": "step_update",
                "step_update": {"step_type": "agent_response", "text_delta": "answer"},
            }
        ),
    ]
    outcome = parse_agy_stream(lines)
    assert outcome.is_error
    assert outcome.subtype == NO_RESULT_SUBTYPE
    assert outcome.answer == "partial answer"
    assert outcome.session_id == "conv-1"


@pytest.mark.parametrize(("status", "subtype"), [("ERROR", "error"), ("", "error")])
def test_a_result_that_is_not_success_is_an_error_with_its_tokens(
    status: str, subtype: str
) -> None:
    outcome = parse_agy_stream(
        [_result(status=status, response="half", usage={"input_tokens": 5, "output_tokens": 2})]
    )
    assert outcome.is_error
    assert outcome.subtype == subtype
    assert outcome.answer == "half"
    assert (outcome.input_tokens, outcome.output_tokens) == (5, 2)


def test_an_empty_success_without_denied_actions_is_no_answer_but_no_error() -> None:
    outcome = parse_agy_stream([_result(status="SUCCESS", response="  \n")])
    assert not outcome.is_error
    assert outcome.answer == ""


def test_denied_actions_are_named_once_in_order_and_malformed_entries_skipped() -> None:
    outcome = parse_agy_stream(
        [
            _result(
                status="SUCCESS",
                response="",
                denied_actions=[
                    {"action": "write"},
                    {"action": "command"},
                    {"action": "command"},
                    {"action": 3},
                    "bare",
                ],
            )
        ]
    )
    assert outcome.stop_reason == "the provider produced no answer; denied actions: command, write"


def test_malformed_denied_actions_mean_none_were_reported() -> None:
    outcome = parse_agy_stream([_result(status="SUCCESS", response="", denied_actions="command")])
    assert not outcome.is_error


@pytest.mark.parametrize(
    "usage",
    [None, "lots", {"input_tokens": True, "output_tokens": "2"}, {}],
)
def test_malformed_usage_and_turns_read_as_zero(usage: object) -> None:
    outcome = parse_agy_stream(
        [_result(status="SUCCESS", response="ok", usage=usage, num_turns=True)]
    )
    assert outcome.answer == "ok"
    assert (outcome.input_tokens, outcome.output_tokens, outcome.num_turns) == (0, 0, 0)


def test_noise_lines_are_skipped_and_the_init_conversation_is_kept() -> None:
    lines = [
        "",
        "not json",
        "[1, 2]",
        _line({"event": "init", "conversation_id": "conv-init", "init": {}}),
        _line({"event": "result", "result": "not an object"}),
        _line({"event": "result", "result": {"status": "SUCCESS", "response": 5}}),
    ]
    outcome = parse_agy_stream(lines)
    assert outcome.session_id == "conv-init"
    assert outcome.answer == ""
    assert not outcome.is_error
