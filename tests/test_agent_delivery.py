# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — public client API delivery journey
"""Drive v3 delivery through two ordinary SynapseAgent clients and a real hub."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.server import ServerConnection, serve

from hub_e2e_helpers import running_hub
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.delivery_modes import DeliveryRefusal
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType


async def _next_type(queue: asyncio.Queue[dict[str, Any]], kind: str) -> dict[str, Any]:
    """Read callback frames until the requested response arrives."""
    while True:
        frame = await asyncio.wait_for(queue.get(), 3)
        if frame.get("type") == kind:
            return frame


@pytest.mark.real_hub
async def test_public_agent_delivery_methods_use_negotiated_session(tmp_path: Path) -> None:
    """WHO discovery, admission, ACK and outcome work through client methods."""
    async with running_hub(SynapseHub(journal=EventStore(tmp_path / "hub.db"), hub_id="hub-1")) as (
        _hub,
        uri,
    ):
        sender_frames: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        receiver_frames: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def sender_callback(frame: dict[str, Any]) -> None:
            await sender_frames.put(frame)

        async def receiver_callback(frame: dict[str, Any]) -> None:
            await receiver_frames.put(frame)

        sender = SynapseAgent("P/author", uri=uri, on_message_callback=sender_callback)
        receiver = SynapseAgent(
            "P/receiver",
            uri=uri,
            on_message_callback=receiver_callback,
            delivery_capabilities={"follow_up": "native"},
        )
        sender_task = asyncio.create_task(sender.connect())
        receiver_task = asyncio.create_task(receiver.connect())
        try:
            await asyncio.wait_for(receiver.delivery_ready_event.wait(), 3)
            assert await sender.wait_until_ready()
            await sender.request_who()
            roster = await _next_type(sender_frames, MessageType.WHO_SNAPSHOT)
            session = roster["delivery_sessions"]["P/receiver"]
            assert session["incarnation"] == receiver.delivery_incarnation
            key = await sender.request_delivery(
                target="P/receiver",
                target_incarnation=session["incarnation"],
                mode="follow_up",
                body="Answer the reviewed question.",
                deadline=time.time() + 120,
                request_id="client-req-1",
                idempotency_key="client-idem-1",
                task_id="client-task-1",
            )
            queued = await _next_type(sender_frames, MessageType.DELIVERY_STATUS)
            offer = await _next_type(receiver_frames, MessageType.DELIVERY_OFFER)
            assert queued["operation_key"] == key == offer["operation_key"]
            await receiver.report_delivery_stage(
                key,
                request_id="client-req-1",
                task_id="client-task-1",
                mutation_id="client-boundary-1",
                stage="boundary_delivered",
                evidence={"boundary": "turn-1"},
            )
            await _next_type(receiver_frames, MessageType.DELIVERY_STATUS)
            await receiver.report_delivery_stage(
                key,
                request_id="client-req-1",
                task_id="client-task-1",
                mutation_id="client-ack-1",
                stage="acknowledged",
                evidence={"receipt_id": "local-1"},
            )
            acknowledged = await _next_type(receiver_frames, MessageType.DELIVERY_STATUS)
            assert acknowledged["explicitly_acknowledged"]
            assert not acknowledged["task_completed"]
            await receiver.report_delivery_stage(
                key,
                request_id="client-req-1",
                task_id="client-task-1",
                mutation_id="client-outcome-1",
                stage="completed",
                evidence={"executor_ref": "run-1", "outcome_code": "success"},
            )
            completed = await _next_type(receiver_frames, MessageType.DELIVERY_STATUS)
            assert completed["task_completed"]
        finally:
            if sender.connection is not None:
                await sender.connection.close()
            if receiver.connection is not None:
                await receiver.connection.close()
            await asyncio.gather(sender_task, receiver_task)


async def test_client_refuses_delivery_before_hub_negotiation() -> None:
    """A disconnected client emits no v3 frame or accidental v2 chat fallback."""
    agent = SynapseAgent("P/author", machine_identity=False)
    with pytest.raises(DeliveryRefusal, match="connected hub"):
        await agent.request_delivery_status("a" * 64)


async def test_v3_client_sends_no_delivery_verb_to_v2_hub() -> None:
    """The public API gates v3 methods on the actual WELCOME version."""
    received: list[dict[str, Any]] = []

    async def old_hub(websocket: ServerConnection) -> None:
        await websocket.send(
            json.dumps(
                {
                    "sender": "SynapseHub",
                    "target": "self",
                    "type": MessageType.WELCOME,
                    "payload": "welcome",
                    "timestamp": 1.0,
                    "hub_id": "old-hub",
                    "protocol_version": 2,
                }
            )
        )
        async for raw in websocket:
            received.append(json.loads(raw))

    async with serve(old_hub, "127.0.0.1", 0) as server:
        socket = server.sockets[0]
        uri = f"ws://127.0.0.1:{socket.getsockname()[1]}"
        agent = SynapseAgent("P/author", uri=uri, machine_identity=False)
        task = asyncio.create_task(agent.connect())
        try:
            assert await agent.wait_until_ready()
            with pytest.raises(DeliveryRefusal) as failure:
                await agent.request_delivery_status("a" * 64)
            assert failure.value.code == "unsupported_protocol"
            await asyncio.sleep(0)
            assert all(frame["type"] == MessageType.HEARTBEAT for frame in received)
        finally:
            if agent.connection is not None:
                await agent.connection.close()
            await task
