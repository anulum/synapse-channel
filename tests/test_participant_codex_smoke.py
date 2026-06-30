# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — gated real smoke test for the headless Codex participant
"""A gated end-to-end smoke test that drives a real headless Codex turn.

Double-gated like the Claude smoke: it runs only when the ``codex`` binary is on ``PATH``
**and** ``SYNAPSE_PARTICIPANT_REAL_SMOKE=1`` is set, so the default suite and CI never spend a
real Codex call.
"""

from __future__ import annotations

import os
import shutil

import pytest

from synapse_channel.participants.envelope import TurnRequest
from synapse_channel.participants.headless_codex import CodexParticipant

_REAL_SMOKE_ENABLED = (
    bool(shutil.which("codex")) and os.environ.get("SYNAPSE_PARTICIPANT_REAL_SMOKE") == "1"
)

_SKIP_REASON = "set SYNAPSE_PARTICIPANT_REAL_SMOKE=1 with the codex CLI to run this smoke"

pytestmark = pytest.mark.skipif(not _REAL_SMOKE_ENABLED, reason=_SKIP_REASON)


async def test_real_codex_turn_returns_an_answer() -> None:
    seat = CodexParticipant("SC/codex-smoke", timeout=120.0)
    assert seat.health().available is True

    result = await seat.take_turn(
        TurnRequest(
            topic_id="codex-smoke",
            prompt="Reply with exactly the single word: pong",
            context="You are a terse test participant.",
        )
    )

    assert result["is_error"] is False, result["reason"]
    assert result["answer"] != ""
    assert "pong" in result["answer"].lower()
    assert result["session"] != ""
