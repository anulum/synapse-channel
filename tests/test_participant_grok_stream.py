# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the (unverified) Grok streaming-json parser
"""Tests for :mod:`synapse_channel.participants.grok_stream`.

The Grok stream schema is **unverified** (the CLI is not run on this machine), so the parser
delegates to the Claude parser on the assumption that Grok's streaming-json follows the same
Claude-Code-family convention. These tests assert that delegation against Claude-family
fixtures; they do not, and cannot, prove the assumption holds against a real Grok run.
"""

from __future__ import annotations

import json

from synapse_channel.participants.grok_stream import (
    GROK_SCHEMA_VERIFIED,
    parse_grok_stream,
)
from synapse_channel.participants.stream_json import NO_RESULT_SUBTYPE


def test_schema_is_flagged_unverified() -> None:
    # The flag must stay False until a real grok streaming-json trace is captured.
    assert GROK_SCHEMA_VERIFIED is False


def _claude_family_stream(answer: str = "pong", *, session: str = "grok-sess") -> list[str]:
    init = json.dumps({"type": "system", "subtype": "init", "session_id": session})
    assistant = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "thinking", "thinking": "reasoning"},
                    {"type": "text", "text": answer},
                ]
            },
        }
    )
    result = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": answer,
            "session_id": session,
            "total_cost_usd": 0.0,
            "num_turns": 1,
            "stop_reason": "end_turn",
        }
    )
    return [init, assistant, result]


def test_delegates_to_claude_parser_on_a_full_stream() -> None:
    outcome = parse_grok_stream(_claude_family_stream(answer="pong", session="abc"))
    assert outcome.answer == "pong"
    assert outcome.rationale == "reasoning"
    assert outcome.session_id == "abc"
    assert outcome.is_error is False
    assert outcome.subtype == "success"


def test_stream_without_result_is_error() -> None:
    init = json.dumps({"type": "system", "subtype": "init", "session_id": "x"})
    outcome = parse_grok_stream([init])
    assert outcome.is_error is True
    assert outcome.subtype == NO_RESULT_SUBTYPE
