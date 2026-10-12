# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real delivery-registration lifecycle tests
"""Exercise delivery registration through the serving hub and live sockets."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedError

from hub_e2e_helpers import read_until_type, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore


async def test_refused_delivery_profile_closes_the_socket_and_releases_its_slot() -> None:
    hub = SynapseHub(max_connections_per_host=1)
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as refused:
            await read_until_type(refused, "welcome")
            await refused.send(
                json.dumps(
                    {
                        "sender": "REGISTER/receiver",
                        "type": "heartbeat",
                        "protocol_version": 3,
                        "delivery_session_token": "a" * 64,
                        "delivery_capabilities": {},
                    }
                )
            )
            error = await read_until_type(refused, "error")
            assert error["reason_code"] == "unsupported_profile"
            with pytest.raises(ConnectionClosedError) as closed:
                await refused.recv()
            assert closed.value.rcvd is not None and closed.value.rcvd.code == 4020
        async with connect(uri) as replacement:
            await read_until_type(replacement, "welcome")
            await replacement.send(
                json.dumps({"sender": "REGISTER/replacement", "type": "heartbeat"})
            )
            joined = await read_until_type(replacement, "presence_update")
            assert joined["agent"] == "REGISTER/replacement"
            assert hub.clients.delivery_session("REGISTER/receiver") is None


async def test_reconnecting_receiver_advertises_its_new_incarnation_only(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    incarnations: list[str] = []
    try:
        async with running_hub(SynapseHub(journal=store, hub_id="registration")) as (hub, uri):
            for token in ("a" * 64, "b" * 64):
                async with connect(uri) as websocket:
                    await read_until_type(websocket, "welcome")
                    await websocket.send(
                        json.dumps(
                            {
                                "sender": "REGISTER/receiver",
                                "type": "heartbeat",
                                "protocol_version": 3,
                                "delivery_session_token": token,
                                "delivery_capabilities": {"next_turn": "emulated"},
                            }
                        )
                    )
                    registered = await read_until_type(websocket, "delivery_session")
                    session = hub.clients.delivery_session("REGISTER/receiver")
                    assert session is not None
                    assert registered["incarnation"] == session.incarnation
                    assert registered["capabilities"] == {"next_turn": "emulated"}
                    assert token not in json.dumps(registered)
                    incarnations.append(session.incarnation)
    finally:
        store.close()
    assert len(incarnations) == 2 and incarnations[0] != incarnations[1]
