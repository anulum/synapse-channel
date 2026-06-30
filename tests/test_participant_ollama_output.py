# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the ollama run plain-text distiller
"""Tests for :mod:`synapse_channel.participants.ollama_output`.

The fixtures mirror the plain-text output captured from a real ``ollama run`` invocation
(Ollama 0.20.2): stdout is the model's reply with no JSON envelope, no session token, and no
cost.
"""

from __future__ import annotations

from synapse_channel.participants.ollama_output import parse_ollama_output
from synapse_channel.participants.stream_json import NO_RESULT_SUBTYPE


def test_plain_reply_is_the_answer() -> None:
    outcome = parse_ollama_output("PONG\n\n")
    assert outcome.answer == "PONG"
    assert outcome.is_error is False
    assert outcome.subtype == "success"
    assert outcome.rationale == ""
    assert outcome.session_id == ""
    assert outcome.cost_usd == 0.0
    assert outcome.stop_reason == ""


def test_surrounding_whitespace_is_trimmed() -> None:
    outcome = parse_ollama_output("   the answer is 42  \n")
    assert outcome.answer == "the answer is 42"


def test_empty_output_is_error() -> None:
    outcome = parse_ollama_output("")
    assert outcome.is_error is True
    assert outcome.subtype == NO_RESULT_SUBTYPE
    assert outcome.answer == ""


def test_whitespace_only_output_is_error() -> None:
    outcome = parse_ollama_output("  \n\t ")
    assert outcome.is_error is True
    assert outcome.subtype == NO_RESULT_SUBTYPE
    assert outcome.answer == ""
