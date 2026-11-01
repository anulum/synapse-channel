# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — delivery bridge against a real hub: refusal, reconnect, full queue
"""Drive the recipient bridge through hub refusals, a missing hub and a full queue."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import running_hub
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.participants.delivery_bridge import DeliveryParticipantBridge
from synapse_channel.participants.envelope import TurnRequest, TurnResult
from synapse_channel.participants.participant import ParticipantChannel, ParticipantHealth

RECEIVER = "P/receiver"


class _NoTurnParticipant:
    """A healthy executor that must never be asked for a turn in these journeys."""

    def __init__(self) -> None:
        self.calls = 0

    @property
    def identity(self) -> str:
        return RECEIVER

    @property
    def channel(self) -> ParticipantChannel:
        return ParticipantChannel.HEADLESS

    def health(self) -> ParticipantHealth:
        return ParticipantHealth(self.identity, self.channel, True, "ready")

    async def take_turn(self, request: TurnRequest) -> TurnResult:
        self.calls += 1
        raise AssertionError(f"provider turn started for {request.topic_id}")


def _offer(agent: SynapseAgent, key: str) -> dict[str, Any]:
    """Build a well-formed offer for this recipient session that no hub ever issued."""
    return {
        "type": MessageType.DELIVERY_OFFER,
        "target": agent.name,
        "target_incarnation": agent.delivery_incarnation,
        "operation_key": key,
        "request_id": f"local-{key[:8]}",
        "task_id": "bridge-boundary-task",
        "body": "Answer the queued question.",
        "deadline": time.time() + 60,
        "selected_mode": "next_turn",
    }


def _ledger_row(path: Path, key: str) -> tuple[str, str] | None:
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT stage, outcome_code FROM delivery_bridge WHERE operation_key = ?", (key,)
        ).fetchone()
    return None if row is None else (str(row[0]), str(row[1]))


async def _stop(task: asyncio.Task[None]) -> None:
    """Cancel an agent's connection loop and wait until it has unwound."""
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.real_hub
async def test_bridge_retries_without_a_hub_then_records_the_hub_refusal(tmp_path: Path) -> None:
    """A missing hub is retried; the connected hub's refusal ends the turn as hub_refused."""
    participant = _NoTurnParticipant()
    ledger = tmp_path / "bridge.db"
    async with running_hub(SynapseHub(journal=EventStore(tmp_path / "hub.db"), hub_id="hub-1")) as (
        _hub,
        uri,
    ):
        receiver = SynapseAgent(
            RECEIVER,
            uri=uri,
            delivery_capabilities={"next_turn": "emulated"},
            machine_identity=False,
        )
        bridge = DeliveryParticipantBridge(receiver, participant, ledger_path=ledger)
        refusals: list[dict[str, Any]] = []

        async def receiver_callback(frame: dict[str, Any]) -> None:
            if frame.get("type") == MessageType.DELIVERY_REFUSED:
                refusals.append(frame)
            await bridge.on_message(frame)

        receiver.callback = receiver_callback
        key = "c" * 64
        connect_now = asyncio.Event()

        async def connect_when_released() -> None:
            await connect_now.wait()
            await receiver.connect()

        bridge.start()
        receiver_task = asyncio.create_task(connect_when_released())
        try:
            await bridge.on_message(_offer(receiver, key))
            # Without a hub the boundary report is refused locally as unavailable_hub.
            # The bridge must keep the offer queued and retry, not settle it.
            await asyncio.sleep(0.6)
            assert _ledger_row(ledger, key) == ("queued", "")
            assert refusals == []
            connect_now.set()
            await asyncio.wait_for(receiver.delivery_ready_event.wait(), 5)
            await asyncio.wait_for(bridge._queue.join(), 10)
            assert _ledger_row(ledger, key) == ("hub_refused", "unknown_request")
            assert [frame["request_id"] for frame in refusals] == [f"local-{key[:8]}"]
            assert participant.calls == 0
        finally:
            await _stop(receiver_task)
            await bridge.close()


@pytest.mark.real_hub
async def test_full_local_queue_rejects_a_real_offer_at_the_hub(tmp_path: Path) -> None:
    """A recipient whose ordered queue is full settles the hub's offer as rejected."""
    participant = _NoTurnParticipant()
    ledger = tmp_path / "bridge.db"
    async with running_hub(SynapseHub(journal=EventStore(tmp_path / "hub.db"), hub_id="hub-1")) as (
        _hub,
        uri,
    ):
        sender_frames: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        offers: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def sender_callback(frame: dict[str, Any]) -> None:
            await sender_frames.put(frame)

        sender = SynapseAgent(
            "P/author", uri=uri, on_message_callback=sender_callback, machine_identity=False
        )
        receiver = SynapseAgent(
            RECEIVER,
            uri=uri,
            delivery_capabilities={"next_turn": "emulated"},
            machine_identity=False,
        )
        bridge = DeliveryParticipantBridge(receiver, participant, ledger_path=ledger)

        async def receiver_callback(frame: dict[str, Any]) -> None:
            if frame.get("type") == MessageType.DELIVERY_OFFER:
                await offers.put(frame)
            else:
                await bridge.on_message(frame)

        receiver.callback = receiver_callback
        sender_task = asyncio.create_task(sender.connect())
        receiver_task = asyncio.create_task(receiver.connect())
        try:
            await asyncio.wait_for(receiver.delivery_ready_event.wait(), 5)
            assert await sender.wait_until_ready()
            # The executor is not started, so 128 accepted offers fill the queue.
            for index in range(128):
                await bridge.on_message(_offer(receiver, f"{index:064x}"))
            assert bridge._queue.full()
            key = await sender.request_delivery(
                target=RECEIVER,
                target_incarnation=receiver.delivery_incarnation,
                mode="next_turn",
                body="This offer arrives at a full queue.",
                deadline=time.time() + 60,
                request_id="full-req",
                idempotency_key="full-idem",
            )
            offer = await asyncio.wait_for(offers.get(), 5)
            assert offer["operation_key"] == key
            rejecting = asyncio.create_task(bridge.on_message(offer))
            await asyncio.sleep(0)
            # The rejection report now waits for the hub. A refusal of another
            # request reaches the recipient first and must not settle this stage.
            waiter = bridge._stage_waiter
            assert waiter is not None
            await bridge.on_message(
                {
                    "type": MessageType.DELIVERY_REFUSED,
                    "target": RECEIVER,
                    "request_id": "another-request",
                    "reason_code": "state_conflict",
                }
            )
            assert not waiter[3].done()
            await asyncio.wait_for(rejecting, 10)
            assert _ledger_row(ledger, key) == ("rejected", "queue_full")
            assert key not in bridge._queued
            await sender.request_delivery_status(key)
            while True:
                status = await asyncio.wait_for(sender_frames.get(), 5)
                if (
                    status.get("type") == MessageType.DELIVERY_STATUS
                    and status.get("operation_key") == key
                    and status.get("stage") == "rejected"
                    and "task_completed" in status
                ):
                    break
            assert not status["task_completed"]
            assert participant.calls == 0
            # The guard above is live: a provider turn would have failed the journey.
            with pytest.raises(AssertionError, match="provider turn started for probe"):
                await participant.take_turn(TurnRequest(topic_id="probe", prompt=""))
            assert participant.calls == 1
        finally:
            await _stop(sender_task)
            await _stop(receiver_task)
            await bridge.close()
