# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — gated real smoke test for the Ollama REST API participant
"""A gated end-to-end smoke test that drives a real local Ollama turn over the REST API.

Double-gated like the other provider smokes: it runs only when the ``ollama`` binary is on
``PATH`` (a proxy for a running local server) **and** ``SYNAPSE_PARTICIPANT_REAL_SMOKE=1`` is set,
so the default suite and CI never make a real request. The model is taken from
``SYNAPSE_OLLAMA_SMOKE_MODEL`` (default ``gemma3:1b``) so the smoke stays fast. Unlike the CLI
smoke, this exercises the HTTP path and asserts the API-reported token counts are captured.
"""

from __future__ import annotations

import os
import shutil

import pytest

from synapse_channel.participants.api_ollama import OllamaApiParticipant
from synapse_channel.participants.envelope import TurnRequest

_REAL_SMOKE_ENABLED = (
    bool(shutil.which("ollama")) and os.environ.get("SYNAPSE_PARTICIPANT_REAL_SMOKE") == "1"
)

_SKIP_REASON = "set SYNAPSE_PARTICIPANT_REAL_SMOKE=1 with a running ollama server to run this smoke"

_MODEL = os.environ.get("SYNAPSE_OLLAMA_SMOKE_MODEL", "gemma3:1b")

pytestmark = pytest.mark.skipif(not _REAL_SMOKE_ENABLED, reason=_SKIP_REASON)


async def test_real_ollama_api_turn_returns_an_answer_and_tokens() -> None:
    seat = OllamaApiParticipant("SC/ollama-api-smoke", model=_MODEL, timeout=120.0)
    assert seat.health().available is True

    result = await seat.take_turn(
        TurnRequest(
            topic_id="ollama-api-smoke",
            prompt="Reply with exactly the single word: pong",
            context="You are a terse test participant.",
        )
    )

    assert result["is_error"] is False, result["reason"]
    assert "pong" in result["answer"].lower()
    assert result["channel"] == "api"
    assert result["model"] == _MODEL
    # The REST endpoint reports token counts, which the Fabric now captures.
    assert result["input_tokens"] > 0
    assert result["output_tokens"] > 0
    assert result["cost_usd"] == 0.0
