# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — real forwarding ownership and receipt projection tests
"""Exercise transient ownership against real outbox state and WebSocket peers."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import pytest
from websockets.asyncio.client import ClientConnection, connect
from websockets.asyncio.server import ServerConnection, serve

from hub_e2e_helpers import read_until_type, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.message_forward_attempts import own_forward_work
from synapse_channel.core.message_forward_ledger import MessageForwardLedger, OutboxEntry
from synapse_channel.core.message_forward_origin import (
    attempt_forward,
    deliver_pending_forward_receipts,
    run_forward_retries,
)
from synapse_channel.core.message_forward_transport import MessageForwardPeer
from synapse_channel.core.message_forward_wire import (
    MessageForwardRequest,
    encode_message_forward_request,
)
from synapse_channel.core.persistence import EventStore


@pytest.mark.parametrize("kind", ["attempt", "notification"])
def test_ownership_is_per_forward_and_ledger_and_released_on_failure(
    kind: Literal["attempt", "notification"],
) -> None:
    """Real ledger operations coalesce one key while independent operations remain usable."""
    ledger = MessageForwardLedger.in_memory()
    other = MessageForwardLedger.in_memory()
    with own_forward_work(ledger, "first", kind) as first:
        assert first
        with own_forward_work(ledger, "first", kind) as duplicate:
            assert not duplicate
        with own_forward_work(ledger, "second", kind) as independent:
            assert independent
        with own_forward_work(other, "first", kind) as separate:
            assert separate
    with pytest.raises(RuntimeError, match="controlled owner failure"):
        with own_forward_work(ledger, "first", kind) as acquired:
            assert acquired
            raise RuntimeError("controlled owner failure")
    with own_forward_work(ledger, "first", kind) as recovered:
        assert recovered


async def test_cancellation_releases_owned_forward_without_waiting_for_another_job() -> None:
    """A cancelled actual task cannot leave its transient forward slot occupied."""
    ledger = MessageForwardLedger.in_memory()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def owner() -> None:
        with own_forward_work(ledger, "cancelled", "attempt") as acquired:
            assert acquired
            entered.set()
            await release.wait()

    task = asyncio.create_task(owner())
    await asyncio.wait_for(entered.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with own_forward_work(ledger, "cancelled", "attempt") as recovered:
        assert recovered


async def test_overlapping_real_sweeps_share_one_exchange_and_one_terminal_receipt(
    tmp_path: Path,
) -> None:
    """Two live retry sweeps cannot duplicate healthy forwarding or sender projection."""
    received: list[dict[str, Any]] = []
    retry_received = asyncio.Event()
    answer = asyncio.Event()

    async def peer(socket: ServerConnection) -> None:
        frame = json.loads(await asyncio.wait_for(socket.recv(), 3))
        received.append(frame)
        if len(received) == 1:
            await socket.close(code=1011, reason="private-peer-diagnostic-CRR10")
            return
        retry_received.set()
        await asyncio.wait_for(answer.wait(), 3)
        await socket.send(
            json.dumps(
                {
                    "sender": "SynapseHub",
                    "type": "multihub_message_result",
                    "forward_id": frame["forward_id"],
                    "answering_hub": "peer",
                    "disposition": "accepted",
                    "reason_code": "",
                    "detail": "",
                    "result": {"delivered": True, "recipients": ["PROJ/recipient"]},
                }
            )
        )

    journal = EventStore(tmp_path / "origin.db")
    tasks: list[asyncio.Task[int]] = []
    try:
        async with serve(peer, "127.0.0.1", 0) as server:
            peer_port = server.sockets[0].getsockname()[1]
            hub = SynapseHub(
                hub_id="origin",
                journal=journal,
                message_peers={"peer": MessageForwardPeer(f"ws://127.0.0.1:{peer_port}")},
            )
            async with running_hub(hub) as (_, uri), connect(uri) as sender:
                await read_until_type(sender, "welcome")
                await sender.send(
                    json.dumps(
                        {
                            "sender": "PROJ/sender",
                            "type": "heartbeat",
                            "target": "System",
                            "protocol_version": 3,
                        }
                    )
                )
                await sender.send(
                    json.dumps(
                        {
                            "sender": "PROJ/sender",
                            "type": "chat",
                            "target": "PROJ/recipient@peer",
                            "payload": "one durable payload",
                            "receipt_requested": True,
                        }
                    )
                )
                initial = await read_until_type(sender, "delivery_receipt")
                assert initial["forward_state"] == "pending"
                assert "private-peer-diagnostic-CRR10" not in initial["payload"]
                key = initial["forward_id"]
                tasks.append(asyncio.create_task(run_forward_retries(hub, now=time.time() + 2)))
                await asyncio.wait_for(retry_received.wait(), 3)
                tasks.append(asyncio.create_task(run_forward_retries(hub, now=time.time() + 2)))
                assert await asyncio.wait_for(tasks[1], 3) == 0
                assert len(received) == 2
                answer.set()
                assert await asyncio.wait_for(tasks[0], 3) == 1
                final = await read_until_type(sender, "delivery_receipt")
                assert final["forward_state"] == "accepted"
                assert final["forward_id"] == key
                assert final["recipients"] == ["PROJ/recipient@peer"]
                await sender.send(json.dumps({"sender": "PROJ/sender", "type": "who_request"}))
                for _ in range(20):
                    frame = json.loads(await asyncio.wait_for(sender.recv(), 3))
                    assert frame.get("type") != "delivery_receipt"
                    if frame.get("type") == "who_snapshot":
                        break
                else:
                    raise AssertionError("sender fence did not complete")
                assert all(frame["forward_id"] == key for frame in received)
                stored = hub.message_forward_ledger.outbox_entry(key)
                assert stored is not None and stored.attempts == 2 and stored.state == "accepted"
                assert not hub.message_forward_ledger.sender_notification_pending(key)
    finally:
        answer.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        journal.close()


@dataclass
class HeldPeer:
    """A real peer withholding its first reply while subsequent forwards can complete."""

    uri: str
    received: list[dict[str, Any]]
    entered: asyncio.Event
    release: asyncio.Event


@contextlib.asynccontextmanager
async def held_peer() -> AsyncIterator[HeldPeer]:
    """Serve a controlled reply delay through the production WebSocket transport.

    Yields
    ------
    HeldPeer
        Network address, original received frames and the first exchange's event gates.
    """
    received: list[dict[str, Any]] = []
    entered = asyncio.Event()
    release = asyncio.Event()

    async def handler(socket: ServerConnection) -> None:
        frame = json.loads(await asyncio.wait_for(socket.recv(), 3))
        received.append(frame)
        if len(received) == 1:
            entered.set()
            await asyncio.wait_for(release.wait(), 3)
        if socket.close_code is None:
            await socket.send(
                json.dumps(
                    {
                        "type": "multihub_message_result",
                        "forward_id": frame["forward_id"],
                        "answering_hub": "peer",
                        "disposition": "accepted",
                        "reason_code": "",
                        "detail": "",
                        "result": {"delivered": True, "recipients": ["PROJ/recipient"]},
                    }
                )
            )

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        try:
            yield HeldPeer(f"ws://127.0.0.1:{port}", received, entered, release)
        finally:
            release.set()


@contextlib.asynccontextmanager
async def registered_sender(uri: str, name: str = "PROJ/sender") -> AsyncIterator[ClientConnection]:
    """Register a real seat and fence its connection before exposing it to a test.

    Parameters
    ----------
    uri : str
        Served origin hub address.
    name : str, optional
        The authenticated local test seat.

    Yields
    ------
    ClientConnection
        Registered sender connection, closed when the context exits.
    """
    async with connect(uri) as socket:
        await read_until_type(socket, "welcome")
        await socket.send(json.dumps({"sender": name, "type": "heartbeat", "protocol_version": 3}))
        await socket.send(json.dumps({"sender": name, "type": "who_request"}))
        await read_until_type(socket, "who_snapshot")
        yield socket


def enqueue_forward(
    hub: SynapseHub, *, expires_at: float, forward_id: str = "held-forward"
) -> OutboxEntry:
    """Create a durable forward through its public ledger API.

    Parameters
    ----------
    hub : SynapseHub
        Actual origin hub whose production retry path will consume the entry.
    expires_at : float
        Wall-clock deadline for this forward.
    forward_id : str, optional
        Origin-unique identifier used to exercise independent durable forwards.

    Returns
    -------
    OutboxEntry
        The persisted pending entry with sender notification requested.
    """
    request = MessageForwardRequest(
        forward_id=forward_id,
        kind="chat",
        sender_seat="PROJ/sender",
        target_seat="PROJ/recipient",
        body={
            "payload": "held durable payload",
            "origin_msg_id": 1,
            "origin_timestamp": time.time(),
        },
    )
    return hub.message_forward_ledger.enqueue(
        forward_id=request.forward_id,
        peer_hub="peer",
        sender=request.sender_seat,
        target="PROJ/recipient@peer",
        request=encode_message_forward_request(request),
        now=time.time(),
        expires_at=expires_at,
        notify_sender=True,
    )


async def test_cancelled_peer_exchange_can_be_retried_with_the_same_durable_id() -> None:
    """Actual transport cancellation releases ownership and leaves the outbox recoverable."""
    async with held_peer() as peer:
        hub = SynapseHub(hub_id="origin", message_peers={"peer": MessageForwardPeer(peer.uri)})
        entry = enqueue_forward(hub, expires_at=time.time() + 60)
        assert not hub.message_forward_ledger.sender_notification_pending(entry.forward_id)
        task = asyncio.create_task(attempt_forward(hub, entry))
        try:
            await asyncio.wait_for(peer.entered.wait(), 3)
            coalesced = await attempt_forward(hub, entry)
            assert coalesced.state == "pending" and len(peer.received) == 1
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            current = hub.message_forward_ledger.outbox_entry(entry.forward_id)
            assert current is not None and current.state == "pending" and current.attempts == 0
            assert await run_forward_retries(hub, now=time.time()) == 1
            settled = hub.message_forward_ledger.outbox_entry(entry.forward_id)
            assert settled is not None and settled.state == "accepted" and settled.attempts == 1
            assert [frame["forward_id"] for frame in peer.received] == [entry.forward_id] * 2
            assert hub.message_forward_ledger.sender_notification_pending(entry.forward_id)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_overlapping_sweeps_skip_entries_settled_from_their_old_snapshot() -> None:
    """A second sweep can finish an independent forward while the first snapshot is held."""
    async with held_peer() as peer:
        hub = SynapseHub(hub_id="origin", message_peers={"peer": MessageForwardPeer(peer.uri)})
        first = enqueue_forward(hub, expires_at=time.time() + 60)
        second = enqueue_forward(hub, expires_at=time.time() + 60, forward_id="independent-forward")
        task = asyncio.create_task(run_forward_retries(hub, now=time.time()))
        try:
            await asyncio.wait_for(peer.entered.wait(), 3)
            assert await run_forward_retries(hub, now=time.time()) == 1
            assert [frame["forward_id"] for frame in peer.received] == [
                first.forward_id,
                second.forward_id,
            ]
            peer.release.set()
            assert await asyncio.wait_for(task, 3) == 1
            assert len(peer.received) == 2
            for entry in [first, second]:
                stored = hub.message_forward_ledger.outbox_entry(entry.forward_id)
                assert stored is not None and stored.state == "accepted" and stored.attempts == 1
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_initial_chat_expiry_before_peer_reply_has_only_one_terminal_receipt() -> None:
    """An initial handler returning late sees the expiry notification already delivered."""
    async with held_peer() as peer:
        hub = SynapseHub(hub_id="origin", message_peers={"peer": MessageForwardPeer(peer.uri)})
        async with running_hub(hub) as (_, uri), registered_sender(uri) as sender:
            await sender.send(
                json.dumps(
                    {
                        "sender": "PROJ/sender",
                        "type": "chat",
                        "target": "PROJ/recipient@peer",
                        "payload": "expires while held",
                        "receipt_requested": True,
                    }
                )
            )
            await asyncio.wait_for(peer.entered.wait(), 3)
            entry = hub.message_forward_ledger.outbox_entry(peer.received[0]["forward_id"])
            assert entry is not None
            assert await run_forward_retries(hub, now=entry.expires_at + 1) == 1
            receipt = await read_until_type(sender, "delivery_receipt")
            assert receipt["forward_state"] == "expired" and not receipt["delivered"]
            peer.release.set()
            await sender.send(json.dumps({"sender": "PROJ/sender", "type": "who_request"}))
            for _ in range(20):
                frame = json.loads(await asyncio.wait_for(sender.recv(), 3))
                assert frame.get("type") != "delivery_receipt"
                if frame.get("type") == "who_snapshot":
                    break
            else:
                raise AssertionError("late initial expiry fence did not complete")
            assert not hub.message_forward_ledger.sender_notification_pending(entry.forward_id)
            assert len(peer.received) == 1


async def test_expiry_during_peer_exchange_survives_a_late_answer_and_notifies_once() -> None:
    """A late transport answer cannot revive an expired forward or duplicate its receipt."""
    async with held_peer() as peer:
        hub = SynapseHub(hub_id="origin", message_peers={"peer": MessageForwardPeer(peer.uri)})
        entry = enqueue_forward(hub, expires_at=time.time() + 60)
        async with running_hub(hub) as (_, uri), registered_sender(uri) as sender:
            task = asyncio.create_task(run_forward_retries(hub, now=time.time()))
            try:
                await asyncio.wait_for(peer.entered.wait(), 3)
                assert await run_forward_retries(hub, now=entry.expires_at + 1) == 1
                receipt = await read_until_type(sender, "delivery_receipt")
                assert receipt["forward_state"] == "expired" and not receipt["delivered"]
                peer.release.set()
                assert await asyncio.wait_for(task, 3) == 0
                stale = await attempt_forward(hub, entry)
                assert stale.state == "expired" and stale.attempts == 0
                await deliver_pending_forward_receipts(hub, sender=entry.sender)
                await sender.send(json.dumps({"sender": entry.sender, "type": "who_request"}))
                for _ in range(20):
                    frame = json.loads(await asyncio.wait_for(sender.recv(), 3))
                    assert frame.get("type") != "delivery_receipt"
                    if frame.get("type") == "who_snapshot":
                        break
                else:
                    raise AssertionError("expiry receipt fence did not complete")
                assert len(peer.received) == 1
                assert not hub.message_forward_ledger.sender_notification_pending(entry.forward_id)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("receipt_requested", [True, False])
async def test_initial_exchange_coalesces_retry_while_another_chat_remains_independent(
    tmp_path: Path, receipt_requested: bool
) -> None:
    """A held initial chat blocks neither another chat nor its receipt and retry is coalesced."""
    journal = EventStore(tmp_path / "origin.db")
    try:
        async with held_peer() as peer:
            hub = SynapseHub(
                hub_id="origin",
                journal=journal,
                message_peers={"peer": MessageForwardPeer(peer.uri)},
            )
            async with (
                running_hub(hub) as (_, uri),
                registered_sender(uri) as sender,
                registered_sender(uri, "PROJ/other") as other,
            ):
                await sender.send(
                    json.dumps(
                        {
                            "sender": "PROJ/sender",
                            "type": "chat",
                            "target": "PROJ/recipient@peer",
                            "payload": "held",
                            "client_msg_id": "held-client-message",
                            "receipt_requested": receipt_requested,
                        }
                    )
                )
                await asyncio.wait_for(peer.entered.wait(), 3)
                assert await run_forward_retries(hub, now=time.time()) == 0
                await other.send(
                    json.dumps(
                        {
                            "sender": "PROJ/other",
                            "type": "chat",
                            "target": "PROJ/recipient@peer",
                            "payload": "independent",
                            "receipt_requested": True,
                        }
                    )
                )
                independent = await read_until_type(other, "delivery_receipt")
                assert independent["forward_state"] == "accepted"
                assert len(peer.received) == 2 and not peer.release.is_set()
                peer.release.set()
                if receipt_requested:
                    receipt = await read_until_type(sender, "delivery_receipt")
                    assert receipt["forward_state"] == "accepted" and not receipt["deferred"]
                    assert receipt["client_msg_id"] == "held-client-message"
                    assert isinstance(receipt["message_seq"], int)
                await sender.send(json.dumps({"sender": "PROJ/sender", "type": "who_request"}))
                for _ in range(20):
                    frame = json.loads(await asyncio.wait_for(sender.recv(), 3))
                    assert frame.get("type") != "delivery_receipt"
                    if frame.get("type") == "who_snapshot":
                        break
                else:
                    raise AssertionError("initial receipt fence did not complete")
                held_id = peer.received[0]["forward_id"]
                assert not hub.message_forward_ledger.sender_notification_pending(held_id)
                assert not hub.message_forward_ledger.sender_notification_pending("missing")
                assert await run_forward_retries(hub, now=time.time() + 10) == 0
                assert len(peer.received) == 2
    finally:
        journal.close()


async def test_offline_settlement_survives_restart_and_is_reported_once_on_registration(
    tmp_path: Path,
) -> None:
    """A failed sender send remains durable and registration recovers exactly one receipt."""
    async with held_peer() as peer:
        peer.release.set()
        journal = EventStore(tmp_path / "restart.db")
        try:
            hub = SynapseHub(
                hub_id="origin",
                journal=journal,
                message_peers={"peer": MessageForwardPeer(peer.uri)},
            )
            entry = enqueue_forward(hub, expires_at=time.time() + 60)
            assert await run_forward_retries(hub, now=time.time()) == 1
            assert hub.message_forward_ledger.sender_notification_pending(entry.forward_id)
        finally:
            journal.close()
        recovered_store = EventStore(tmp_path / "restart.db")
        try:
            recovered = SynapseHub(hub_id="origin", journal=recovered_store)
            assert recovered.message_forward_ledger.sender_notification_pending(entry.forward_id)
            async with running_hub(recovered) as (_, uri), connect(uri) as sender:
                await read_until_type(sender, "welcome")
                await sender.send(
                    json.dumps(
                        {
                            "sender": entry.sender,
                            "type": "heartbeat",
                            "protocol_version": 3,
                        }
                    )
                )
                receipt = await read_until_type(sender, "delivery_receipt")
                assert receipt["forward_id"] == entry.forward_id
                assert receipt["forward_state"] == "accepted" and receipt["deferred"]
                await asyncio.gather(
                    deliver_pending_forward_receipts(recovered, sender=entry.sender),
                    deliver_pending_forward_receipts(recovered, sender=entry.sender),
                )
                await sender.send(json.dumps({"sender": entry.sender, "type": "who_request"}))
                for _ in range(20):
                    frame = json.loads(await asyncio.wait_for(sender.recv(), 3))
                    assert frame.get("type") != "delivery_receipt"
                    if frame.get("type") == "who_snapshot":
                        break
                else:
                    raise AssertionError("recovered receipt fence did not complete")
                assert not recovered.message_forward_ledger.sender_notification_pending(
                    entry.forward_id
                )
                assert len(peer.received) == 1
        finally:
            recovered_store.close()
