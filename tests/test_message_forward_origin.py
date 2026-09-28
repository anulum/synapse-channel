# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — origin hub against peers that fail, refuse, vanish or answer badly
"""An origin hub never reports more than its peer proved, whatever the peer does.

A current hub always answers a well-formed forward correctly, so the answers that only an
older, faulty or absent peer produces come from a scripted peer on a real loopback socket
(:mod:`message_forward_peer_helpers`) or from a port nothing listens on. The origin hub is a
real hub served on loopback and its agents are real websocket clients.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.client import ClientConnection, connect

from hub_e2e_helpers import _await_listening, _free_port, read_until_type
from message_forward_peer_helpers import error_frame, result_frame, scripted_peer
from synapse_channel.core.handlers.messaging import ChatRouting
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.message_forward_origin import (
    message_forward_retry_loop,
    retry_delay,
    run_forward_retries,
)
from synapse_channel.core.message_forward_transport import MessageForwardPeer, forward_message
from synapse_channel.core.message_forward_wire import MessageForwardRequest
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType

_READ_LIMIT = 60


@contextlib.asynccontextmanager
async def _served(hub: SynapseHub) -> AsyncIterator[str]:
    port = _free_port()
    task = asyncio.create_task(hub.serve("localhost", port))
    await _await_listening(port)
    try:
        yield f"ws://localhost:{port}"
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def _agent(uri: str, name: str) -> ClientConnection:
    websocket = await connect(uri)
    await read_until_type(websocket, MessageType.WELCOME)
    await websocket.send(
        json.dumps(
            {
                "sender": name,
                "type": MessageType.HEARTBEAT,
                "target": "System",
                "payload": "online",
                "protocol_version": 3,
            }
        )
    )
    return websocket


async def _chat(websocket: ClientConnection, target: str, *, receipt: bool = True) -> None:
    frame: dict[str, Any] = {
        "sender": "PROJ/alice",
        "type": MessageType.CHAT,
        "target": target,
        "payload": "hi",
    }
    if receipt:
        frame["receipt_requested"] = True
    await websocket.send(json.dumps(frame))


async def _receipt(websocket: ClientConnection) -> dict[str, Any]:
    return await read_until_type(websocket, MessageType.DELIVERY_RECEIPT, limit=_READ_LIMIT)


def _unreachable() -> MessageForwardPeer:
    return MessageForwardPeer(uri=f"ws://localhost:{_free_port()}")


def test_retry_delay_doubles_and_is_capped() -> None:
    """Backoff is 1, 2, 4 ... seconds and never exceeds five minutes."""
    assert [retry_delay(n) for n in (0, 1, 2, 3)] == [1.0, 1.0, 2.0, 4.0]
    assert retry_delay(10_000) == 300.0


async def test_a_peer_that_does_not_know_the_frame_settles_the_chat_as_refused() -> None:
    """An older peer answers with an error frame; retrying cannot help, so the chat settles."""
    async with scripted_peer(error_frame) as peer:
        hub = SynapseHub(
            hub_id="workstation", message_peers={"laptop": MessageForwardPeer(peer.uri)}
        )
        async with _served(hub) as uri:
            alice = await _agent(uri, "PROJ/alice")
            try:
                await _chat(alice, "PROJ/bob@laptop")
                receipt = await _receipt(alice)
            finally:
                await alice.close()
    assert receipt["forward_state"] == "refused"
    assert receipt["reason"] == "forward_refused"
    assert "peer_rejected" in receipt["payload"]
    assert "client_msg_id" not in receipt
    assert "message_seq" not in receipt
    assert hub.message_forward_ledger.pending_counts() == {}


async def test_unanswered_forwards_stay_pending_until_the_peer_is_removed() -> None:
    """A failed retry keeps the entry; a peer dropped from the config settles it as refused."""
    hub = SynapseHub(hub_id="workstation", message_peers={"laptop": _unreachable()})
    async with _served(hub) as uri:
        alice = await _agent(uri, "PROJ/alice")
        try:
            await _chat(alice, "PROJ/bob@laptop", receipt=False)
            for _ in range(200):
                if hub.message_forward_ledger.pending_counts():
                    break
                await asyncio.sleep(0.01)
            assert hub.message_forward_ledger.pending_counts() == {"laptop": 1}
            assert await run_forward_retries(hub, now=time.time() + 3600) == 0
            (entry,) = hub.message_forward_ledger.due(time.time() + 7200)
            assert entry.attempts == 2
            assert "failed" in entry.result["last_error"]

            hub.message_peers = {}
            assert await run_forward_retries(hub, now=time.time() + 7200) == 1
            settled = hub.message_forward_ledger.outbox_entry(entry.forward_id)
        finally:
            await alice.close()
    assert settled is not None
    assert settled.state == "refused"
    assert settled.result["reason_code"] == "peer_not_configured"
    assert hub.message_forward_ledger.pending_sender_notifications("PROJ/alice") == []


async def test_the_retry_loop_delivers_once_the_peer_answers() -> None:
    """The background sweep retries on its own and tells the online sender what settled."""
    hub = SynapseHub(hub_id="workstation", message_peers={"laptop": _unreachable()})
    async with _served(hub) as uri:
        alice = await _agent(uri, "PROJ/alice")
        try:
            await _chat(alice, "PROJ/bob@laptop")
            first = await _receipt(alice)
            assert first["forward_state"] == "pending"
            async with scripted_peer(
                lambda frame: result_frame(
                    frame["forward_id"],
                    result={"delivered": True, "recipients": ["PROJ/bob"]},
                )
            ) as peer:
                hub.message_peers = {"laptop": MessageForwardPeer(peer.uri)}
                loop = asyncio.create_task(message_forward_retry_loop(hub, interval=0.01))
                try:
                    final = await _receipt(alice)
                finally:
                    loop.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await loop
        finally:
            await alice.close()
    assert final["forward_state"] == "accepted"
    assert final["recipients"] == ["PROJ/bob@laptop"]


async def test_a_malformed_roster_answer_is_filtered_not_trusted() -> None:
    """Only well-formed remote names and sessions reach the requester, all hub-qualified."""
    answer = {
        "online_agents": "PROJ/bob",
        "delivery_sessions": {
            "PROJ/bob": {"incarnation": "i-1", "capabilities": {}},
            "PROJ/eve": "not a session",
            "": {"incarnation": "i-2"},
        },
    }
    async with scripted_peer(
        lambda frame: result_frame(frame["forward_id"], result=answer)
    ) as peer:
        hub = SynapseHub(
            hub_id="workstation", message_peers={"laptop": MessageForwardPeer(peer.uri)}
        )
        async with _served(hub) as uri:
            alice = await _agent(uri, "PROJ/alice")
            try:
                await alice.send(
                    json.dumps(
                        {"sender": "PROJ/alice", "type": MessageType.WHO_REQUEST, "hub": "laptop"}
                    )
                )
                roster = await read_until_type(alice, MessageType.WHO_SNAPSHOT, limit=_READ_LIMIT)
            finally:
                await alice.close()
    assert roster["online_agents"] == []
    assert roster["delivery_sessions"] == {
        "PROJ/bob@laptop": {"incarnation": "i-1", "capabilities": {}, "hub_id": "laptop"}
    }


def _delivery_request(target: str, key: str) -> str:
    return json.dumps(
        {
            "sender": "PROJ/alice",
            "type": MessageType.DELIVERY_REQUEST,
            "target": target,
            "protocol_version": 3,
            "request_id": f"req-{key}",
            "idempotency_key": f"idem-{key}",
            "target_incarnation": "i-1",
            "mode": "follow_up",
            "allowed_fallbacks": ["next_turn"],
            "task_id": "T-1",
            "body": "Please review.",
            "deadline": time.time() + 120,
        }
    )


async def _refusal_for(peer: MessageForwardPeer, target: str, key: str, path: Path) -> str:
    store = EventStore(path / f"{key}.db")
    hub = SynapseHub(hub_id="workstation", journal=store, message_peers={"laptop": peer})
    try:
        async with _served(hub) as hub_uri:
            alice = await _agent(hub_uri, "PROJ/alice")
            try:
                await alice.send(_delivery_request(target, key))
                refused = await read_until_type(
                    alice, MessageType.DELIVERY_REFUSED, limit=_READ_LIMIT
                )
            finally:
                await alice.close()
    finally:
        store.close()
    return str(refused["reason_code"])


async def test_a_delivery_intent_is_refused_when_the_peer_cannot_answer(tmp_path: Path) -> None:
    """An unreachable peer and one that does not know the frame each yield a named refusal."""
    assert (
        await _refusal_for(_unreachable(), "PROJ/bob@laptop", "down", tmp_path)
        == "peer_unreachable"
    )
    async with scripted_peer(error_frame) as peer:
        assert (
            await _refusal_for(MessageForwardPeer(peer.uri), "PROJ/bob@laptop", "old", tmp_path)
            == "peer_rejected"
        )


@pytest.mark.parametrize(
    ("key", "answer"),
    [
        ("no-status", {}),
        ("no-key", {"status": {"type": MessageType.DELIVERY_STATUS, "operation_key": "short"}}),
        (
            "not-hex",
            {"status": {"type": MessageType.DELIVERY_STATUS, "operation_key": "G" * 64}},
        ),
    ],
)
async def test_a_delivery_intent_is_refused_unless_the_peer_answers_with_a_status(
    tmp_path: Path, key: str, answer: dict[str, Any]
) -> None:
    """An accepted answer without a well-formed status is never relayed as one."""

    def reply(frame: dict[str, Any]) -> str:
        return result_frame(frame["forward_id"], result=answer)

    async with scripted_peer(reply) as peer:
        assert (
            await _refusal_for(MessageForwardPeer(peer.uri), "PROJ/bob@laptop", key, tmp_path)
            == "peer_invalid_answer"
        )


async def test_a_hub_without_a_serving_policy_accepts_no_forward() -> None:
    """No policy, no grant: chats and roster requests from any peer are refused."""
    hub = SynapseHub(hub_id="laptop")
    async with _served(hub) as uri:
        peer = MessageForwardPeer(uri)
        chat = MessageForwardRequest(
            forward_id="c-1",
            kind="chat",
            sender_seat="PROJ/alice",
            target_seat="PROJ/bob",
            body={"payload": "hi"},
        )
        who = MessageForwardRequest(forward_id="w-1", kind="who", sender_seat="PROJ/alice")
        chat_result = await forward_message(chat, peer=peer, local_id="workstation")
        who_result = await forward_message(who, peer=peer, local_id="workstation")
    assert (chat_result.disposition, chat_result.reason_code) == ("refused", "peer_not_authorised")
    assert (who_result.disposition, who_result.reason_code) == ("refused", "peer_not_authorised")
    assert hub.message_forward_ledger.inbound("workstation", "c-1") is None


def test_only_a_locally_routed_chat_has_a_verdict() -> None:
    """A refusal, a channel chat or a forwarded chat carries no local delivery verdict."""
    with pytest.raises(ValueError, match="quota"):
        _ = ChatRouting(refusal="quota").verdict
    with pytest.raises(ValueError, match="not routed to local seats"):
        _ = ChatRouting().verdict


async def test_a_retry_loop_that_dies_stops_the_hub_loudly(tmp_path: Path) -> None:
    """Forwards must not silently stop retrying: a failed sweep ends ``serve`` with its error."""
    import sqlite3

    store = EventStore(tmp_path / "hub.db")
    hub = SynapseHub(hub_id="workstation", journal=store, message_peers={"laptop": _unreachable()})
    port = _free_port()
    task = asyncio.create_task(hub.serve("localhost", port))
    await _await_listening(port)
    store.close()
    with pytest.raises(sqlite3.ProgrammingError):
        await asyncio.wait_for(task, 10.0)


async def test_sigterm_stops_the_hub_and_its_retry_loop_cleanly() -> None:
    """A graceful stop returns from ``serve`` and cancels the retry loop, raising nothing."""
    import os
    import signal

    hub = SynapseHub(hub_id="workstation", message_peers={"laptop": _unreachable()})
    port = _free_port()
    task = asyncio.create_task(hub.serve("localhost", port))
    await _await_listening(port)
    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.wait_for(task, 10.0)
    assert task.exception() is None


async def _next_delivery_answer(websocket: ClientConnection) -> dict[str, Any]:
    """Return the next ``delivery_status`` or ``delivery_refused`` frame."""
    for _ in range(_READ_LIMIT):
        frame = json.loads(await asyncio.wait_for(websocket.recv(), 3.0))
        if frame.get("type") in (MessageType.DELIVERY_STATUS, MessageType.DELIVERY_REFUSED):
            return dict(frame)
    raise AssertionError("no delivery answer")


async def test_a_peer_key_may_not_shadow_a_local_delivery_or_another_route(
    tmp_path: Path,
) -> None:
    """Follow-ups route by operation key, so a peer cannot claim a key already routed here."""
    returned: dict[str, str] = {}

    def reply(frame: dict[str, Any]) -> str:
        status = {"type": MessageType.DELIVERY_STATUS, "operation_key": returned["key"]}
        return result_frame(frame["forward_id"], result={"status": status})

    store = EventStore(tmp_path / "hub.db")
    try:
        async with scripted_peer(reply) as peer:
            hub = SynapseHub(
                hub_id="workstation",
                journal=store,
                message_peers={"laptop": MessageForwardPeer(peer.uri)},
            )
            async with _served(hub) as uri:
                carol = await connect(uri)
                await read_until_type(carol, MessageType.WELCOME)
                await carol.send(
                    json.dumps(
                        {
                            "sender": "PROJ/carol",
                            "type": MessageType.HEARTBEAT,
                            "target": "System",
                            "payload": "online",
                            "protocol_version": 3,
                            "delivery_session_token": "c" * 64,
                            "delivery_capabilities": {"follow_up": "native"},
                        }
                    )
                )
                session = await read_until_type(carol, MessageType.DELIVERY_SESSION)
                alice = await _agent(uri, "PROJ/alice")
                try:
                    local = json.loads(_delivery_request("PROJ/carol", "local"))
                    local["target_incarnation"] = session["incarnation"]
                    await alice.send(json.dumps(local))
                    local_key = (await _next_delivery_answer(alice))["operation_key"]

                    returned["key"] = local_key
                    await alice.send(_delivery_request("PROJ/bob@laptop", "shadow-local"))
                    shadow = await _next_delivery_answer(alice)
                    assert shadow["reason_code"] == "peer_invalid_answer"

                    returned["key"] = "d" * 64
                    await alice.send(_delivery_request("PROJ/bob@laptop", "first"))
                    first = await _next_delivery_answer(alice)
                    assert first["type"] == MessageType.DELIVERY_STATUS
                    assert first["remote_hub"] == "laptop"
                    await alice.send(_delivery_request("PROJ/bob@laptop", "first"))
                    again = await _next_delivery_answer(alice)
                    assert again["operation_key"] == "d" * 64

                    other = await _agent(uri, "PROJ/mallory")
                    try:
                        request = json.loads(_delivery_request("PROJ/bob@laptop", "steal"))
                        request["sender"] = "PROJ/mallory"
                        await other.send(json.dumps(request))
                        stolen = await _next_delivery_answer(other)
                    finally:
                        await other.close()
                    assert stolen["reason_code"] == "peer_invalid_answer"
                    route = hub.message_forward_ledger.remote_delivery("d" * 64)
                    assert route is not None
                    assert route.sender == "PROJ/alice"
                finally:
                    await alice.close()
                    await carol.close()
    finally:
        store.close()
