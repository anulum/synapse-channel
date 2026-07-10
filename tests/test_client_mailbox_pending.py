# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — client mailbox watermark ACK tests

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.protocol import MessageType


async def _ignore(_data: dict[str, Any]) -> None:
    """Discard callback-visible frames in dispatch tests."""


async def test_accepted_live_mailbox_frame_acks_the_logical_identity() -> None:
    agent = SynapseAgent(
        "PROJ/A-rx",
        _ignore,
        mailbox=True,
        mailbox_for="PROJ/A",
        mailbox_advance=lambda _data: True,
    )
    agent.hub_protocol_version = 2
    sender = AsyncMock()
    agent.send_message = sender  # type: ignore[method-assign]

    await agent._dispatch(
        json.dumps(
            {
                "type": "chat",
                "sender": "PEER",
                "target": "PROJ/A",
                "payload": "work",
                "seq": 7,
            }
        )
    )

    assert agent.mailbox_cursor == 7
    sender.assert_awaited_once_with(MessageType.ACK, seq=7, mailbox_for="PROJ/A")


async def test_refused_mailbox_frame_neither_advances_nor_acks() -> None:
    agent = SynapseAgent(
        "PROJ/A-rx",
        _ignore,
        mailbox=True,
        mailbox_since_seq=3,
        mailbox_for="PROJ/A",
        mailbox_advance=lambda _data: False,
    )
    agent.hub_protocol_version = 2
    sender = AsyncMock()
    agent.send_message = sender  # type: ignore[method-assign]

    await agent._dispatch(
        json.dumps(
            {
                "type": "chat",
                "sender": "PEER",
                "target": "all",
                "payload": "routine",
                "seq": 8,
            }
        )
    )

    assert agent.mailbox_cursor == 3
    sender.assert_not_awaited()


async def test_old_hub_withholds_additive_ack_but_keeps_local_cursor() -> None:
    agent = SynapseAgent("PROJ/A", _ignore, mailbox=True)
    agent.hub_protocol_version = 1
    sender = AsyncMock()
    agent.send_message = sender  # type: ignore[method-assign]

    await agent._dispatch(
        json.dumps(
            {
                "type": "chat",
                "sender": "PEER",
                "target": "PROJ/A",
                "payload": "work",
                "seq": 4,
            }
        )
    )

    assert agent.mailbox_cursor == 4
    sender.assert_not_awaited()


async def test_direct_ack_omits_blank_mailbox_identity() -> None:
    agent = SynapseAgent("PROJ/A", _ignore)
    agent.hub_protocol_version = 2
    sender = AsyncMock()
    agent.send_message = sender  # type: ignore[method-assign]

    assert await agent.ack(9) is True

    sender.assert_awaited_once_with(MessageType.ACK, seq=9)
