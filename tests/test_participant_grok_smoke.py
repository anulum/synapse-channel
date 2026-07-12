# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — gated real smoke test for the headless Grok participant
"""A gated end-to-end smoke test that drives a real headless Grok turn.

The live ``streaming-json`` schema is already verified against a real
``grok`` 0.2.93 capture
(:data:`~synapse_channel.participants.grok_stream.GROK_SCHEMA_VERIFIED` is
``True``), so ``synapse ask --provider grok`` is enabled. This smoke remains
**opt-in** because it spawns a real binary: it requires the ``grok`` binary on
``PATH``, ``SYNAPSE_PARTICIPANT_REAL_SMOKE=1``, and ``SYNAPSE_GROK_SMOKE=1``.
"""

from __future__ import annotations

import os
import shutil

import pytest

from synapse_channel.participants.envelope import TurnRequest
from synapse_channel.participants.grok_stream import GROK_SCHEMA_VERIFIED
from synapse_channel.participants.headless_grok import GrokParticipant

_REAL_SMOKE_ENABLED = (
    bool(shutil.which("grok"))
    and os.environ.get("SYNAPSE_PARTICIPANT_REAL_SMOKE") == "1"
    and os.environ.get("SYNAPSE_GROK_SMOKE") == "1"
    and GROK_SCHEMA_VERIFIED
)

_SKIP_REASON = (
    "Grok participant real smoke is opt-in: requires SYNAPSE_GROK_SMOKE=1, "
    "SYNAPSE_PARTICIPANT_REAL_SMOKE=1, grok on PATH, and GROK_SCHEMA_VERIFIED=True. "
    "The stream schema is already verified against grok 0.2.93; the gate here is "
    "operator consent to spawn a real binary."
)

pytestmark = pytest.mark.skipif(not _REAL_SMOKE_ENABLED, reason=_SKIP_REASON)


async def test_real_grok_turn_returns_an_answer() -> None:
    seat = GrokParticipant("SC/grok-smoke", timeout=120.0)
    assert seat.health().available is True

    result = await seat.take_turn(
        TurnRequest(
            topic_id="grok-smoke",
            prompt="Reply with exactly the single word: pong",
            context="You are a terse test participant.",
        )
    )

    assert result["is_error"] is False, result["reason"]
    assert result["answer"] != ""
    assert "pong" in result["answer"].lower()
