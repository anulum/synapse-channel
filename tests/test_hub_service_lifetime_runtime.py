# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — background services without a hub-owned network listener
"""Exercise real expiry, retry, key refusal and resource failure through services."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest
from websockets.asyncio.client import connect

from hub_e2e_helpers import read_until_type
from hub_service_lifetime_examples import client_transport, running_services, until
from message_forward_peer_helpers import result_frame, scripted_peer
from synapse_channel.core.frame_signature_gate import KEY_UNTRUSTED
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.hub_identity_gate import IDENTITY_BINDING_CLOSE_CODE
from synapse_channel.core.message_forward_transport import MessageForwardPeer
from synapse_channel.core.persistence import EventStore
from test_delivery_persistence import _create, _intent
from test_frame_signature_gate import _bundle, _hub, _register
from test_message_forward_retry_isolation import _enqueue


async def test_services_expire_and_retry_using_the_same_durable_store(tmp_path: Path) -> None:
    """Actual expiry and peer retry run without calling public hub serving."""
    path = tmp_path / "services.db"
    intent = _intent()
    async with scripted_peer(lambda frame: result_frame(frame["forward_id"])) as peer:
        with EventStore(path) as store:
            _create(store, intent)
            hub = SynapseHub(
                hub_id="hub-1", journal=store, message_peers={"peer": MessageForwardPeer(peer.uri)}
            )
            _enqueue(hub, "a" * 32, "peer")

            def settled() -> bool:
                """Observe both independent services' actual durable results."""
                delivery = store.delivery.get(intent.operation_key)
                forward = store.message_forward.outbox_entry("a" * 32)
                return (
                    delivery is not None
                    and delivery.stage == "expired"
                    and forward is not None
                    and forward.state == "accepted"
                )

            async with running_services(hub) as stop:
                with pytest.raises(TimeoutError):
                    await hub.wait_until_serving(timeout=0.01)
                await until(settled)
                assert not stop.is_set()
                assert len(peer.received) == 1
            assert settled()
            assert hub._live_checkpoint is None
            store.delivery.verify_replay()
        with EventStore(path) as reopened:
            delivery = reopened.delivery.get(intent.operation_key)
            forward = reopened.message_forward.outbox_entry("a" * 32)
            assert delivery is not None and delivery.stage == "expired"
            assert forward is not None and forward.state == "accepted" and forward.attempts == 1


async def test_service_key_recheck_closes_a_real_idle_connection() -> None:
    """A current revoked bundle closes an idle socket through the real recheck loop.

    The fixture owns the network listener. The shared lifetime owns neither
    that socket nor a public bound address, and is not an isolation claim.
    """
    hub = _hub()
    async with client_transport(hub) as uri:
        async with connect(uri) as websocket:
            await _register(websocket)
            hub._identity_gate.replace_trust_bundle(_bundle(revoked=True))
            async with running_services(hub) as stop:
                refusal = await asyncio.wait_for(
                    read_until_type(websocket, "error", timeout=15), 15
                )
                assert refusal["verification_result"] == KEY_UNTRUSTED
                await asyncio.wait_for(websocket.wait_closed(), 5)
                assert websocket.close_code == IDENTITY_BINDING_CLOSE_CODE
                assert not stop.is_set()
        await until(lambda: not hub.clients.connected_clients)
    assert hub.counters.auth_failures == 1


async def test_service_storage_failure_requests_stop_and_keeps_original_error(
    tmp_path: Path,
) -> None:
    """A genuinely closed journal stops services and propagates its SQLite error."""
    store = EventStore(tmp_path / "closed.db")
    hub = SynapseHub(hub_id="hub-1", journal=store, anti_rollback_checkpoint=False)
    _create(store, _intent())
    tasks_before = asyncio.all_tasks()
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        async with running_services(hub) as stop:
            store.close()
            await asyncio.wait_for(stop.wait(), 5)
    assert asyncio.all_tasks() <= tasks_before


async def test_finishing_services_leaves_caller_journal_usable(tmp_path: Path) -> None:
    """Repeated service lifetimes drain while the caller retains its exact store."""
    with EventStore(tmp_path / "owned.db") as store:
        hub = SynapseHub(hub_id="service-owner", journal=store, anti_rollback_checkpoint=False)
        tasks_before = asyncio.all_tasks()
        for _ in range(2):
            async with running_services(hub) as stop:
                assert not stop.is_set()
                await asyncio.sleep(0)
            assert asyncio.all_tasks() <= tasks_before
            assert store.max_seq() == 0
