# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — gated real smoke test for the headless Grok participant
"""A gated end-to-end smoke test that would drive a real headless Grok turn.

**Triple-gated and not run here on purpose.** Grok support is ready, but not recommended until
xAI ships a stable Grok CLI: the CLI is not yet stable, so its output schema is unverified
(:data:`~synapse_channel.participants.grok_stream.GROK_SCHEMA_VERIFIED` is ``False``). On top of
the usual two gates — the ``grok`` binary on ``PATH`` and ``SYNAPSE_PARTICIPANT_REAL_SMOKE=1`` —
this smoke also requires ``SYNAPSE_GROK_SMOKE=1`` so it stays skipped even in the environment
that runs the other providers' real smokes. Run it, and use what it prints, only once a stable
Grok CLI is available and the stream schema has been captured and verified at source; until then
it is the test that confirms the Grok schema is still unverified.
"""

from __future__ import annotations

import os
import shutil

import pytest

from synapse_channel.participants.envelope import TurnRequest
from synapse_channel.participants.headless_grok import GrokParticipant

_REAL_SMOKE_ENABLED = (
    bool(shutil.which("grok"))
    and os.environ.get("SYNAPSE_PARTICIPANT_REAL_SMOKE") == "1"
    and os.environ.get("SYNAPSE_GROK_SMOKE") == "1"
)

_SKIP_REASON = (
    "Grok is ready but not recommended until xAI ships a stable Grok CLI: set "
    "SYNAPSE_GROK_SMOKE=1 *and* SYNAPSE_PARTICIPANT_REAL_SMOKE=1 with a stable grok CLI, and "
    "verify the stream schema at source, before trusting this smoke"
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
