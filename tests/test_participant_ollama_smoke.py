# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — gated real smoke test for the headless Ollama participant
"""A gated end-to-end smoke test that drives a real local Ollama turn.

Double-gated like the other provider smokes: it runs only when the ``ollama`` binary is on
``PATH`` **and** ``SYNAPSE_PARTICIPANT_REAL_SMOKE=1`` is set, so the default suite and CI never
spawn a local model. The model is taken from ``SYNAPSE_OLLAMA_SMOKE_MODEL`` (default
``gemma3:1b``, the smallest pulled model) so the smoke stays fast.
"""

from __future__ import annotations

import os
import shutil

import pytest

from synapse_channel.participants.envelope import TurnRequest
from synapse_channel.participants.headless_ollama import OllamaParticipant

_REAL_SMOKE_ENABLED = (
    bool(shutil.which("ollama")) and os.environ.get("SYNAPSE_PARTICIPANT_REAL_SMOKE") == "1"
)

_SKIP_REASON = "set SYNAPSE_PARTICIPANT_REAL_SMOKE=1 with the ollama CLI to run this smoke"

_MODEL = os.environ.get("SYNAPSE_OLLAMA_SMOKE_MODEL", "gemma3:1b")

pytestmark = pytest.mark.skipif(not _REAL_SMOKE_ENABLED, reason=_SKIP_REASON)


async def test_real_ollama_turn_returns_an_answer() -> None:
    seat = OllamaParticipant("SC/ollama-smoke", model=_MODEL, timeout=120.0)
    assert seat.health().available is True

    result = await seat.take_turn(
        TurnRequest(
            topic_id="ollama-smoke",
            prompt="Reply with exactly the single word: pong",
            context="You are a terse test participant.",
        )
    )

    assert result["is_error"] is False, result["reason"]
    assert result["answer"] != ""
    assert "pong" in result["answer"].lower()
    # A local turn carries no provider session token.
    assert result["session"] == ""
    assert result["cost_usd"] == 0.0
