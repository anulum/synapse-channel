# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — one connection that cannot be written to never stops hub work
"""Background sweeps survive a connection that is already going away."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest
from websockets.exceptions import ConnectionClosedOK

from hub_e2e_helpers import _await_listening, _free_port, read_until_type, running_hub
from synapse_channel.core.frame_signature_gate import KEY_UNTRUSTED, FrameSignatureGate
from synapse_channel.core.frame_signing import FrameSigningSessions
from synapse_channel.core.handlers.delivery_modes import expire_due_deliveries
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.message_auth import EventSignatureTrustBundle
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from test_delivery_hub import _intent, _register


class _GoneSocket:
    """A connection whose every write fails, as one that is closing does."""

    remote_address = ("127.0.0.1", 0)

    def __init__(self) -> None:
        self.attempts = 0

    async def send(self, _raw: str) -> None:
        self.attempts += 1
        raise ConnectionClosedOK(None, None)


@pytest.mark.real_hub
async def test_an_expiry_notification_that_cannot_be_written_stays_pending(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "hub.db")
    async with running_hub(SynapseHub(journal=store, hub_id="hub-1")) as (hub, uri):
        receiver, session = await _register(uri, "P/receiver", capabilities={"follow_up": "native"})
        sender, _ = await _register(uri, "P/author")
        try:
            assert session is not None
            request = _intent(session["incarnation"])
            request["deadline"] = time.time() + 0.2
            await sender.send(json.dumps(request))
            await read_until_type(sender, MessageType.DELIVERY_STATUS)
            await read_until_type(receiver, MessageType.DELIVERY_OFFER)
            await asyncio.sleep(0.25)
            # The author's connection is going away: every write to it fails.
            gone = _GoneSocket()
            hub.clients.agent_sockets["P/author"] = gone
            assert await expire_due_deliveries(hub) == 1
            assert gone.attempts == 1
            assert hub.journal is not None
            pending = hub.journal.delivery.pending_notifications("P/author")
            assert [frame["stage"] for _row, _id, frame in pending][-1] == "expired"
            # The hub still serves the receiver.
            await receiver.send(
                json.dumps(
                    {
                        "sender": "P/receiver",
                        "type": MessageType.WHO_REQUEST,
                        "target": "System",
                    }
                )
            )
            await read_until_type(receiver, MessageType.WHO_SNAPSHOT)
        finally:
            await sender.close()
            await receiver.close()


async def test_a_key_lookup_that_fails_closes_the_connection() -> None:
    sent: list[dict[str, Any]] = []
    closed: list[Any] = []

    async def send_json(_socket: Any, frame: dict[str, Any]) -> None:
        sent.append(frame)

    async def close_socket(socket: Any, *, code: int, reason: str) -> None:
        closed.append(socket)

    def broken_bundle() -> EventSignatureTrustBundle:
        raise OSError("the trust store could not be read")

    sessions = FrameSigningSessions()
    gate = FrameSignatureGate(
        sessions=sessions,
        hub_id="h",
        enabled=lambda: True,
        bundle=broken_bundle,
        pins=None,
        send_json=send_json,
        system=lambda payload, **fields: {"payload": payload, **fields},
        close_socket=close_socket,
        count_refusal=lambda: None,
    )
    socket = object()
    sessions.record(socket, "P/a", "k", "bundle")
    assert await gate.recheck() == 1
    assert closed == [socket]
    assert sent[0]["verification_result"] == KEY_UNTRUSTED


async def test_a_delivery_sweep_whose_store_fails_stops_the_hub_loudly(tmp_path: Path) -> None:
    """A deadline sweep must not stop quietly: a store failure ends ``serve`` with its error."""
    store = EventStore(tmp_path / "hub.db")
    hub = SynapseHub(journal=store, hub_id="hub-1")
    port = _free_port()
    task = asyncio.create_task(hub.serve("localhost", port))
    await _await_listening(port)
    store.close()
    with pytest.raises(sqlite3.ProgrammingError):
        await asyncio.wait_for(task, 15.0)
