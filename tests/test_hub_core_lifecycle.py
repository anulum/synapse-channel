# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL - end-to-end tests for hub socket lifecycle helpers

from __future__ import annotations

import json

import pytest
from websockets.asyncio.client import connect

from hub_e2e_helpers import close_agents, connect_agent, read_until_type, running_hub
from synapse_channel.core.hub import SynapseHub


async def test_duplicate_name_from_second_socket_is_rejected_end_to_end() -> None:
    async with running_hub() as (_, uri):
        first = await connect_agent("DUP", uri)
        second = await connect_agent("DUP", uri, wait_presence=False)
        try:
            await second.agent.chat("intruder", target="all")
            conflict = await second.recorder.wait_for(lambda m: m.get("type") == "name_conflict")
            assert "already online" in conflict["payload"]
        finally:
            await close_agents(first, second)


async def test_name_switch_on_same_socket_is_rejected_end_to_end() -> None:
    async with running_hub() as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await websocket.send(json.dumps({"sender": "A", "type": "heartbeat"}))
            await read_until_type(websocket, "presence_update")
            await websocket.send(json.dumps({"sender": "B", "type": "chat", "payload": "x"}))
            conflict = await read_until_type(websocket, "name_conflict")
    assert "Sender name switch denied" in conflict["payload"]


async def test_unregister_removes_agent_and_announces_departure_end_to_end() -> None:
    async with running_hub() as (hub, uri):
        alpha = await connect_agent("ALPHA", uri)
        beta = await connect_agent("BETA", uri)
        try:
            await alpha.close()
            left = await beta.recorder.wait_for(
                lambda m: m.get("type") == "presence_update" and m.get("agent") == "ALPHA"
            )
            assert left["event"] == "left"
            assert "ALPHA" not in hub.agent_sockets
        finally:
            await close_agents(beta)


async def test_online_agents_sorted_end_to_end() -> None:
    async with running_hub() as (hub, uri):
        zed = await connect_agent("ZED", uri)
        alpha = await connect_agent("ALPHA", uri)
        try:
            assert hub.online_agents() == ["ALPHA", "ZED"]
        finally:
            await close_agents(zed, alpha)


@pytest.mark.parametrize("seq", [1, 2, 3])
def test_message_seq_is_monotonic(seq: int) -> None:
    hub = SynapseHub(hub_id="syn-test")
    for _ in range(seq):
        value = hub._next_msg_id()
    assert value == seq
