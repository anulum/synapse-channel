# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real client tests for per-message authentication envelopes
"""Client-side per-message authentication envelope tests."""

from __future__ import annotations

from client_helpers import connected_recording_agent, wait_for_recorded_count
from synapse_channel.core.message_auth import (
    MessageAuthKey,
    MessageReplayCache,
    VerificationResult,
    verify_frame,
)


async def test_configured_client_signs_mutating_envelopes_only() -> None:
    key = MessageAuthKey(key_id="main", secret=b"shared-secret", senders=frozenset({"ALPHA"}))
    async with connected_recording_agent(
        "ALPHA",
        per_message_auth_key_id="main",
        per_message_auth_secret="shared-secret",
    ) as (agent, messages):
        await agent.chat("hello")
        await agent.claim("T1")
        await wait_for_recorded_count(messages, 3)
        chat, claim = messages[1:]

    assert "auth" not in chat
    assert claim["auth"]["kid"] == "main"
    assert claim["auth"]["alg"] == "hmac-sha256"
    assert isinstance(claim["idem_key"], str)
    assert claim["idem_key"]
    assert (
        verify_frame(
            claim,
            keys={key.key_id: key},
            replay_cache=MessageReplayCache(window_seconds=30.0, max_entries=16),
            now=claim["auth"]["timestamp"],
            required_sender="ALPHA",
        )
        == VerificationResult.OK
    )
