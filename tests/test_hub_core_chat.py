# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL - end-to-end tests for hub chat, presence, and unknown messages

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from websockets.asyncio.client import connect

from hub_e2e_helpers import (
    close_agents,
    collect_available,
    connect_agent,
    read_until_type,
    running_hub,
)
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore


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


async def test_declared_role_is_bound_reaches_its_holder_and_shows_in_who() -> None:
    # A declares the coordinator role on its registration heartbeat; a directed chat to
    # the role must reach A (delivered, not dead-lettered) and /who must show the binding.
    async with running_hub(SynapseHub(hub_id="syn-test")) as (hub, uri):
        async with connect(uri) as a_ws, connect(uri) as b_ws:
            await read_until_type(a_ws, "welcome")
            await read_until_type(b_ws, "welcome")
            await a_ws.send(
                json.dumps(
                    {
                        "sender": "proj/claude",
                        "type": "heartbeat",
                        "target": "System",
                        "payload": "online",
                        "roles": ["proj/coordinator", "  ", "proj/coordinator"],
                    }
                )
            )
            await a_ws.send(json.dumps({"sender": "proj/claude", "type": "who_request"}))
            who = await read_until_type(a_ws, "who_snapshot")
            # deduplicated, blanks dropped, bound to the declaring identity
            assert who["agent_roles"]["proj/claude"] == ["proj/coordinator"]

            await b_ws.send(
                json.dumps(
                    {"sender": "B", "type": "heartbeat", "target": "System", "payload": "online"}
                )
            )
            await b_ws.send(
                json.dumps(
                    {
                        "sender": "B",
                        "type": "chat",
                        "target": "proj/coordinator",
                        "payload": "role ping",
                        "receipt_requested": True,
                    }
                )
            )
            got = await read_until_type(a_ws, "chat")
            assert got["payload"] == "role ping"
            receipt = await read_until_type(b_ws, "delivery_receipt")
            # a live recipient matched via the role, so it was delivered, not dead-lettered
            assert receipt["delivered"] is True
            assert "proj/claude" in receipt["recipients"]

    assert all(entry["target"] != "proj/coordinator" for entry in hub.dead_letters.snapshot())


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


async def test_chat_with_a_non_numeric_timestamp_is_stamped_not_crashed_end_to_end() -> None:
    # A bare float() on a string timestamp used to raise ValueError out of the frame
    # handler, dropping the sender's connection; the chat must now be broadcast with
    # the hub's own finite clock instead.
    async with running_hub() as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await websocket.send(
                json.dumps(
                    {"sender": "A", "type": "chat", "payload": "x", "timestamp": "not-a-number"}
                )
            )
            frame = await read_until_type(websocket, "chat")
    assert frame["payload"] == "x"
    assert isinstance(frame["timestamp"], (int, float))
    assert math.isfinite(frame["timestamp"])


async def test_chat_with_an_overflowing_numeric_timestamp_is_stamped_finite_end_to_end() -> None:
    # ``1e400`` is a valid JSON number literal that decodes to ``inf`` (bypassing the
    # bareword-constant guard in loads_bounded), so the handler must coerce it to a
    # finite instant rather than broadcasting and journalling a non-finite timestamp.
    async with running_hub() as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            raw_frame = '{"sender": "A", "type": "chat", "payload": "y", "timestamp": 1e400}'
            await websocket.send(raw_frame)
            frame = await read_until_type(websocket, "chat")
    assert frame["payload"] == "y"
    assert isinstance(frame["timestamp"], (int, float))
    assert math.isfinite(frame["timestamp"])


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


async def test_chat_delivery_receipt_reports_matching_online_recipient() -> None:
    async with running_hub() as (_, uri):
        beta = await connect_agent("BETA", uri)
        try:
            async with connect(uri) as websocket:
                await read_until_type(websocket, "welcome")
                await websocket.send(
                    json.dumps(
                        {
                            "sender": "ALPHA",
                            "type": "chat",
                            "target": "BETA",
                            "payload": "hello",
                            "receipt_requested": True,
                        }
                    )
                )
                receipt = await read_until_type(websocket, "delivery_receipt")
        finally:
            await close_agents(beta)

    assert receipt["delivered"] is True
    assert receipt["target"] == "ALPHA"
    assert receipt["message_target"] == "BETA"
    assert receipt["recipients"] == ["BETA"]
    assert receipt["message_id"] == 1


async def test_chat_delivery_receipt_reports_no_online_recipient() -> None:
    async with running_hub() as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await websocket.send(
                json.dumps(
                    {
                        "sender": "ALPHA",
                        "type": "chat",
                        "target": "MISSING",
                        "payload": "hello",
                        "receipt_requested": True,
                    }
                )
            )
            receipt = await read_until_type(websocket, "delivery_receipt")

    assert receipt["delivered"] is False
    assert receipt["message_target"] == "MISSING"
    assert receipt["recipients"] == []
    assert "no online recipient matched MISSING" in receipt["payload"]


async def test_chat_delivery_receipt_preserves_history_bound_and_journal(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(journal=store, max_history=1)
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await websocket.send(json.dumps({"sender": "ALPHA", "type": "chat", "payload": "one"}))
            await read_until_type(websocket, "chat")
            await websocket.send(
                json.dumps(
                    {
                        "sender": "ALPHA",
                        "type": "chat",
                        "target": "MISSING",
                        "payload": "two",
                        "receipt_requested": True,
                    }
                )
            )
            await read_until_type(websocket, "delivery_receipt")

    events = store.read_all()
    store.close()
    assert [message["payload"] for message in hub.chat_history] == ["two"]
    assert [event.kind for event in events] == ["chat", "chat"]


async def _send_chat_frames(websocket: Any, sender: str, items: list[tuple[str, str]]) -> None:
    """Send each (target, payload) as a chat and drain the sender's own echo."""
    for target, text in items:
        await websocket.send(
            json.dumps({"sender": sender, "type": "chat", "target": target, "payload": text})
        )
        await read_until_type(websocket, "chat")


async def test_mailbox_reconnect_replays_missed_directed_from_journal(tmp_path: Path) -> None:
    # ALICE addresses BOB (offline), a broadcast, and CAROL; on reconnect BOB asks for
    # its backlog and the hub replays ONLY the directed-to-BOB chats, in order, marked
    # replayed with a durable seq. The broadcast and CAROL's message are not replayed,
    # and the client-only receipt flag is stripped from the replayed frame.
    store = EventStore(tmp_path / "events.db")
    async with running_hub(SynapseHub(hub_id="mbx", journal=store)) as (_hub, uri):
        async with connect(uri) as alice_ws:
            await read_until_type(alice_ws, "welcome")
            await _send_chat_frames(
                alice_ws,
                "ALICE",
                [("BOB", "m1"), ("all", "bc"), ("BOB", "m2"), ("CAROL", "other"), ("BOB", "m3")],
            )
        async with connect(uri) as bob_ws:
            await read_until_type(bob_ws, "welcome")
            await bob_ws.send(
                json.dumps(
                    {
                        "sender": "BOB",
                        "type": "heartbeat",
                        "target": "System",
                        "payload": "online",
                        "mailbox": True,
                        "since_seq": 0,
                    }
                )
            )
            replayed = [await read_until_type(bob_ws, "chat") for _ in range(3)]
    events = store.read_all()
    store.close()
    assert [frame["payload"] for frame in replayed] == ["m1", "m2", "m3"]
    assert all(frame["replayed"] is True for frame in replayed)
    seqs = [frame["seq"] for frame in replayed]
    assert seqs == sorted(seqs)
    assert set(seqs) <= {event.seq for event in events}
    assert all("receipt_requested" not in frame for frame in replayed)


async def test_mailbox_replay_resumes_after_the_since_seq_cursor(tmp_path: Path) -> None:
    # A live chat frame carries its durable seq; on reconnect BOB passes the last seq
    # it saw and the hub replays only the strictly-later directed message.
    store = EventStore(tmp_path / "events.db")
    async with running_hub(SynapseHub(journal=store)) as (_hub, uri):
        async with connect(uri) as alice_ws:
            await read_until_type(alice_ws, "welcome")
            last_seq = 0
            for text in ["m1", "m2", "m3"]:
                await alice_ws.send(
                    json.dumps(
                        {"sender": "ALICE", "type": "chat", "target": "BOB", "payload": text}
                    )
                )
                echo = await read_until_type(alice_ws, "chat")
                last_seq = echo["seq"]
        async with connect(uri) as bob_ws:
            await read_until_type(bob_ws, "welcome")
            await bob_ws.send(
                json.dumps(
                    {
                        "sender": "BOB",
                        "type": "heartbeat",
                        "target": "System",
                        "payload": "online",
                        "mailbox": True,
                        "since_seq": last_seq - 1,
                    }
                )
            )
            frame = await read_until_type(bob_ws, "chat")
    store.close()
    assert frame["payload"] == "m3"
    assert frame["seq"] == last_seq


async def test_mailbox_replay_is_silent_without_a_journal() -> None:
    # A hub with no durable journal cannot replay; the mailbox heartbeat is a no-op and
    # the next chat BOB sees is its own follow-up broadcast, not a replay of m1.
    async with running_hub(SynapseHub()) as (_hub, uri):
        async with connect(uri) as alice_ws:
            await read_until_type(alice_ws, "welcome")
            await _send_chat_frames(alice_ws, "ALICE", [("BOB", "m1")])
        async with connect(uri) as bob_ws:
            await read_until_type(bob_ws, "welcome")
            await bob_ws.send(
                json.dumps(
                    {
                        "sender": "BOB",
                        "type": "heartbeat",
                        "target": "System",
                        "payload": "online",
                        "mailbox": True,
                        "since_seq": 0,
                    }
                )
            )
            await bob_ws.send(
                json.dumps({"sender": "BOB", "type": "chat", "target": "all", "payload": "ping"})
            )
            frame = await read_until_type(bob_ws, "chat")
    assert frame["payload"] == "ping"


async def test_mailbox_replay_needs_a_literal_true_flag(tmp_path: Path) -> None:
    # A heartbeat without ``mailbox: true`` never replays, even with a journal present.
    store = EventStore(tmp_path / "events.db")
    async with running_hub(SynapseHub(journal=store)) as (_hub, uri):
        async with connect(uri) as alice_ws:
            await read_until_type(alice_ws, "welcome")
            await _send_chat_frames(alice_ws, "ALICE", [("BOB", "m1")])
        async with connect(uri) as bob_ws:
            await read_until_type(bob_ws, "welcome")
            await bob_ws.send(
                json.dumps(
                    {
                        "sender": "BOB",
                        "type": "heartbeat",
                        "target": "System",
                        "payload": "online",
                        "since_seq": 0,
                    }
                )
            )
            await bob_ws.send(
                json.dumps({"sender": "BOB", "type": "chat", "target": "all", "payload": "ping"})
            )
            frame = await read_until_type(bob_ws, "chat")
    store.close()
    assert frame["payload"] == "ping"


async def test_mailbox_replay_includes_a_role_the_reconnecting_agent_holds(tmp_path: Path) -> None:
    # A message addressed to a role is replayed to the agent that declares it holds the
    # role on the same reconnect heartbeat, because roles are bound before the replay.
    store = EventStore(tmp_path / "events.db")
    async with running_hub(SynapseHub(journal=store)) as (_hub, uri):
        async with connect(uri) as alice_ws:
            await read_until_type(alice_ws, "welcome")
            await _send_chat_frames(alice_ws, "ALICE", [("proj/coordinator", "role-msg")])
        async with connect(uri) as bob_ws:
            await read_until_type(bob_ws, "welcome")
            await bob_ws.send(
                json.dumps(
                    {
                        "sender": "proj/bob",
                        "type": "heartbeat",
                        "target": "System",
                        "payload": "online",
                        "roles": ["proj/coordinator"],
                        "mailbox": True,
                        "since_seq": 0,
                    }
                )
            )
            frame = await read_until_type(bob_ws, "chat")
    store.close()
    assert frame["payload"] == "role-msg"


async def test_mailbox_replay_treats_a_malformed_cursor_as_zero(tmp_path: Path) -> None:
    # A non-integer ``since_seq`` degrades to 0 (replay the whole retained window)
    # rather than dropping the socket.
    store = EventStore(tmp_path / "events.db")
    async with running_hub(SynapseHub(journal=store)) as (_hub, uri):
        async with connect(uri) as alice_ws:
            await read_until_type(alice_ws, "welcome")
            await _send_chat_frames(alice_ws, "ALICE", [("BOB", "m1")])
        async with connect(uri) as bob_ws:
            await read_until_type(bob_ws, "welcome")
            await bob_ws.send(
                json.dumps(
                    {
                        "sender": "BOB",
                        "type": "heartbeat",
                        "target": "System",
                        "payload": "online",
                        "mailbox": True,
                        "since_seq": "not-a-number",
                    }
                )
            )
            frame = await read_until_type(bob_ws, "chat")
    store.close()
    assert frame["payload"] == "m1"
