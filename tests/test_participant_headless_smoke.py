# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — gated real smoke test for the headless Claude participant
"""A gated end-to-end smoke test that drives a real headless Claude turn.

This is the one test that spends a real model call, so it is double-gated: it runs only
when the ``claude`` binary is on ``PATH`` **and** ``SYNAPSE_PARTICIPANT_REAL_SMOKE=1`` is
set. The default suite and CI never invoke it; an operator opts in deliberately to confirm
the headless contract still holds against the installed CLI.
"""

from __future__ import annotations

import os
import shutil

import pytest

from synapse_channel.participants.continuity import ContinuitySeat
from synapse_channel.participants.envelope import TurnRequest
from synapse_channel.participants.headless_claude import HeadlessClaudeParticipant

_REAL_SMOKE_ENABLED = (
    bool(shutil.which("claude")) and os.environ.get("SYNAPSE_PARTICIPANT_REAL_SMOKE") == "1"
)

_SKIP_REASON = "set SYNAPSE_PARTICIPANT_REAL_SMOKE=1 with the claude CLI to run this smoke"

pytestmark = pytest.mark.skipif(not _REAL_SMOKE_ENABLED, reason=_SKIP_REASON)


async def test_real_headless_turn_returns_an_answer() -> None:
    seat = HeadlessClaudeParticipant(
        "SC/claude-smoke",
        model="claude-haiku-4-5-20251001",
        timeout=120.0,
    )
    assert seat.health().available is True

    result = await seat.take_turn(
        TurnRequest(
            topic_id="smoke",
            prompt="Reply with exactly the single word: pong",
            context="You are a terse test participant.",
        )
    )

    assert result["is_error"] is False, result["reason"]
    assert result["answer"] != ""
    assert "pong" in result["answer"].lower()
    assert result["session"] != ""


async def test_real_continuity_recalls_across_turns() -> None:
    # Prove --resume actually carries memory: a fact set in turn one is recalled in turn two.
    inner = HeadlessClaudeParticipant(
        "SC/claude-smoke",
        model="claude-haiku-4-5-20251001",
        timeout=120.0,
        persist_session=True,
    )
    seat = ContinuitySeat(inner)

    first = await seat.take_turn(
        TurnRequest(
            topic_id="smoke-mem",
            prompt="Remember this codeword for later: BANANA. Reply only: ok",
            context="You are a terse test participant with memory across turns.",
        )
    )
    assert first["is_error"] is False, first["reason"]
    assert seat.session != ""

    second = await seat.take_turn(
        TurnRequest(
            topic_id="smoke-mem",
            prompt="What was the codeword I gave you? Reply with just the word.",
            context="You are a terse test participant with memory across turns.",
        )
    )
    assert second["is_error"] is False, second["reason"]
    assert "banana" in second["answer"].lower()
