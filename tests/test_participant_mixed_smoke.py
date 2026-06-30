# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — gated real smoke for a cross-provider exchange
"""A gated end-to-end smoke proving two different real providers coordinate in one exchange.

This is the thesis of the Participant Fabric made concrete: a Claude session and a Codex
session, driven as uniform participants, take turns in one exchange — the Codex reactor sees
the Claude opener's typed result framed as data. Double-gated: both the ``claude`` and
``codex`` binaries must be on ``PATH`` and ``SYNAPSE_PARTICIPANT_REAL_SMOKE=1`` set, so the
default suite and CI never spend the two real calls.
"""

from __future__ import annotations

import os
import shutil

import pytest

from synapse_channel.participants.envelope import TurnResult
from synapse_channel.participants.exchange import conduct_exchange
from synapse_channel.participants.headless_claude import HeadlessClaudeParticipant
from synapse_channel.participants.headless_codex import CodexParticipant

_REAL_SMOKE_ENABLED = (
    bool(shutil.which("claude"))
    and bool(shutil.which("codex"))
    and os.environ.get("SYNAPSE_PARTICIPANT_REAL_SMOKE") == "1"
)

_SKIP_REASON = "set SYNAPSE_PARTICIPANT_REAL_SMOKE=1 with both the claude and codex CLIs"

pytestmark = pytest.mark.skipif(not _REAL_SMOKE_ENABLED, reason=_SKIP_REASON)


async def test_claude_and_codex_take_turns_in_one_exchange() -> None:
    opener = HeadlessClaudeParticipant(
        "SC/claude-mixed", model="claude-haiku-4-5-20251001", timeout=120.0
    )
    reactor = CodexParticipant("SC/codex-mixed", timeout=120.0)

    posted: list[TurnResult] = []

    async def collect(result: TurnResult) -> None:
        posted.append(result)

    transcript = await conduct_exchange(
        "In one short sentence, name a benefit of local-first software.",
        opener,
        reactor,
        topic_id="mixed-smoke",
        post=collect,
        shared_context="You are one of two AI participants deliberating. Be brief.",
    )

    assert len(transcript.turns) == 2
    assert transcript.turns[0]["participant"] == "SC/claude-mixed"
    assert transcript.turns[1]["participant"] == "SC/codex-mixed"
    assert transcript.turns[0]["is_error"] is False, transcript.turns[0]["reason"]
    assert transcript.turns[1]["is_error"] is False, transcript.turns[1]["reason"]
    assert transcript.turns[0]["answer"] != ""
    assert transcript.turns[1]["answer"] != ""
    assert [r["participant"] for r in posted] == ["SC/claude-mixed", "SC/codex-mixed"]
