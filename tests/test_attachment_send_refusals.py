# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — client-side attachment send refusals
"""An agent refuses an attachment send it cannot make safely, before any frame leaves."""

from __future__ import annotations

import asyncio

import pytest

from hub_e2e_helpers import running_hub
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.protocol import MessageType


async def test_unknown_type_and_unconnected_agent_are_refused() -> None:
    """A non-attachment verb is refused; so is any send before a hub version is known."""
    agent = SynapseAgent("P/sender", machine_identity=False)
    with pytest.raises(ValueError, match="^unknown attachment request type$"):
        await agent.send_attachment(MessageType.CHAT, payload="not an attachment")
    assert agent.hub_protocol_version is None
    with pytest.raises(
        ValueError, match="^hub does not advertise attachment protocol version four$"
    ):
        await agent.send_attachment(MessageType.ATTACHMENT_BEGIN, scope="proj")


@pytest.mark.real_hub
async def test_connected_agent_without_a_message_key_cannot_send_attachments() -> None:
    """A current hub accepts attachments only signed per message; no key means no send."""
    async with running_hub() as (_hub, uri):
        agent = SynapseAgent("P/sender", uri=uri, machine_identity=False)
        task = asyncio.create_task(agent.connect())
        try:
            assert await agent.wait_until_ready()
            assert (agent.hub_protocol_version or 0) >= 4
            with pytest.raises(ValueError, match="^attachment requests require a per-message key$"):
                await agent.send_attachment(MessageType.ATTACHMENT_BEGIN, scope="proj")
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
