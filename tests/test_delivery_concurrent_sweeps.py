# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — concurrent durable delivery terminal sweeps
"""Keep expiry and session replacement idempotent across real asynchronous callbacks."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Literal

import pytest
from websockets.asyncio.client import ClientConnection, connect

from hub_e2e_helpers import read_until_type, running_hub
from synapse_channel.core.handlers.delivery_modes import (
    expire_due_deliveries,
    supersede_old_delivery_sessions,
)
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from test_delivery_hub import _intent, _register


async def _queue_pair(
    sender: ClientConnection,
    receiver: ClientConnection,
    incarnation: str,
    deadline: float,
) -> list[str]:
    """Admit two distinct operations through the live registered sockets."""
    keys: list[str] = []
    for index in (1, 2):
        request = _intent(incarnation)
        request.update(
            request_id=f"req-{index}",
            idempotency_key=f"idem-{index}",
            task_id=f"T-{index}",
            deadline=deadline,
        )
        await sender.send(json.dumps(request))
        queued = await read_until_type(sender, MessageType.DELIVERY_STATUS)
        offered = await read_until_type(receiver, MessageType.DELIVERY_OFFER)
        key = queued["operation_key"]
        assert isinstance(key, str) and offered["operation_key"] == key
        assert queued["stage"] == "queued" and queued["task_completed"] is False
        keys.append(key)
    assert len(set(keys)) == 2
    return keys


async def _assert_terminal_pair(
    store: EventStore,
    sender: ClientConnection,
    receiver: ClientConnection,
    keys: list[str],
    stage: Literal["expired", "superseded"],
) -> None:
    """Read exact terminal statuses and prove one durable transition per admitted key."""
    notices = [await read_until_type(sender, MessageType.DELIVERY_STATUS) for _ in keys]
    assert {notice["operation_key"] for notice in notices} == set(keys)
    assert all(notice["stage"] == stage for notice in notices)
    for key in keys:
        await sender.send(
            json.dumps(
                {
                    "sender": "P/author",
                    "type": MessageType.DELIVERY_STATUS_REQUEST,
                    "target": "System",
                    "protocol_version": 3,
                    "operation_key": key,
                }
            )
        )
        status = await read_until_type(sender, MessageType.DELIVERY_STATUS)
        assert status["operation_key"] == key and status["stage"] == stage
        assert status["task_completed"] is False
        assert status["boundary_delivered"] is False and status["explicitly_acknowledged"] is False
        record = store.delivery.get(key)
        assert record is not None and record.stage == stage and record.ordinal == 2
    kinds = [event.kind for event in store.iter_events()]
    assert kinds.count("delivery_intent_accepted") == 2
    assert kinds.count("delivery_intent_queued") == 2
    assert kinds.count("delivery_intent_transition") == 2
    assert len(kinds) == 6
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(read_until_type(sender, MessageType.DELIVERY_STATUS), 0.1)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(read_until_type(receiver, MessageType.DELIVERY_OFFER), 0.1)


@pytest.mark.real_hub
async def test_concurrent_expiry_callbacks_commit_each_deadline_once(tmp_path: Path) -> None:
    """Two actual expiry callbacks cannot duplicate durable transitions or notifications."""
    with EventStore(tmp_path / "hub.db") as store:
        async with running_hub(SynapseHub(journal=store, hub_id="hub-1")) as (hub, uri):
            receiver, session = await _register(
                uri, "P/receiver", capabilities={"follow_up": "native"}
            )
            sender, _ = await _register(uri, "P/author")
            try:
                assert session is not None and isinstance(session["incarnation"], str)
                deadline = time.time() + 3
                keys = await _queue_pair(sender, receiver, session["incarnation"], deadline)
                await asyncio.sleep(max(0, deadline - time.time()) + 0.05)

                results = await asyncio.gather(
                    expire_due_deliveries(hub), expire_due_deliveries(hub)
                )

                assert all(0 <= count <= 2 for count in results) and sum(results) <= 2
                await _assert_terminal_pair(store, sender, receiver, keys, "expired")
            finally:
                await sender.close()
                await receiver.close()


@pytest.mark.real_hub
async def test_reconnect_and_concurrent_supersession_keep_old_work_terminal(tmp_path: Path) -> None:
    """A real new registration and repeated callbacks resolve old offers exactly once."""
    with EventStore(tmp_path / "hub.db") as store:
        async with running_hub(SynapseHub(journal=store, hub_id="hub-1")) as (hub, uri):
            receiver, session = await _register(
                uri, "P/receiver", capabilities={"follow_up": "native"}
            )
            sender, _ = await _register(uri, "P/author")
            try:
                assert session is not None and isinstance(session["incarnation"], str)
                keys = await _queue_pair(
                    sender, receiver, session["incarnation"], time.time() + 120
                )
                await receiver.close()
                deadline = asyncio.get_running_loop().time() + 3
                while "P/receiver" in hub.clients.agent_sockets:
                    assert asyncio.get_running_loop().time() < deadline
                    await asyncio.sleep(0)
                receiver = await connect(uri)
                await read_until_type(receiver, MessageType.WELCOME)
                await receiver.send(
                    json.dumps(
                        {
                            "sender": "P/receiver",
                            "type": MessageType.HEARTBEAT,
                            "target": "System",
                            "protocol_version": 3,
                            "delivery_session_token": "b" * 64,
                            "delivery_capabilities": {"follow_up": "native"},
                        }
                    )
                )
                deadline = asyncio.get_running_loop().time() + 3
                while True:
                    current = hub.clients.delivery_session("P/receiver")
                    if current is not None and current.incarnation != session["incarnation"]:
                        break
                    assert asyncio.get_running_loop().time() < deadline
                    await asyncio.sleep(0)

                await asyncio.gather(
                    supersede_old_delivery_sessions(
                        hub, target="P/receiver", incarnation=current.incarnation
                    ),
                    supersede_old_delivery_sessions(
                        hub, target="P/receiver", incarnation=current.incarnation
                    ),
                )

                registered = await read_until_type(receiver, MessageType.DELIVERY_SESSION)
                assert registered["incarnation"] == current.incarnation
                await _assert_terminal_pair(store, sender, receiver, keys, "superseded")
            finally:
                await sender.close()
                await receiver.close()
