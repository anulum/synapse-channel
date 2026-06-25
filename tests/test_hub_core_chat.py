# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL - end-to-end tests for hub chat, presence, and unknown messages

from __future__ import annotations

import json

from websockets.asyncio.client import connect

from hub_e2e_helpers import (
    close_agents,
    collect_available,
    connect_agent,
    read_until_type,
    running_hub,
)
from synapse_channel.core.hub import SynapseHub


async def test_chat_is_broadcast_and_recorded_end_to_end() -> None:
    async with running_hub(SynapseHub(hub_id="syn-test")) as (hub, uri):
        alpha = await connect_agent("ALPHA", uri)
        beta = await connect_agent("BETA", uri)
        try:
            await alpha.agent.chat("hello", target="all")
            relayed = await beta.recorder.wait_for(
                lambda m: m.get("type") == "chat" and m.get("payload") == "hello"
            )
            assert relayed["hub_id"] == "syn-test"
            assert relayed["msg_id"] == 1
            assert hub.chat_history[-1]["payload"] == "hello"
        finally:
            await close_agents(alpha, beta)


async def test_chat_preserves_supplied_timestamp_and_increments_seq_end_to_end() -> None:
    async with running_hub() as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await websocket.send(
                json.dumps({"sender": "A", "type": "chat", "payload": "1", "timestamp": 1700.0})
            )
            first = await read_until_type(websocket, "chat")
            await websocket.send(json.dumps({"sender": "A", "type": "chat", "payload": "2"}))
            second = await read_until_type(websocket, "chat")
    assert first["timestamp"] == 1700.0
    assert [first["msg_id"], second["msg_id"]] == [1, 2]


async def test_presence_broadcast_on_first_message_end_to_end() -> None:
    async with running_hub() as (_, uri):
        alpha = await connect_agent("ALPHA", uri)
        beta = await connect_agent("BETA", uri)
        try:
            presence = await alpha.recorder.wait_for(
                lambda m: m.get("type") == "presence_update" and m.get("agent") == "BETA"
            )
            assert presence["event"] == "joined"
        finally:
            await close_agents(alpha, beta)


async def test_heartbeat_produces_no_route_reply_end_to_end() -> None:
    async with running_hub() as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await websocket.send(json.dumps({"sender": "A", "type": "heartbeat"}))
            replies = await collect_available(websocket)
    assert [m.get("type") for m in replies if m.get("type") != "presence_update"] == []


async def test_unknown_type_errors_sender_end_to_end() -> None:
    async with running_hub() as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await websocket.send(json.dumps({"sender": "A", "type": "frobnicate"}))
            error = await read_until_type(websocket, "error")
    assert "Unknown message type" in error["payload"]
