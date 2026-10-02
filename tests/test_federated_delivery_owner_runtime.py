# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — receiving-hub ownership runtime regressions
"""Exercise delivery deadlines and journal identity through two actual TLS hubs."""

from __future__ import annotations

import asyncio
import json
import ssl
import time
from pathlib import Path

import pytest
from websockets.asyncio.client import connect

from hub_e2e_helpers import read_until_type
from synapse_channel.core.delivery_modes import DeliveryRefusal
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from test_message_forward_e2e import _agent, _two_hubs, _who


@pytest.mark.real_hub
async def test_receiving_hub_expires_a_forwarded_delivery(tmp_path: Path) -> None:
    """The recipient journal expires remote work without changing its provenance."""
    async with _two_hubs(tmp_path) as pair:
        receiver = await _agent(
            pair.lp_uri, pair.material.ca, "PROJ/bob", capabilities={"next_turn": "emulated"}
        )
        sender = await _agent(pair.ws_uri, pair.material.ca, "PROJ/alice")
        try:
            roster = await _who(sender, "PROJ/alice", "laptop")
            session = roster["delivery_sessions"]["PROJ/bob@laptop"]
            deadline = time.time() + 1.0
            await sender.send(
                json.dumps(
                    {
                        "sender": "PROJ/alice",
                        "type": MessageType.DELIVERY_REQUEST,
                        "target": "PROJ/bob@laptop",
                        "protocol_version": 3,
                        "request_id": "owner-expiry",
                        "idempotency_key": "owner-expiry",
                        "target_incarnation": session["incarnation"],
                        "mode": "next_turn",
                        "allowed_fallbacks": [],
                        "task_id": "OWNER-EXPIRY",
                        "body": "Observe the expiry; do not execute a task.",
                        "deadline": deadline,
                    }
                )
            )
            queued = await read_until_type(sender, MessageType.DELIVERY_STATUS, limit=60)
            assert queued["stage"] == "queued"
            offer = await read_until_type(receiver, MessageType.DELIVERY_OFFER, limit=60)
            assert offer["operation_key"] == queued["operation_key"]
            assert offer["origin_hub"] == "workstation"
            await asyncio.sleep(max(0.0, deadline - time.time()) + 0.25)
            await sender.send(
                json.dumps(
                    {
                        "sender": "PROJ/alice",
                        "type": MessageType.DELIVERY_STATUS_REQUEST,
                        "target": "System",
                        "protocol_version": 3,
                        "operation_key": queued["operation_key"],
                    }
                )
            )
            observed = await read_until_type(sender, MessageType.DELIVERY_STATUS, limit=60)
            assert observed["operation_key"] == queued["operation_key"]
            assert observed["stage"] == "expired"
            record = pair.stores["laptop"].delivery.get(queued["operation_key"])
            assert record is not None and record.stage == "expired"
            assert record.request["origin_hub"] == "workstation"
            assert record.sender == "PROJ/alice@workstation"
        finally:
            await receiver.close()
            await sender.close()

    with EventStore(tmp_path / "laptop.db") as recovered:
        recovered.delivery.verify_origin_hub("laptop")
        replayed = recovered.delivery.get(queued["operation_key"])
        assert replayed is not None and replayed.stage == "expired"


@pytest.mark.real_hub
async def test_forwarded_only_journal_refuses_another_receiving_hub(tmp_path: Path) -> None:
    """A fresh process must retain the actual receiver's durable hub identity."""
    async with _two_hubs(tmp_path) as pair:
        receiver = await _agent(
            pair.lp_uri, pair.material.ca, "PROJ/bob", capabilities={"next_turn": "emulated"}
        )
        sender = await _agent(pair.ws_uri, pair.material.ca, "PROJ/alice")
        try:
            roster = await _who(sender, "PROJ/alice", "laptop")
            await sender.send(
                json.dumps(
                    {
                        "sender": "PROJ/alice",
                        "type": MessageType.DELIVERY_REQUEST,
                        "target": "PROJ/bob@laptop",
                        "protocol_version": 3,
                        "request_id": "receiver-binding",
                        "idempotency_key": "receiver-binding",
                        "target_incarnation": roster["delivery_sessions"]["PROJ/bob@laptop"][
                            "incarnation"
                        ],
                        "mode": "next_turn",
                        "allowed_fallbacks": [],
                        "task_id": "OWNER-BINDING",
                        "body": "Retain receiving-hub ownership across a restart.",
                        "deadline": time.time() + 120.0,
                    }
                )
            )
            queued = await read_until_type(sender, MessageType.DELIVERY_STATUS, limit=60)
            assert queued["stage"] == "queued"
            await read_until_type(receiver, MessageType.DELIVERY_OFFER, limit=60)
        finally:
            await receiver.close()
            await sender.close()

    with EventStore(tmp_path / "laptop.db") as recovered:
        changed = SynapseHub(journal=recovered, hub_id="different-receiving-hub")
        with pytest.raises(DeliveryRefusal, match="different stable hub id") as refusal:
            await asyncio.wait_for(changed.serve("localhost", 0), timeout=0.5)
        assert refusal.value.code == "hub_identity_mismatch"
        record = recovered.delivery.get(queued["operation_key"])
        assert record is not None and record.stage == "queued"


@pytest.mark.real_hub
async def test_receiving_hub_supersedes_forwarded_work_for_a_replaced_session(
    tmp_path: Path,
) -> None:
    """A new real recipient registration resolves the old remote queued operation."""
    async with _two_hubs(tmp_path) as pair:
        receiver = await _agent(
            pair.lp_uri, pair.material.ca, "PROJ/bob", capabilities={"next_turn": "emulated"}
        )
        sender = await _agent(pair.ws_uri, pair.material.ca, "PROJ/alice")
        replacement = None
        try:
            roster = await _who(sender, "PROJ/alice", "laptop")
            old_incarnation = roster["delivery_sessions"]["PROJ/bob@laptop"]["incarnation"]
            await sender.send(
                json.dumps(
                    {
                        "sender": "PROJ/alice",
                        "type": MessageType.DELIVERY_REQUEST,
                        "target": "PROJ/bob@laptop",
                        "protocol_version": 3,
                        "request_id": "owner-supersede",
                        "idempotency_key": "owner-supersede",
                        "target_incarnation": old_incarnation,
                        "mode": "next_turn",
                        "allowed_fallbacks": [],
                        "task_id": "OWNER-SUPERSEDE",
                        "body": "Resolve this old session without executing it.",
                        "deadline": time.time() + 120.0,
                    }
                )
            )
            queued = await read_until_type(sender, MessageType.DELIVERY_STATUS, limit=60)
            assert queued["stage"] == "queued"
            await read_until_type(receiver, MessageType.DELIVERY_OFFER, limit=60)
            await receiver.close()
            replacement = await connect(
                pair.lp_uri, ssl=ssl.create_default_context(cafile=str(pair.material.ca))
            )
            await read_until_type(replacement, MessageType.WELCOME)
            await replacement.send(
                json.dumps(
                    {
                        "sender": "PROJ/bob",
                        "type": MessageType.HEARTBEAT,
                        "target": "System",
                        "payload": "online",
                        "protocol_version": 3,
                        "delivery_session_token": "c" * 64,
                        "delivery_capabilities": {"next_turn": "emulated"},
                    }
                )
            )
            await read_until_type(replacement, MessageType.DELIVERY_SESSION, limit=60)
            await sender.send(
                json.dumps(
                    {
                        "sender": "PROJ/alice",
                        "type": MessageType.DELIVERY_STATUS_REQUEST,
                        "target": "System",
                        "protocol_version": 3,
                        "operation_key": queued["operation_key"],
                    }
                )
            )
            observed = await read_until_type(sender, MessageType.DELIVERY_STATUS, limit=60)
            assert observed["stage"] == "superseded"
            record = pair.stores["laptop"].delivery.get(queued["operation_key"])
            assert record is not None and record.receiving_hub == "laptop"
            assert record.request["origin_hub"] == "workstation"
            assert record.request["target_incarnation"] == old_incarnation
        finally:
            await receiver.close()
            await sender.close()
            if replacement is not None:
                await replacement.close()
    with EventStore(tmp_path / "laptop.db") as recovered:
        recovered.delivery.verify_origin_hub("laptop")
        record = recovered.delivery.get(queued["operation_key"])
        assert record is not None and record.stage == "superseded"
