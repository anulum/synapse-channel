# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — gated real smoke test for the headless Kimi participant
"""A gated end-to-end smoke test that drives a real headless Kimi turn.

Double-gated like the Claude and Codex smokes: it runs only when the ``kimi`` binary is on
``PATH`` **and** ``SYNAPSE_PARTICIPANT_REAL_SMOKE=1`` is set, so the default suite and CI never
spend a real Kimi call. The turn runs in read-only plan mode (the participant's default), so it
cannot modify the workspace, and a second resumed turn proves continuity recalls the first.
"""

from __future__ import annotations

import os
import shutil

import pytest

from synapse_channel.participants.envelope import TurnRequest
from synapse_channel.participants.headless_kimi import KimiParticipant

_REAL_SMOKE_ENABLED = (
    bool(shutil.which("kimi")) and os.environ.get("SYNAPSE_PARTICIPANT_REAL_SMOKE") == "1"
)

_SKIP_REASON = "set SYNAPSE_PARTICIPANT_REAL_SMOKE=1 with the kimi CLI to run this smoke"

pytestmark = pytest.mark.skipif(not _REAL_SMOKE_ENABLED, reason=_SKIP_REASON)


async def test_real_kimi_turn_returns_an_answer() -> None:
    seat = KimiParticipant("SC/kimi-smoke", timeout=120.0)
    assert seat.health().available is True

    result = await seat.take_turn(
        TurnRequest(
            topic_id="kimi-smoke",
            prompt="Reply with exactly the single word: pong",
            context="You are a terse test participant.",
        )
    )

    assert result["is_error"] is False, result["reason"]
    assert result["answer"] != ""
    assert "pong" in result["answer"].lower()
    assert result["session"] != ""


async def test_real_kimi_session_resumes() -> None:
    seat = KimiParticipant("SC/kimi-smoke", timeout=120.0)

    first = await seat.take_turn(
        TurnRequest(
            topic_id="kimi-resume",
            prompt="Remember the codeword GANYMEDE. Reply with just: ok",
        )
    )
    assert first["is_error"] is False, first["reason"]
    assert first["session"] != ""

    second = await seat.take_turn(
        TurnRequest(
            topic_id="kimi-resume",
            prompt="What codeword did I ask you to remember? Reply with just that word.",
            resume_session=first["session"],
        )
    )
    assert second["is_error"] is False, second["reason"]
    assert "ganymede" in second["answer"].lower()
