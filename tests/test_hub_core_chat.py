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
import time
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
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
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


async def test_bearer_token_is_not_routed_retained_or_journalled(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(
        hub_id="syn-test",
        authenticator=TokenAuthenticator(["s3cret"]),
        journal=store,
    )
    async with running_hub(hub) as (_, uri):
        alpha = await connect_agent("ALPHA", uri, token="s3cret")
        beta = await connect_agent("BETA", uri, token="s3cret")
        try:
            await alpha.agent.send_message(
                "chat",
                target="all",
                payload="credential stays at ingress",
                token="s3cret",
            )
            relayed = await beta.recorder.wait_for(
                lambda message: message.get("payload") == "credential stays at ingress"
            )
            assert "token" not in relayed
            assert "token" not in hub.chat_history[-1]
        finally:
            await close_agents(alpha, beta)

    events = store.read_all()
    store.close()
    assert all("token" not in event.payload for event in events)
    assert all("s3cret" not in json.dumps(event.payload) for event in events)


async def test_a_binary_frame_gets_a_clean_error_and_keeps_the_connection() -> None:
    # A non-UTF-8 binary frame must surface as a clean "Malformed JSON." error, not
    # kill the socket with an unhandled 1011. loads_bounded re-raises the decoder's
    # UnicodeDecodeError as a JSONDecodeError, which the hub's existing decode guard
    # reports as an error while keeping the connection open for the next frame.
    # Open hubs require a name-binding first frame (WF3), so register before the
    # malformed probe — product close 4010 on pre-bind junk is intentional.
    async with running_hub(SynapseHub(hub_id="syn-test")) as (_hub, uri):
        async with connect(uri) as ws:
            await read_until_type(ws, "welcome")
            await ws.send(json.dumps({"sender": "A", "type": "heartbeat", "payload": "online"}))
            await read_until_type(ws, "presence_update")
            await ws.send(b"\xff\xfe\xfa")
            error = await read_until_type(ws, "error")
            assert "Malformed JSON" in error["payload"]
            # The connection survived: a following valid frame is still served.
            await ws.send(
                json.dumps(
                    {
                        "sender": "A",
                        "type": "state_request",
                        "target": "System",
                        "payload": "",
                    }
                )
            )
            snapshot = await read_until_type(ws, "state_snapshot")
            assert snapshot["type"] == "state_snapshot"


async def test_an_oversized_integer_gets_a_clean_error_and_keeps_the_connection() -> None:
    # Open hubs require a name-binding first frame (WF3), so register before the
    # oversized-integer probe — product close 4010 on pre-bind junk is intentional.
    async with running_hub(SynapseHub(hub_id="syn-test")) as (_hub, uri):
        async with connect(uri) as ws:
            await read_until_type(ws, "welcome")
            await ws.send(json.dumps({"sender": "A", "type": "heartbeat", "payload": "online"}))
            await read_until_type(ws, "presence_update")
            await ws.send('{"sender":"A","type":"state_request","n":' + "9" * 5000 + "}")
            error = await read_until_type(ws, "error")
            assert "Malformed JSON" in error["payload"]

            await ws.send(
                json.dumps(
                    {
                        "sender": "A",
                        "type": "state_request",
                        "target": "System",
                        "payload": "",
                    }
                )
            )
            snapshot = await read_until_type(ws, "state_snapshot")
            assert snapshot["type"] == "state_snapshot"


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


async def test_chat_keeps_client_time_advisory_and_stamps_hub_time_end_to_end() -> None:
    """Finite client time is retained as client_timestamp; timestamp is hub time."""
    before = time.time()
    async with running_hub() as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await websocket.send(
                json.dumps({"sender": "A", "type": "chat", "payload": "1", "timestamp": 1700.0})
            )
            first = await read_until_type(websocket, "chat")
            await websocket.send(json.dumps({"sender": "A", "type": "chat", "payload": "2"}))
            second = await read_until_type(websocket, "chat")
    after = time.time()
    assert first["client_timestamp"] == 1700.0
    assert isinstance(first["timestamp"], (int, float))
    assert isinstance(second["timestamp"], (int, float))
    first_ts = float(first["timestamp"])
    second_ts = float(second["timestamp"])
    assert before <= first_ts <= after
    assert first_ts != 1700.0
    assert "client_timestamp" not in second
    assert before <= second_ts <= after
    assert [first["msg_id"], second["msg_id"]] == [1, 2]


async def test_byzantine_future_timestamp_does_not_poison_dead_letter_order_end_to_end() -> None:
    """A future client stamp must not become the dead-letter last_ts ordering key."""
    future = time.time() + 10_000_000.0
    before = time.time()
    async with running_hub() as (hub, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await websocket.send(
                json.dumps(
                    {
                        "sender": "A",
                        "type": "chat",
                        "target": "nobody-online",
                        "payload": "miss",
                        "timestamp": future,
                    }
                )
            )
            frame = await read_until_type(websocket, "chat")
        snapshot = hub.dead_letters.snapshot()
    after = time.time()
    assert frame["client_timestamp"] == future
    assert isinstance(frame["timestamp"], (int, float))
    hub_ts = float(frame["timestamp"])
    assert before <= hub_ts <= after
    entry = next(item for item in snapshot if item["target"] == "nobody-online")
    assert isinstance(entry["last_ts"], (int, float))
    last_ts = float(entry["last_ts"])
    assert before <= last_ts <= after + 1.0
    assert last_ts < future


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
    assert receipt["recipient_wake_capabilities"] == {"BETA": "direct"}
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
    assert [event.kind for event in events if event.kind == EventKind.CHAT] == ["chat", "chat"]
    assert [event.kind for event in events if event.kind.startswith("delivery_receipt_")] == [
        EventKind.DELIVERY_RECEIPT_REQUESTED,
        EventKind.DELIVERY_RECEIPT_IMMEDIATE,
    ]


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


async def test_ack_settles_a_dead_lettered_directed_message_with_a_deferred_receipt(
    tmp_path: Path,
) -> None:
    # ALICE's receipt-requested message to offline BOB is confirmed "not delivered" at once
    # and remembered under its durable seq. BOB reconnects, replays the backlog, and acks the
    # seq; the hub then sends ALICE a deferred receipt revising the verdict to delivered.
    store = EventStore(tmp_path / "events.db")
    async with running_hub(SynapseHub(journal=store)) as (hub, uri):
        async with connect(uri) as alice_ws:
            await read_until_type(alice_ws, "welcome")
            await alice_ws.send(
                json.dumps(
                    {
                        "sender": "ALICE",
                        "type": "chat",
                        "target": "BOB",
                        "payload": "urgent",
                        "receipt_requested": True,
                    }
                )
            )
            sync = await read_until_type(alice_ws, "delivery_receipt")
            assert sync["delivered"] is False
            assert len(hub.pending_receipts) == 1
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
                replayed = await read_until_type(bob_ws, "chat")
                seq = replayed["seq"]
                await bob_ws.send(json.dumps({"sender": "BOB", "type": "ack", "seq": seq}))
                deferred = await read_until_type(alice_ws, "delivery_receipt")
        receipt_events = [
            event for event in store.read_all() if event.kind.startswith("delivery_receipt_")
        ]
    store.close()
    assert deferred["delivered"] is True
    assert deferred["deferred"] is True
    assert deferred["message_id"] == 1
    assert deferred["message_seq"] == seq
    assert deferred["target"] == "ALICE"
    assert deferred["message_target"] == "BOB"
    assert deferred["recipients"] == ["BOB"]
    assert len(hub.pending_receipts) == 0
    assert [event.kind for event in receipt_events] == [
        EventKind.DELIVERY_RECEIPT_REQUESTED,
        EventKind.DELIVERY_RECEIPT_IMMEDIATE,
        EventKind.DELIVERY_RECEIPT_DEFERRED,
    ]
    assert receipt_events[1].payload["delivered"] is False
    assert receipt_events[2].payload["delivered"] is True
    assert receipt_events[2].payload["message_seq"] == seq


async def test_pending_delivery_receipt_survives_restart_and_offline_sender(
    tmp_path: Path,
) -> None:
    # The receipt ledger re-seeds pending deferred receipts on restart. BOB can ack
    # ALICE's replayed message after ALICE has disconnected; the live frame is not
    # delivered, but the final deferred verdict is durable and queryable.
    db = tmp_path / "events.db"
    store = EventStore(db)
    async with running_hub(SynapseHub(journal=store)) as (_hub, uri):
        async with connect(uri) as alice_ws:
            await read_until_type(alice_ws, "welcome")
            await alice_ws.send(
                json.dumps(
                    {
                        "sender": "ALICE",
                        "type": "chat",
                        "target": "BOB",
                        "payload": "restart-safe",
                        "receipt_requested": True,
                    }
                )
            )
            assert (await read_until_type(alice_ws, "delivery_receipt"))["delivered"] is False

    async with running_hub(SynapseHub(journal=store)) as (hub, uri):
        assert len(hub.pending_receipts) == 1
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
            replayed = await read_until_type(bob_ws, "chat")
            await bob_ws.send(json.dumps({"sender": "BOB", "type": "ack", "seq": replayed["seq"]}))
            await collect_available(bob_ws)
        assert len(hub.pending_receipts) == 0

    receipt_events = [
        event for event in store.read_all() if event.kind.startswith("delivery_receipt_")
    ]
    store.close()
    assert receipt_events[-1].kind == EventKind.DELIVERY_RECEIPT_DEFERRED
    assert receipt_events[-1].payload["acked_by"] == "BOB"
    assert receipt_events[-1].payload["sender"] == "ALICE"


async def test_ack_from_a_non_recipient_leaves_the_pending_receipt_for_the_real_one(
    tmp_path: Path,
) -> None:
    # MALLORY acks a seq addressed to BOB. She is not a recipient, so the hub sends her
    # nothing and keeps the entry; BOB's genuine ack still settles it for ALICE — proof the
    # spoof neither fabricated a receipt nor destroyed the one BOB was owed.
    store = EventStore(tmp_path / "events.db")
    async with running_hub(SynapseHub(journal=store)) as (hub, uri):
        async with connect(uri) as alice_ws:
            await read_until_type(alice_ws, "welcome")
            await alice_ws.send(
                json.dumps(
                    {
                        "sender": "ALICE",
                        "type": "chat",
                        "target": "BOB",
                        "payload": "urgent",
                        "receipt_requested": True,
                    }
                )
            )
            echo = await read_until_type(alice_ws, "chat")
            seq = echo["seq"]
            await read_until_type(alice_ws, "delivery_receipt")
            async with connect(uri) as mallory_ws:
                await read_until_type(mallory_ws, "welcome")
                await mallory_ws.send(json.dumps({"sender": "MALLORY", "type": "ack", "seq": seq}))
                spoofed = await collect_available(mallory_ws)
            assert not any(m.get("type") == "delivery_receipt" for m in spoofed)
            assert len(hub.pending_receipts) == 1
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
                await read_until_type(bob_ws, "chat")
                await bob_ws.send(json.dumps({"sender": "BOB", "type": "ack", "seq": seq}))
                deferred = await read_until_type(alice_ws, "delivery_receipt")
    store.close()
    assert deferred["delivered"] is True
    assert deferred["recipients"] == ["BOB"]


async def test_ack_for_an_unknown_seq_sends_no_receipt(tmp_path: Path) -> None:
    # An ack for a seq that was never pending (or already settled) is a silent no-op.
    store = EventStore(tmp_path / "events.db")
    async with running_hub(SynapseHub(journal=store)) as (hub, uri):
        async with connect(uri) as ws:
            await read_until_type(ws, "welcome")
            await ws.send(json.dumps({"sender": "BOB", "type": "ack", "seq": 999}))
            leftover = await collect_available(ws)
    store.close()
    assert not any(m.get("type") == "delivery_receipt" for m in leftover)
    assert len(hub.pending_receipts) == 0


async def test_ack_with_a_malformed_seq_is_ignored(tmp_path: Path) -> None:
    # A boolean or non-integer seq is dropped without a receipt and without dropping the
    # socket — a following chat still round-trips.
    store = EventStore(tmp_path / "events.db")
    async with running_hub(SynapseHub(journal=store)) as (hub, uri):
        async with connect(uri) as ws:
            await read_until_type(ws, "welcome")
            for bad_seq in (True, "not-a-number"):
                await ws.send(json.dumps({"sender": "BOB", "type": "ack", "seq": bad_seq}))
            await ws.send(
                json.dumps({"sender": "BOB", "type": "chat", "target": "all", "payload": "ping"})
            )
            frame = await read_until_type(ws, "chat")
    store.close()
    assert frame["payload"] == "ping"
    assert len(hub.pending_receipts) == 0


async def test_a_directed_message_delivered_live_records_no_pending_receipt(
    tmp_path: Path,
) -> None:
    # A receipt-requested directed message that reaches a live recipient is confirmed at
    # once, so nothing is left pending for a later ack to settle.
    store = EventStore(tmp_path / "events.db")
    async with running_hub(SynapseHub(journal=store)) as (hub, uri):
        bob = await connect_agent("BOB", uri)
        try:
            async with connect(uri) as alice_ws:
                await read_until_type(alice_ws, "welcome")
                await alice_ws.send(
                    json.dumps(
                        {
                            "sender": "ALICE",
                            "type": "chat",
                            "target": "BOB",
                            "payload": "hi",
                            "receipt_requested": True,
                        }
                    )
                )
                receipt = await read_until_type(alice_ws, "delivery_receipt")
            assert receipt["delivered"] is True
            assert len(hub.pending_receipts) == 0
        finally:
            await close_agents(bob)


async def test_a_receipt_requested_broadcast_records_no_pending_receipt(tmp_path: Path) -> None:
    # A broadcast is an audience, never a directed message, so even when it reaches nobody it
    # is not a deferred-receipt candidate — only a directed dead letter is.
    store = EventStore(tmp_path / "events.db")
    async with running_hub(SynapseHub(journal=store)) as (hub, uri):
        async with connect(uri) as ws:
            await read_until_type(ws, "welcome")
            await ws.send(
                json.dumps(
                    {
                        "sender": "ALICE",
                        "type": "chat",
                        "target": "all",
                        "payload": "hi all",
                        "receipt_requested": True,
                    }
                )
            )
            receipt = await read_until_type(ws, "delivery_receipt")
    store.close()
    assert receipt["delivered"] is False
    assert len(hub.pending_receipts) == 0


async def test_mailbox_replay_filters_by_a_declared_mailbox_for_identity(tmp_path: Path) -> None:
    # A wake-listener connects under a receive-only -rx name but declares the bare identity
    # it waits on; the hub replays the backlog directed at that identity, not at the -rx name.
    store = EventStore(tmp_path / "events.db")
    async with running_hub(SynapseHub(journal=store)) as (_hub, uri):
        async with connect(uri) as alice_ws:
            await read_until_type(alice_ws, "welcome")
            await _send_chat_frames(alice_ws, "ALICE", [("BOB", "for-bob")])
        async with connect(uri) as rx_ws:
            await read_until_type(rx_ws, "welcome")
            await rx_ws.send(
                json.dumps(
                    {
                        "sender": "BOB-rx",
                        "type": "heartbeat",
                        "target": "System",
                        "payload": "online",
                        "mailbox": True,
                        "since_seq": 0,
                        "mailbox_for": "BOB",
                    }
                )
            )
            frame = await read_until_type(rx_ws, "chat")
    store.close()
    assert frame["payload"] == "for-bob"
    assert frame["replayed"] is True


async def test_mailbox_for_falls_back_to_the_connection_name_when_not_a_string(
    tmp_path: Path,
) -> None:
    # A non-string mailbox_for is ignored and the replay filters by the connection name,
    # so an agent connecting under its own identity still gets its own backlog.
    store = EventStore(tmp_path / "events.db")
    async with running_hub(SynapseHub(journal=store)) as (_hub, uri):
        async with connect(uri) as alice_ws:
            await read_until_type(alice_ws, "welcome")
            await _send_chat_frames(alice_ws, "ALICE", [("BOB", "own-backlog")])
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
                        "mailbox_for": 123,
                    }
                )
            )
            frame = await read_until_type(bob_ws, "chat")
    store.close()
    assert frame["payload"] == "own-backlog"


async def test_mailbox_for_falls_back_to_the_connection_name_when_blank(tmp_path: Path) -> None:
    # A blank mailbox_for string is treated as absent, so the replay filters by the
    # connection name rather than by an empty identity that matches nothing.
    store = EventStore(tmp_path / "events.db")
    async with running_hub(SynapseHub(journal=store)) as (_hub, uri):
        async with connect(uri) as alice_ws:
            await read_until_type(alice_ws, "welcome")
            await _send_chat_frames(alice_ws, "ALICE", [("BOB", "blank-for")])
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
                        "mailbox_for": "   ",
                    }
                )
            )
            frame = await read_until_type(bob_ws, "chat")
    store.close()
    assert frame["payload"] == "blank-for"


def test_mailbox_recipient_honours_self_sidecar_or_acl_grant() -> None:
    # The replay-authorisation predicate: a mailbox heartbeat may replay its own backlog
    # or, for an -rx wake-listener, the backlog of the identity it is the sidecar of —
    # or when the ACL policy grants the mailbox permission on that agent — never an
    # arbitrary named identity without a grant, and never on a non-string/blank declaration.
    from synapse_channel.core.acl import MAILBOX, AclPolicy, AclRule
    from synapse_channel.core.handlers.messaging import _mailbox_recipient
    from synapse_channel.core.hub import SynapseHub

    # An agent under its own identity replays its own backlog.
    assert _mailbox_recipient("BOB", "BOB") == "BOB"
    # BOB's -rx sidecar may replay BOB's backlog (the documented wake-listener contract).
    assert _mailbox_recipient("BOB-rx", "BOB") == "BOB"
    # An unrelated socket naming another identity is refused: the declaration is dropped
    # and the replay falls back to the connection's own (here different) backlog.
    assert _mailbox_recipient("EVE", "BOB") == "EVE"
    # An -rx sidecar of one identity cannot claim a different identity's backlog either.
    assert _mailbox_recipient("EVE-rx", "BOB") == "EVE-rx"
    # A blank/whitespace declaration is treated as absent.
    assert _mailbox_recipient("BOB", "   ") == "BOB"
    # Non-string declarations are ignored rather than dropping the socket.
    assert _mailbox_recipient("BOB", 123) == "BOB"
    assert _mailbox_recipient("BOB", None) == "BOB"
    # An ACL mailbox grant on the requested agent is the policy-file path for a monitor.
    policy = AclPolicy([AclRule(MAILBOX, "agent", "BOB", "", "monitor bob")])
    hub = SynapseHub(acl_policy=policy)
    assert _mailbox_recipient("EVE", "BOB", hub=hub) == "BOB"
    # Without a matching grant the same hub still refuses.
    assert _mailbox_recipient("EVE", "ALICE", hub=hub) == "EVE"


async def test_mailbox_for_refuses_an_unrelated_identitys_backlog(tmp_path: Path) -> None:
    # The confidentiality boundary: a socket that is neither BOB nor BOB's -rx sidecar
    # cannot pull BOB's directed backlog by naming it in mailbox_for. The declared identity
    # is dropped and the replay falls back to the connection's own (empty) backlog, so no
    # message directed at BOB is ever replayed to EVE.
    store = EventStore(tmp_path / "events.db")
    async with running_hub(SynapseHub(journal=store)) as (_hub, uri):
        async with connect(uri) as alice_ws:
            await read_until_type(alice_ws, "welcome")
            await _send_chat_frames(alice_ws, "ALICE", [("BOB", "for-bob")])
        async with connect(uri) as eve_ws:
            await read_until_type(eve_ws, "welcome")
            await eve_ws.send(
                json.dumps(
                    {
                        "sender": "EVE",
                        "type": "heartbeat",
                        "target": "System",
                        "payload": "online",
                        "mailbox": True,
                        "since_seq": 0,
                        "mailbox_for": "BOB",
                    }
                )
            )
            replayed = await collect_available(eve_ws)
    store.close()
    assert not any(m.get("type") == "chat" for m in replayed)
    assert all(m.get("payload") != "for-bob" for m in replayed)
