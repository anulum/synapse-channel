# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a suite identity's connection and its bounded waits
"""A suite session refuses an absent hub and ends every wait by its deadline, with words."""

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel.core.protocol import MessageType
from synapse_channel.effect_suite.session import HubSession, SuiteSessionError
from synapse_channel.effect_suite.workspace import suite_hub


async def test_a_hub_that_never_welcomes_is_refused_and_the_connection_is_stopped() -> None:
    session = HubSession("SUITE/lonely", "ws://127.0.0.1:1", answer_seconds=0.2)
    with pytest.raises(SuiteSessionError) as refused:
        await session.__aenter__()
    assert str(refused.value) == "the hub did not welcome SUITE/lonely within 0.2 seconds"
    assert refused.value.code == "effect_suite_session"
    assert session.agent.running is False
    never_entered = HubSession("SUITE/idle", "ws://127.0.0.1:1")
    await never_entered.__aexit__(None, None, None)
    assert never_entered.agent.running is False


async def test_waits_take_only_later_frames_and_end_by_their_deadline(tmp_path: Path) -> None:
    async with suite_hub(tmp_path) as hub:
        async with HubSession("SUITE/waiter", hub.uri, answer_seconds=0.3) as session:
            welcomed = session.mark()
            assert welcomed >= 1
            with pytest.raises(SuiteSessionError) as earlier:
                await session.wait_after(
                    welcomed, lambda frame: frame.get("type") == "welcome", what="a welcome"
                )
            assert str(earlier.value) == "SUITE/waiter did not receive a welcome within 0.3 seconds"
            granted = await session.after(
                lambda: session.agent.claim("suite-wait", note="test"),
                lambda frame: frame.get("type") == MessageType.CLAIM_GRANTED,
                what="a claim grant",
            )
            assert granted["task_id"] == "suite-wait"
            with pytest.raises(SuiteSessionError) as fewer:
                await session.wait_count(
                    0,
                    lambda frame: frame.get("type") == MessageType.CLAIM_GRANTED,
                    2,
                    what="the claim grants",
                )
            assert str(fewer.value) == (
                "SUITE/waiter did not receive 2 of the claim grants within 0.3 seconds"
            )
