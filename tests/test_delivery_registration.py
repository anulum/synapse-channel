# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — authenticated delivery-session registration tests
"""Verify session advertisements remain tied to durable named connections."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.client import connect

from hub_e2e_helpers import read_until_type, running_hub
from synapse_channel.core.delivery_modes import DeliveryRefusal
from synapse_channel.core.delivery_registration import bind_delivery_registration
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.hub_clients import HubClientRegistry
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType


def _bound() -> tuple[HubClientRegistry, object]:
    """Construct one already authenticated and name-bound registry socket."""
    clients = HubClientRegistry(
        max_clients=2,
        max_unauth_clients=2,
        max_connections_per_host=None,
        takeover_cooldown=0.0,
        clock=lambda: 100.0,
    )
    websocket = object()
    clients.socket_agent[websocket] = "P/receiver"
    clients.set_agent_socket("P/receiver", websocket)
    return clients, websocket


def _register(
    clients: HubClientRegistry,
    websocket: object,
    frame: dict[str, Any],
    **changes: Any,
) -> Any:
    """Call the registration gate with production-equivalent default context."""
    options: dict[str, Any] = {
        "sender": "P/receiver",
        "websocket": websocket,
        "data": frame,
        "msg_type": MessageType.HEARTBEAT,
        "was_bound": False,
        "durable": True,
        "stable_hub_id": "hub-1",
    }
    options.update(changes)
    return bind_delivery_registration(clients, **options)


def test_v3_registration_strips_token_and_binds_one_live_session() -> None:
    """Sensitive process token is removed before later route and logging paths."""
    clients, websocket = _bound()
    frame = {
        "protocol_version": 3,
        "delivery_session_token": "a" * 64,
        "delivery_capabilities": {"next_turn": "emulated"},
    }
    session = _register(clients, websocket, frame)
    assert session is not None
    assert len(session.incarnation) == 64
    assert clients.delivery_session("P/receiver") == session
    assert "delivery_session_token" not in frame
    assert "delivery_capabilities" not in frame


def test_sender_only_registration_requires_no_recipient_session() -> None:
    """A v3 sender can connect without advertising execution capabilities."""
    clients, websocket = _bound()
    assert _register(clients, websocket, {"protocol_version": 3}) is None
    assert clients.delivery_session("P/receiver") is None


@pytest.mark.parametrize(
    ("frame", "changes", "code"),
    [
        (
            {"protocol_version": 2, "delivery_session_token": "a" * 64},
            {},
            "unsupported_protocol",
        ),
        (
            {"protocol_version": True, "delivery_session_token": "a" * 64},
            {},
            "unsupported_protocol",
        ),
        (
            {"protocol_version": 3, "delivery_capabilities": {}},
            {},
            "invalid_shape",
        ),
        (
            {"protocol_version": 3, "delivery_session_token": "a" * 64},
            {"durable": False},
            "unsupported_profile",
        ),
        (
            {"protocol_version": 3, "delivery_session_token": "a" * 64},
            {"stable_hub_id": None},
            "unsupported_profile",
        ),
        (
            {"protocol_version": 3, "delivery_session_token": "a" * 64},
            {"was_bound": True},
            "invalid_shape",
        ),
        (
            {"protocol_version": 3, "delivery_session_token": "a" * 64},
            {"msg_type": MessageType.CHAT},
            "invalid_shape",
        ),
    ],
)
def test_invalid_registration_refuses_without_retaining_token(
    frame: dict[str, Any], changes: dict[str, Any], code: str
) -> None:
    """Wrong profile, version, or registration boundary cannot gain capability."""
    clients, websocket = _bound()
    with pytest.raises(DeliveryRefusal) as failure:
        _register(clients, websocket, frame, **changes)
    assert failure.value.code == code
    assert "delivery_session_token" not in frame
    assert clients.delivery_session("P/receiver") is None


@pytest.mark.real_hub
async def test_real_hub_binds_and_reports_a_v3_delivery_session(tmp_path: Path) -> None:
    """A live WebSocket registration emits the bound incarnation, never the token."""
    store = EventStore(tmp_path / "events.db")
    async with running_hub(SynapseHub(journal=store, hub_id="hub-1")) as (hub, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, MessageType.WELCOME)
            await websocket.send(
                json.dumps(
                    {
                        "sender": "P/receiver",
                        "type": MessageType.HEARTBEAT,
                        "target": "System",
                        "payload": "online",
                        "protocol_version": 3,
                        "delivery_session_token": "a" * 64,
                        "delivery_capabilities": {"next_turn": "emulated"},
                    }
                )
            )
            frame = await read_until_type(websocket, MessageType.DELIVERY_SESSION)
            session = hub.clients.delivery_session("P/receiver")
            assert session is not None
            assert frame["incarnation"] == session.incarnation
            assert frame["capabilities"] == {"next_turn": "emulated"}
            assert "delivery_session_token" not in json.dumps(frame)
            assert "a" * 64 not in json.dumps(frame)


@pytest.mark.real_hub
async def test_real_hub_refuses_delivery_advertisement_without_durable_profile() -> None:
    """A normal v2 hub cannot claim queue or restart guarantees."""
    async with running_hub(SynapseHub()) as (hub, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, MessageType.WELCOME)
            await websocket.send(
                json.dumps(
                    {
                        "sender": "P/receiver",
                        "type": MessageType.HEARTBEAT,
                        "target": "System",
                        "protocol_version": 3,
                        "delivery_session_token": "a" * 64,
                        "delivery_capabilities": {},
                    }
                )
            )
            frame = await read_until_type(websocket, MessageType.ERROR)
            assert frame["reason_code"] == "unsupported_profile"
            assert hub.clients.delivery_session("P/receiver") is None
