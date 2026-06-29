# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — private-channel runtime history and projections tests

from __future__ import annotations

import json
from pathlib import Path

import pytest
from websockets.asyncio.client import connect

from hub_e2e_helpers import close_agents, connect_agent, read_until_type, running_hub
from synapse_channel import cli_channels
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.relay import decode_lite, read_jsonl_since


async def test_private_channel_chat_has_member_history_journal_and_relay(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "events.db")
    relay_log = tmp_path / "relay.ndjson"
    hub = SynapseHub(journal=store, relay_log=relay_log, max_history=2)
    async with running_hub(hub) as (_, uri):
        alice = await connect_agent("alice", uri)
        bob = await connect_agent("bob", uri)
        try:
            await alice.agent.channel_create("ops")
            await alice.recorder.wait_for(
                lambda item: item.get("type") == "channel_result" and item.get("ok") is True
            )
            await bob.agent.channel_join("ops")
            await bob.recorder.wait_for(
                lambda item: item.get("type") == "channel_result" and item.get("ok") is True
            )

            for payload in ("one", "two", "three"):
                await alice.agent.chat(payload, channel="ops")
            third = await bob.recorder.wait_for(
                lambda item: item.get("type") == "chat" and item.get("payload") == "three"
            )
        finally:
            await close_agents(alice, bob)

    events = store.read_all()
    store.close()
    relay_rows, _ = read_jsonl_since(relay_log, 0)

    assert third["channel"] == "ops"
    assert hub.chat_history == []
    assert [item["payload"] for item in hub.channels.history_for("ops", "bob")] == [
        "two",
        "three",
    ]
    assert [event.kind for event in events] == [EventKind.CHAT, EventKind.CHAT, EventKind.CHAT]
    assert [event.payload["channel"] for event in events] == ["ops", "ops", "ops"]
    decoded = [decode_lite(row) for row in relay_rows]
    assert [row["channel"] for row in decoded if row.get("type") == "chat"] == [
        "ops",
        "ops",
        "ops",
    ]


async def test_channel_history_cli_only_returns_history_to_members(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with running_hub(SynapseHub(max_history=5)) as (_hub, uri):
        alice = await connect_agent("alice", uri)
        try:
            await alice.agent.channel_create("ops")
            await alice.recorder.wait_for(
                lambda item: item.get("type") == "channel_result" and item.get("ok") is True
            )
            await alice.agent.chat("visible to members", channel="ops")
            await close_agents(alice)

            member = await cli_channels._run_channel_command(
                uri=uri,
                name="alice",
                token=None,
                command="history",
                channel="ops",
                label="",
                ready_timeout=2.0,
                response_timeout=2.0,
                limit=10,
            )
            member_out = capsys.readouterr().out
            stranger = await cli_channels._run_channel_command(
                uri=uri,
                name="carol",
                token=None,
                command="history",
                channel="ops",
                label="",
                ready_timeout=2.0,
                response_timeout=2.0,
                limit=10,
            )
            stranger_out = capsys.readouterr().out
        finally:
            await close_agents(alice)

    assert member == 0
    assert "alice: visible to members" in member_out
    assert stranger == 1
    assert "not a member of channel 'ops'" in stranger_out
    assert "visible to members" not in stranger_out


async def test_channel_history_request_uses_real_wire_surface() -> None:
    async with running_hub(SynapseHub(max_history=5)) as (_hub, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await websocket.send(
                json.dumps(
                    {
                        "sender": "alice",
                        "type": "channel_create",
                        "channel": "ops",
                    }
                )
            )
            await read_until_type(websocket, "channel_result")
            await websocket.send(
                json.dumps(
                    {
                        "sender": "alice",
                        "type": "chat",
                        "channel": "ops",
                        "payload": "wire note",
                    }
                )
            )
            await websocket.send(
                json.dumps(
                    {
                        "sender": "alice",
                        "type": "channel_history_request",
                        "channel": "ops",
                        "limit": 5,
                    }
                )
            )
            history = await read_until_type(websocket, "channel_history")

    assert history["channel"] == "ops"
    assert [item["payload"] for item in history["messages"]] == ["wire note"]
