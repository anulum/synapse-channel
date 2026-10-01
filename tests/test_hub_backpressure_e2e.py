# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real TCP backpressure, receipt and 100-agent fleet acceptance

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from websockets.asyncio.client import ClientConnection, connect
from websockets.asyncio.server import Server, ServerConnection, serve

from hub_e2e_helpers import (
    AgentHandle,
    close_agents,
    connect_agent,
    read_until_type,
    running_hub,
)
from synapse_channel.core.directed_delivery_liveness import RECIPIENT_TRANSPORT_UNAVAILABLE
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType


class _SmallBuffers(ServerConnection):
    """Use real finite TCP send buffers to reproduce an unread receiver."""

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        """Configure socket backpressure before attaching the real protocol."""
        transport.get_extra_info("socket").setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        super().connection_made(transport)


@contextlib.asynccontextmanager
async def _small_buffer_server(hub: SynapseHub) -> AsyncIterator[tuple[str, Server]]:
    """Exercise the real public hub handler over low-buffer local TCP."""
    async with serve(
        hub.handler,
        "127.0.0.1",
        0,
        create_connection=_SmallBuffers,
        write_limit=1024,
        compression=None,
        ping_interval=None,
        close_timeout=1,
    ) as server:
        yield "ws://127.0.0.1:" + str(server.sockets[0].getsockname()[1]), server


@contextlib.asynccontextmanager
async def _unread_client(uri: str) -> AsyncIterator[ClientConnection]:
    """Negotiate a small real TCP receive window before the WebSocket handshake."""
    endpoint = urlsplit(uri)
    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        client_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        client_socket.setblocking(False)
        assert endpoint.port is not None
        await asyncio.get_running_loop().sock_connect(client_socket, ("127.0.0.1", endpoint.port))
        async with connect(
            uri,
            sock=client_socket,
            compression=None,
            max_queue=1,
            ping_interval=None,
            close_timeout=1,
        ) as connection:
            yield connection
    finally:
        client_socket.close()


@contextlib.asynccontextmanager
async def _blocked_peer(
    server: Server, unread: ClientConnection, identity: str
) -> AsyncIterator[None]:
    """Observe actual TCP write pressure before asking the hub for a receipt."""
    address = unread.transport.get_extra_info("sockname")
    peer = next(
        connection for connection in server.connections if connection.remote_address == address
    )
    unread.transport.pause_reading()
    # OS buffer sizes are hints. Prime this real transport until its write buffer
    # actually blocks; these fixture frames never enter the hub's journal/quota.
    frame = json.dumps(
        {"type": "chat", "sender": "fixture-primer", "target": identity, "payload": "x" * 65536}
    )

    async def prime() -> None:
        for _ in range(1024):
            await peer.send(frame)

    writer = asyncio.create_task(prime())
    try:
        deadline = asyncio.get_running_loop().time() + 5
        while peer.transport.get_write_buffer_size() <= 1024:
            if writer.done():
                await writer
                raise AssertionError("bounded TCP priming completed without write pressure")
            assert asyncio.get_running_loop().time() < deadline, (
                "TCP write pressure was not observed"
            )
            await asyncio.sleep(0)
        assert not writer.done()
        yield
    finally:
        writer.cancel()
        await asyncio.gather(writer, return_exceptions=True)
        unread.transport.resume_reading()
        await peer.close()


@pytest.mark.parametrize("private", [False, True])
async def test_failed_recipient_write_is_negative_and_same_id_can_retry(
    tmp_path: Path, private: bool
) -> None:
    """Real stalled writes cannot produce positive receipts or poison retries."""
    store = EventStore(tmp_path / "hub.db")
    hub = SynapseHub(
        journal=store,
        private_directed_messages=private,
        dead_letter_escalation_threshold=1 if not private else 10,
    )
    async with _small_buffer_server(hub) as (uri, server):
        sender = await connect_agent("P/sender", uri)
        observer = await connect_agent("P/other", uri)
        try:
            async with _unread_client(uri) as unread:
                await read_until_type(unread, "welcome")
                await unread.send(json.dumps({"sender": "P/recipient", "type": "heartbeat"}))
                await sender.recorder.wait_for(
                    lambda m: m.get("type") == "presence_update" and m.get("agent") == "P/recipient"
                )
                # Fill the unread client's frame queue before the larger directed frame.
                await observer.agent.chat("prime", target="all")
                await sender.recorder.wait_for(lambda m: m.get("payload") == "prime")
                async with _blocked_peer(server, unread, "P/recipient"):
                    payload = "retry-body:" + "x" * 262144
                    await sender.agent.send_message(
                        MessageType.CHAT,
                        payload=payload,
                        target="P/recipient",
                        client_msg_id="retry-stalled",
                        receipt_requested=True,
                    )
                    negative = await sender.recorder.wait_for(
                        lambda m: (
                            m.get("type") == MessageType.DELIVERY_RECEIPT
                            and m.get("client_msg_id") == "retry-stalled"
                        ),
                        timeout=15,
                    )
                    assert negative["delivered"] is False
                    assert negative["reason"] == RECIPIENT_TRANSPORT_UNAVAILABLE
                    assert negative["matched_recipients"] == ["P/recipient"]
                    assert "transport completed a write" in negative["payload"]
                    assert hub.counters.chat_duplicates_suppressed == 0
            receiver = await connect_agent("P/recipient", uri)
            try:
                await sender.agent.send_message(
                    MessageType.CHAT,
                    payload=payload,
                    target="P/recipient",
                    client_msg_id="retry-stalled",
                    receipt_requested=True,
                )
                await receiver.recorder.wait_for(
                    lambda m: (
                        m.get("type") == MessageType.CHAT
                        and m.get("client_msg_id") == "retry-stalled"
                    ),
                    timeout=15,
                )
                positive = await sender.recorder.wait_for(
                    lambda m: (
                        m.get("type") == MessageType.DELIVERY_RECEIPT
                        and m.get("client_msg_id") == "retry-stalled"
                        and m.get("delivered") is True
                    ),
                    timeout=15,
                )
                assert positive["recipients"] == ["P/recipient"]
                await sender.agent.send_message(
                    MessageType.CHAT,
                    payload=payload,
                    target="P/recipient",
                    client_msg_id="retry-stalled",
                    receipt_requested=True,
                )
                await sender.recorder.wait_for(lambda m: m.get("duplicate") is True)
                assert hub.counters.chat_duplicates_suppressed == 1
                chats = [
                    event
                    for event in store.read_all()
                    if event.kind == EventKind.CHAT
                    and event.payload.get("client_msg_id") == "retry-stalled"
                ]
                assert len(chats) == 2
                verdicts = [
                    event.payload["delivered"]
                    for event in store.read_all()
                    if event.kind == EventKind.DELIVERY_RECEIPT_IMMEDIATE
                    and event.payload.get("client_msg_id") == "retry-stalled"
                ]
                assert verdicts == [False, True]
            finally:
                await close_agents(receiver)
        finally:
            await close_agents(sender, observer)
    store.close()


@pytest.mark.parametrize("private", [False, True])
async def test_one_hundred_native_clients_exchange_two_hundred_receipted_messages(
    private: bool,
) -> None:
    """Exercise native client registration, bidirectional routing and receipts."""
    hub = SynapseHub(
        max_clients=4096, max_connections_per_host=2048, private_directed_messages=private
    )
    async with running_hub(hub) as (_, uri):
        agents: list[AgentHandle] = []
        try:
            for i in range(100):
                agents.append(await connect_agent(f"P/terminal-{i:03d}", uri))
            assert len(hub.online_agents()) == 100

            async def exchange(i: int, reverse: bool) -> None:
                """Verify a recipient frame and its correlated positive receipt."""
                sender, receiver = agents[i], agents[(i + 1) % 100]
                if reverse:
                    sender, receiver = receiver, sender
                identity = f"roundtrip-{i}-{reverse}"
                await sender.agent.send_message(
                    MessageType.CHAT,
                    payload=identity,
                    target=receiver.agent.name,
                    client_msg_id=identity,
                    receipt_requested=True,
                )
                received, receipt = await asyncio.gather(
                    receiver.recorder.wait_for(
                        lambda m: (
                            m.get("type") == MessageType.CHAT and m.get("client_msg_id") == identity
                        ),
                        timeout=15,
                    ),
                    sender.recorder.wait_for(
                        lambda m: (
                            m.get("type") == MessageType.DELIVERY_RECEIPT
                            and m.get("client_msg_id") == identity
                        ),
                        timeout=15,
                    ),
                )
                assert received["sender"] == sender.agent.name
                assert receipt["delivered"] is True
                assert receipt["recipients"] == [receiver.agent.name]

            for reverse in (False, True):
                for parity in (0, 1):
                    await asyncio.gather(*(exchange(i, reverse) for i in range(parity, 100, 2)))
        finally:
            await close_agents(*agents)


@pytest.mark.parametrize("healthy", [False, True])
async def test_channel_receipt_and_retry_use_completed_concurrent_member_writes(
    tmp_path: Path, healthy: bool
) -> None:
    """Real channel members get truthful receipts without serial stalled fan-out."""
    store = EventStore(tmp_path / "channel.db")
    hub = SynapseHub(journal=store)
    async with _small_buffer_server(hub) as (uri, server):
        sender = await connect_agent("P/sender", uri)
        other = await connect_agent("P/z-healthy", uri)
        try:
            await sender.agent.send_message(MessageType.CHANNEL_CREATE, channel="ops")
            await sender.recorder.wait_for(
                lambda m: m.get("ok") is True and "created" in m.get("payload", "")
            )
            async with _unread_client(uri) as unread:
                await read_until_type(unread, "welcome")
                await unread.send(json.dumps({"sender": "P/a-unread", "type": "heartbeat"}))
                await sender.recorder.wait_for(lambda m: m.get("agent") == "P/a-unread")
                await sender.agent.send_message(
                    MessageType.CHANNEL_INVITE, channel="ops", invitee="P/a-unread"
                )
                await sender.recorder.wait_for(
                    lambda m: m.get("ok") is True and "invited 'P/a-unread'" in m.get("payload", "")
                )
                await unread.send(
                    json.dumps({"sender": "P/a-unread", "type": "channel_join", "channel": "ops"})
                )
                joined = await read_until_type(unread, "channel_result")
                assert joined["ok"] is True
                if healthy:
                    await sender.agent.send_message(
                        MessageType.CHANNEL_INVITE, channel="ops", invitee="P/z-healthy"
                    )
                    await sender.recorder.wait_for(
                        lambda m: (
                            m.get("ok") is True and "invited 'P/z-healthy'" in m.get("payload", "")
                        )
                    )
                    await other.agent.send_message(MessageType.CHANNEL_JOIN, channel="ops")
                    await other.recorder.wait_for(
                        lambda m: m.get("type") == "channel_result" and m.get("ok") is True
                    )
                for i in range(3):
                    await other.agent.chat(f"channel-prime-{i}", target="all")
                await sender.recorder.wait_for(lambda m: m.get("payload") == "channel-prime-2")
                deadline = asyncio.get_running_loop().time() + 3
                while (
                    unread.transport.is_reading() and asyncio.get_running_loop().time() < deadline
                ):
                    await asyncio.sleep(0.01)
                assert not unread.transport.is_reading()
                async with _blocked_peer(server, unread, "P/a-unread"):
                    payload = "channel-body:" + "x" * 262144
                    await sender.agent.send_message(
                        MessageType.CHAT,
                        channel="ops",
                        payload=payload,
                        client_msg_id="channel-retry",
                        receipt_requested=True,
                    )
                    if healthy:
                        await other.recorder.wait_for(
                            lambda m: (
                                m.get("client_msg_id") == "channel-retry"
                                and m.get("type") == MessageType.CHAT
                            ),
                            timeout=3,
                        )
                    receipt = await sender.recorder.wait_for(
                        lambda m: (
                            m.get("client_msg_id") == "channel-retry"
                            and m.get("type") == MessageType.DELIVERY_RECEIPT
                        ),
                        timeout=15,
                    )
                    assert receipt["delivered"] is healthy
                    if healthy:
                        assert receipt["recipients"] == ["P/z-healthy"]
                    else:
                        assert receipt["reason"] == RECIPIENT_TRANSPORT_UNAVAILABLE
                        assert receipt["matched_recipients"] == ["P/a-unread"]
            receiver = await connect_agent("P/a-unread", uri)
            try:
                await sender.agent.send_message(
                    MessageType.CHAT,
                    channel="ops",
                    payload=payload,
                    client_msg_id="channel-retry",
                    receipt_requested=True,
                )
                if not healthy:
                    await receiver.recorder.wait_for(
                        lambda m: (
                            m.get("client_msg_id") == "channel-retry"
                            and m.get("type") == MessageType.CHAT
                        ),
                        timeout=15,
                    )
                    positive = await sender.recorder.wait_for(
                        lambda m: (
                            m.get("client_msg_id") == "channel-retry"
                            and m.get("type") == MessageType.DELIVERY_RECEIPT
                            and m.get("delivered") is True
                        ),
                        timeout=15,
                    )
                    assert positive["recipients"] == ["P/a-unread"]
                    await sender.agent.send_message(
                        MessageType.CHAT,
                        channel="ops",
                        payload=payload,
                        client_msg_id="channel-retry",
                        receipt_requested=True,
                    )
                await sender.recorder.wait_for(lambda m: m.get("duplicate") is True)
                assert hub.counters.chat_duplicates_suppressed == 1
                attempts = [
                    e
                    for e in store.read_all()
                    if e.kind == EventKind.CHAT
                    and e.payload.get("client_msg_id") == "channel-retry"
                ]
                assert len(attempts) == (1 if healthy else 2)
            finally:
                await close_agents(receiver)
        finally:
            await close_agents(sender, other)
    store.close()
