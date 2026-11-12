# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — untrusted pending work response contracts
"""Validate correlated native recovery responses before writing observer state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.server import ServerConnection, serve

from hub_e2e_helpers import running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.pending_work import PendingItem, PendingQuery
from synapse_channel.core.persistence import EventStore
from synapse_channel.mcp.bridge import SynapseHubBridge
from test_mcp_activity_page import connected_bridge
from test_pending_work_network import seed_legacy


@pytest.mark.parametrize(
    "changes",
    [
        {"identity": None},
        {"identity": "x" * 257},
        {"hub_id": None},
        {"hub_id": "x" * 257},
        {"limit": -1},
    ],
)
def test_query_field_bounds(changes: dict[str, Any]) -> None:
    values: dict[str, Any] = {"identity": "ALPHA/worker", "hub_id": "", "limit": 1024}
    with pytest.raises(ValueError, match="query"):
        PendingQuery(**(values | changes))


@pytest.mark.parametrize("kind,key", [("unknown", "a" * 64), ("tasks", None), ("tasks", "PRIVATE")])
def test_pending_item_bounds(kind: Any, key: Any) -> None:
    with pytest.raises(ValueError, match="item"):
        PendingItem(kind, key)


async def test_real_bridge_recovers_exact_metadata_on_its_existing_connection(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "hub.db")
    seed_legacy(store)
    hub = SynapseHub(journal=store, hub_id="pending-client", anti_rollback_checkpoint=False)
    try:
        async with running_hub(hub) as (_, uri):
            async with connected_bridge(uri) as bridge:
                result = await bridge.pending_work()
                assert result.identity == "ALPHA/worker" and result.hub_id == "pending-client"
                assert [x.kind for x in result.items] == ["handoffs", "tasks"]
                assert await bridge.pending_work(hub_id=result.hub_id) == result
                assert set(hub.agent_sockets) == {"ALPHA/worker"}
    finally:
        store.close()


@pytest.mark.parametrize(
    "changes",
    [
        {"version": True},
        {"version": 2},
        {"available": False},
        {"identity": "foreign"},
        {"hub_id": "foreign"},
        {"extra": "private"},
        {"items": None},
        {"items": [None]},
        {"items": [{"kind": "tasks", "key": "a" * 64, "body": "private"}]},
        {"items": [{"kind": "tasks", "key": "a" * 64}] * 1025},
        {"items": [{"kind": "tasks", "key": "a" * 64}] * 2},
        {"items": [{"kind": "tasks", "key": "b" * 64}, {"kind": "tasks", "key": "a" * 64}]},
    ],
)
async def test_foreign_or_malformed_real_remote_response_refuses(changes: dict[str, Any]) -> None:
    async def endpoint(socket: ServerConnection) -> None:
        await socket.send(json.dumps({"type": "welcome", "hub_id": "test-hub"}))
        async for raw in socket:
            request = json.loads(raw)
            if request.get("type") != "history_request":
                continue
            response = {
                "version": 1,
                "available": True,
                "identity": "ALPHA/worker",
                "hub_id": "test-hub",
                "items": [{"kind": "tasks", "key": "a" * 64}],
            } | changes
            await socket.send(
                json.dumps(
                    {
                        "type": "history_snapshot",
                        "target": "ALPHA/worker",
                        "request_id": request["request_id"],
                        "pending_work": response,
                    }
                )
            )

    async with serve(endpoint, "127.0.0.1", 0) as server:
        async with connected_bridge(
            f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        ) as bridge:
            with pytest.raises(RuntimeError, match="observer unchanged"):
                await bridge.pending_work()


async def test_disconnected_bridge_does_not_open_a_recovery_socket() -> None:
    bridge = SynapseHubBridge(uri="ws://127.0.0.1:1", name="ALPHA/worker", request_timeout=0.1)
    with pytest.raises(RuntimeError, match="observer unchanged"):
        await bridge.pending_work()
    assert bridge.agent.connection is None


@pytest.mark.parametrize("mode", ["foreign-target", "uncorrelated", "hub-change", "legacy"])
async def test_response_correlation_and_hub_continuity_are_required(mode: str) -> None:
    async def endpoint(socket: ServerConnection) -> None:
        await socket.send(json.dumps({"type": "welcome", "hub_id": "test-hub"}))
        async for raw in socket:
            request = json.loads(raw)
            if request.get("type") != "history_request":
                continue
            if mode == "hub-change":
                await socket.send(json.dumps({"type": "welcome", "hub_id": "new-hub"}))
            response: dict[str, Any] = {
                "type": "history_snapshot",
                "target": "ALPHA/foreign" if mode == "foreign-target" else "ALPHA/worker",
                "request_id": "unrelated" if mode == "uncorrelated" else request["request_id"],
            }
            if mode != "legacy":
                response["pending_work"] = {
                    "version": 1,
                    "available": True,
                    "identity": "ALPHA/worker",
                    "hub_id": "test-hub",
                    "items": [],
                }
            await socket.send(json.dumps(response))

    async with serve(endpoint, "127.0.0.1", 0) as server:
        async with connected_bridge(
            f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        ) as bridge:
            with pytest.raises(RuntimeError, match="observer unchanged"):
                await bridge.pending_work()
