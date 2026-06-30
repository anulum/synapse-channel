# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the Ollama REST generate response parser
"""Tests for :mod:`synapse_channel.participants.ollama_api_output`.

The fixture bodies mirror the schema captured from a real Ollama 0.20.2 ``/api/generate`` call
(``stream=false``): ``response`` text, ``prompt_eval_count`` / ``eval_count`` token counts, and a
``done_reason``. The suite asserts the answer and tokens are distilled, and that a missing or blank
response is reported as an error.
"""

from __future__ import annotations

from synapse_channel.participants.ollama_api_output import (
    NO_RESPONSE_SUBTYPE,
    parse_ollama_api_response,
)


def test_parses_a_full_response_body() -> None:
    body = {
        "model": "gemma3:1b",
        "response": "  pong  ",
        "prompt_eval_count": 11,
        "eval_count": 25,
        "done": True,
        "done_reason": "stop",
    }
    outcome = parse_ollama_api_response(body)
    assert outcome.answer == "pong"
    assert outcome.is_error is False
    assert outcome.subtype == "success"
    assert outcome.cost_usd == 0.0
    assert outcome.session_id == ""
    assert outcome.input_tokens == 11
    assert outcome.output_tokens == 25
    assert outcome.stop_reason == "stop"


def test_missing_response_is_an_error() -> None:
    outcome = parse_ollama_api_response({"model": "gemma3:1b", "done": True})
    assert outcome.is_error is True
    assert outcome.subtype == NO_RESPONSE_SUBTYPE
    assert outcome.answer == ""


def test_blank_or_non_string_response_is_an_error() -> None:
    for response in ("   ", 42, None):
        outcome = parse_ollama_api_response({"response": response})
        assert outcome.is_error is True
        assert outcome.subtype == NO_RESPONSE_SUBTYPE


def test_malformed_token_counts_default_to_zero() -> None:
    body = {
        "response": "ok",
        "prompt_eval_count": -3,
        "eval_count": True,
        "done_reason": 99,
    }
    outcome = parse_ollama_api_response(body)
    assert outcome.input_tokens == 0
    assert outcome.output_tokens == 0
    # A non-string done_reason is dropped rather than coerced.
    assert outcome.stop_reason == ""
