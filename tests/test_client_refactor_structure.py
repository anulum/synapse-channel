# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — structural regression tests for the client agent split

from __future__ import annotations

from client_helpers import connected_recording_agent, wait_for_recorded_count
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.client.agent_dispatch import AgentDispatchMixin
from synapse_channel.client.agent_lifecycle import AgentLifecycleMixin
from synapse_channel.client.agent_outbound import AgentOutboundMixin
from synapse_channel.client.agent_queries import AgentQueryMixin


async def test_synapse_agent_mixins_preserve_recording_websocket_surface() -> None:
    async with connected_recording_agent("A") as (agent, messages):
        assert isinstance(agent, SynapseAgent)
        assert isinstance(agent, AgentLifecycleMixin)
        assert isinstance(agent, AgentDispatchMixin)
        assert isinstance(agent, AgentOutboundMixin)
        assert isinstance(agent, AgentQueryMixin)

        await agent.chat("hello", target="B")
        await agent.claim("T1", paths=["src"])
        await agent.request_board()
        await wait_for_recorded_count(messages, 4)

        chat, claim, board = messages[1:]

    assert chat["type"] == "chat"
    assert chat["sender"] == "A"
    assert chat["target"] == "B"
    assert chat["payload"] == "hello"
    assert claim["type"] == "claim"
    assert claim["sender"] == "A"
    assert claim["task_id"] == "T1"
    assert claim["paths"] == ["src"]
    assert board["type"] == "board_request"
    assert board["sender"] == "A"
