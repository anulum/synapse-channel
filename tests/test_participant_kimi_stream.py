# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the Kimi --print stream-json parser
"""Tests for :mod:`synapse_channel.participants.kimi_stream`.

The fixture lines mirror the schema captured from a real ``kimi --print --output-format
stream-json`` invocation (Kimi CLI 1.47.0): each stdout line is one assistant message whose
``content`` is either a plain reply string or a list of ``think`` / ``text`` blocks, and the
resume session id is reported on stderr as ``To resume this session: kimi -r <id>``.
"""

from __future__ import annotations

import json

from synapse_channel.participants.kimi_stream import (
    extract_kimi_session,
    parse_kimi_stream,
)
from synapse_channel.participants.stream_json import NO_RESULT_SUBTYPE

_STDERR = "\nTo resume this session: kimi -r 95bfe0bc-5261-4241-9a3c-52865bccedc2\n"


def _assistant(content: object) -> str:
    return json.dumps({"role": "assistant", "content": content})


def _blocks(*, think: str | None = None, text: str | None = None) -> list[dict[str, object]]:
    blocks: list[dict[str, object]] = []
    if think is not None:
        blocks.append({"type": "think", "think": think, "encrypted": None})
    if text is not None:
        blocks.append({"type": "text", "text": text})
    return blocks


def test_string_content_is_the_answer() -> None:
    outcome = parse_kimi_stream([_assistant("PONG")], stderr=_STDERR)
    assert outcome.answer == "PONG"
    assert outcome.rationale == ""
    assert outcome.is_error is False
    assert outcome.subtype == "success"
    assert outcome.cost_usd == 0.0
    assert outcome.session_id == "95bfe0bc-5261-4241-9a3c-52865bccedc2"


def test_block_content_splits_reasoning_from_reply() -> None:
    line = _assistant(_blocks(think="the user wants PONG", text="PONG"))
    outcome = parse_kimi_stream([line], stderr=_STDERR)
    assert outcome.answer == "PONG"
    assert outcome.rationale == "the user wants PONG"


def test_multiple_text_blocks_in_one_message_join() -> None:
    line = _assistant([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}])
    outcome = parse_kimi_stream([line])
    assert outcome.answer == "ab"


def test_last_non_empty_message_is_the_answer() -> None:
    lines = [
        _assistant(_blocks(think="planning")),  # no text → does not overwrite the answer
        _assistant("working"),
        _assistant("final"),
    ]
    outcome = parse_kimi_stream(lines)
    assert outcome.answer == "final"
    assert outcome.rationale == "planning"


def test_think_blocks_accumulate_across_messages() -> None:
    lines = [
        _assistant(_blocks(think="step one")),
        _assistant(_blocks(think="step two", text="done")),
    ]
    outcome = parse_kimi_stream(lines)
    assert outcome.rationale == "step one\nstep two"
    assert outcome.answer == "done"


def test_non_dict_block_is_skipped() -> None:
    line = _assistant(["not a block", {"type": "text", "text": "kept"}])
    outcome = parse_kimi_stream([line])
    assert outcome.answer == "kept"


def test_unknown_block_type_is_ignored() -> None:
    line = _assistant([{"type": "tool_use", "name": "shell"}, {"type": "text", "text": "real"}])
    outcome = parse_kimi_stream([line])
    assert outcome.answer == "real"
    assert outcome.rationale == ""


def test_block_with_non_string_payload_is_ignored() -> None:
    line = _assistant([{"type": "text", "text": 42}, {"type": "think", "think": None}])
    outcome = parse_kimi_stream([line])
    # The message is an assistant message (not an error) but carries no usable text.
    assert outcome.answer == ""
    assert outcome.rationale == ""
    assert outcome.is_error is False


def test_non_string_non_list_content_yields_empty_answer() -> None:
    outcome = parse_kimi_stream([_assistant(42)])
    assert outcome.answer == ""
    assert outcome.is_error is False


def test_blank_and_malformed_lines_skipped() -> None:
    lines = ["", "   ", "not json", "[1, 2]", _assistant("ok")]
    outcome = parse_kimi_stream(lines, stderr=_STDERR)
    assert outcome.answer == "ok"
    assert outcome.is_error is False


def test_no_assistant_message_is_error() -> None:
    outcome = parse_kimi_stream([""], stderr="")
    assert outcome.is_error is True
    assert outcome.subtype == NO_RESULT_SUBTYPE
    assert outcome.answer == ""
    assert outcome.session_id == ""


def test_role_error_line_is_reported_as_error() -> None:
    lines = [_assistant("partial"), json.dumps({"role": "error", "message": "boom"})]
    outcome = parse_kimi_stream(lines)
    assert outcome.is_error is True
    assert outcome.subtype == "error"


def test_typed_error_line_uses_its_type_as_subtype() -> None:
    lines = [json.dumps({"type": "error_event", "message": "bad"})]
    outcome = parse_kimi_stream(lines)
    assert outcome.is_error is True
    assert outcome.subtype == "error_event"


def test_non_assistant_non_error_line_is_ignored() -> None:
    lines = [json.dumps({"role": "system", "content": "noise"}), _assistant("answer")]
    outcome = parse_kimi_stream(lines)
    assert outcome.answer == "answer"
    assert outcome.is_error is False


def test_cost_is_always_zero() -> None:
    # Kimi reports no monetary cost; the outcome must not fabricate one.
    outcome = parse_kimi_stream([_assistant("x")])
    assert outcome.cost_usd == 0.0


def test_extract_session_returns_last_match() -> None:
    stderr = "kimi -r first-id\nsome noise\nTo resume this session: kimi -r second-id\n"
    assert extract_kimi_session(stderr) == "second-id"


def test_extract_session_absent_is_empty() -> None:
    assert extract_kimi_session("no resume hint here") == ""
