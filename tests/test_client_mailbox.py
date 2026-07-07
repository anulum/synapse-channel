# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — client mailbox mode: reconnect replay, cursor, and deferred-receipt acks

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from websockets.asyncio.client import connect

from client_helpers import connected_recording_agent, wait_for_recorded_count
from hub_e2e_helpers import AgentHandle, Recorder, read_until_type, running_hub
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore


async def _mailbox_agent(name: str, uri: str, *, since_seq: int = 0) -> AgentHandle:
    """Connect a mailbox-mode agent and return its handle once the welcome lands."""
    recorder = Recorder()
    agent = SynapseAgent(
        name,
        recorder,
        uri=uri,
        heartbeat_interval=60.0,
        verbose=False,
        mailbox=True,
        mailbox_since_seq=since_seq,
    )
    task = asyncio.create_task(agent.connect())
    handle = AgentHandle(agent=agent, recorder=recorder, task=task)
    if not await agent.wait_until_ready(3.0):
        await handle.close()
        raise TimeoutError(f"mailbox agent {name} did not receive hub welcome")
    return handle


class TestRegistrationDeclaration:
    async def test_mailbox_agent_declares_mailbox_and_cursor_on_registration(self) -> None:
        async with connected_recording_agent("A", mailbox=True, mailbox_since_seq=7) as (
            _agent,
            messages,
        ):
            await wait_for_recorded_count(messages, 1)
            heartbeat = messages[0]

        assert heartbeat["type"] == "heartbeat"
        assert heartbeat["mailbox"] is True
        assert heartbeat["since_seq"] == 7

    async def test_non_mailbox_agent_registration_omits_mailbox(self) -> None:
        async with connected_recording_agent("A") as (_agent, messages):
            await wait_for_recorded_count(messages, 1)
            heartbeat = messages[0]

        assert "mailbox" not in heartbeat
        assert "since_seq" not in heartbeat


class TestDispatchMailboxTracking:
    async def test_ignores_a_chat_frame_with_no_seq(self) -> None:
        agent = SynapseAgent("A", mailbox=True, mailbox_since_seq=5)
        await agent._dispatch(json.dumps({"type": "chat", "sender": "X", "target": "A"}))
        assert agent.mailbox_cursor == 5

    async def test_ignores_a_boolean_seq(self) -> None:
        agent = SynapseAgent("A", mailbox=True, mailbox_since_seq=5)
        await agent._dispatch(json.dumps({"type": "chat", "sender": "X", "seq": True}))
        assert agent.mailbox_cursor == 5

    async def test_does_not_regress_the_cursor_on_a_lower_seq(self) -> None:
        agent = SynapseAgent("A", mailbox=True, mailbox_since_seq=10)
        await agent._dispatch(json.dumps({"type": "chat", "sender": "X", "target": "A", "seq": 3}))
        assert agent.mailbox_cursor == 10

    async def test_advances_the_cursor_on_a_live_frame(self) -> None:
        # A live (non-replayed) frame advances the cursor without taking the ack path.
        agent = SynapseAgent("A", mailbox=True)
        await agent._dispatch(json.dumps({"type": "chat", "sender": "X", "target": "A", "seq": 4}))
        assert agent.mailbox_cursor == 4

    async def test_a_non_mailbox_agent_never_tracks(self) -> None:
        agent = SynapseAgent("A")
        await agent._dispatch(json.dumps({"type": "chat", "sender": "X", "target": "A", "seq": 9}))
        assert agent.mailbox_cursor == 0


class TestReplayAckAndCursor:
    async def test_mailbox_agent_replays_acks_and_confirms_the_sender(self, tmp_path: Path) -> None:
        # SENDER's receipt-requested message to offline RECIPIENT dead-letters; RECIPIENT
        # comes online as a mailbox agent, is replayed the message, acks it, and SENDER
        # gets a deferred delivery receipt — the full end-to-end reliability path.
        store = EventStore(tmp_path / "events.db")
        async with running_hub(SynapseHub(journal=store)) as (hub, uri):
            async with connect(uri) as sender_ws:
                await read_until_type(sender_ws, "welcome")
                await sender_ws.send(
                    json.dumps(
                        {
                            "sender": "SENDER",
                            "type": "chat",
                            "target": "RECIPIENT",
                            "payload": "while you were out",
                            "receipt_requested": True,
                        }
                    )
                )
                sync = await read_until_type(sender_ws, "delivery_receipt")
                assert sync["delivered"] is False
                assert len(hub.pending_receipts) == 1

                recipient = await _mailbox_agent("RECIPIENT", uri)
                try:
                    replayed = await recipient.recorder.wait_for(
                        lambda m: m.get("type") == "chat" and m.get("replayed") is True
                    )
                    deferred = await read_until_type(sender_ws, "delivery_receipt")
                    cursor = recipient.agent.mailbox_cursor
                finally:
                    await recipient.close()
        store.close()
        assert replayed["payload"] == "while you were out"
        assert deferred["delivered"] is True
        assert deferred["deferred"] is True
        assert deferred["recipients"] == ["RECIPIENT"]
        assert cursor == replayed["seq"]
        assert len(hub.pending_receipts) == 0

    async def test_mailbox_agent_resumes_from_its_seeded_cursor(self, tmp_path: Path) -> None:
        # Two directed messages are journalled; a mailbox agent seeded past the first
        # replays only the second, proving the since_seq cursor is honoured end to end.
        store = EventStore(tmp_path / "events.db")
        async with running_hub(SynapseHub(journal=store)) as (_hub, uri):
            seqs: list[int] = []
            async with connect(uri) as sender_ws:
                await read_until_type(sender_ws, "welcome")
                for text in ["first", "second"]:
                    await sender_ws.send(
                        json.dumps(
                            {
                                "sender": "SENDER",
                                "type": "chat",
                                "target": "RECIPIENT",
                                "payload": text,
                            }
                        )
                    )
                    echo = await read_until_type(sender_ws, "chat")
                    seqs.append(echo["seq"])

            recipient = await _mailbox_agent("RECIPIENT", uri, since_seq=seqs[0])
            try:
                replayed = await recipient.recorder.wait_for(
                    lambda m: m.get("type") == "chat" and m.get("replayed") is True
                )
            finally:
                await recipient.close()
        store.close()
        assert replayed["payload"] == "second"
        assert replayed["seq"] == seqs[1]

    async def test_non_mailbox_agent_is_never_replayed_a_backlog(self, tmp_path: Path) -> None:
        # A plain (non-mailbox) agent connecting after a directed message dead-lettered
        # asks for no replay: the first chat it sees is a live broadcast, not the backlog,
        # and its cursor never advances.
        store = EventStore(tmp_path / "events.db")
        async with running_hub(SynapseHub(journal=store)) as (_hub, uri):
            async with connect(uri) as sender_ws:
                await read_until_type(sender_ws, "welcome")
                await sender_ws.send(
                    json.dumps(
                        {
                            "sender": "SENDER",
                            "type": "chat",
                            "target": "RECIPIENT",
                            "payload": "backlog",
                        }
                    )
                )
                await read_until_type(sender_ws, "chat")

                recorder = Recorder()
                agent = SynapseAgent(
                    "RECIPIENT", recorder, uri=uri, heartbeat_interval=60.0, verbose=False
                )
                task = asyncio.create_task(agent.connect())
                handle = AgentHandle(agent=agent, recorder=recorder, task=task)
                try:
                    assert await agent.wait_until_ready(3.0)
                    await sender_ws.send(
                        json.dumps(
                            {
                                "sender": "SENDER",
                                "type": "chat",
                                "target": "all",
                                "payload": "sentinel",
                            }
                        )
                    )
                    first_chat = await recorder.wait_for(lambda m: m.get("type") == "chat")
                    cursor = agent.mailbox_cursor
                finally:
                    await handle.close()
        store.close()
        assert first_chat["payload"] == "sentinel"
        assert first_chat.get("replayed") is not True
        assert cursor == 0
