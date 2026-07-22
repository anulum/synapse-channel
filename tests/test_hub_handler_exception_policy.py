# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real-frame evidence for the hub handler exception boundary
"""Prove malformed handler input stays local without masking integrity failures."""

from __future__ import annotations

import json

import pytest
from websockets.asyncio.client import connect

from hub_e2e_helpers import read_until_type, running_hub, send_json
from synapse_channel.core.handlers import DISPATCH
from synapse_channel.core.hub import SynapseHub


def _malformed_shape_frame(message_type: str) -> dict[str, object]:
    """Build a valid envelope with deliberately mistyped handler-owned fields."""
    return {
        "sender": "agent-a",
        "type": message_type,
        "payload": "",
        "task_id": {"unexpected": ["shape"]},
        "paths": {"unexpected": "shape"},
        "expected_version": {"unexpected": 1},
        "limit": {"unexpected": 1},
        "capacity": {"unexpected": 1},
        "meta": ["unexpected"],
        "depends_on": {"unexpected": 1},
        "roles": {"unexpected": 1},
        "freshness_seconds": {"unexpected": 1},
        "returned_claim_ids": {"unexpected": 1},
        "contracts": {"unexpected": 1},
        "signature": {"unexpected": 1},
    }


@pytest.mark.parametrize("message_type", sorted(DISPATCH), ids=str)
async def test_registered_handler_rejects_malformed_shape_without_losing_connection(
    message_type: str,
) -> None:
    """Every registered handler preserves a connection after benign malformed input."""
    hub = SynapseHub(hub_id="handler-exception-policy")
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await send_json(websocket, sender="agent-a", type="heartbeat", payload="online")
            await websocket.send(json.dumps(_malformed_shape_frame(message_type)))
            await send_json(
                websocket,
                sender="agent-a",
                type="state_request",
                target="System",
                payload="",
            )
            snapshot = await read_until_type(websocket, "state_snapshot", limit=50)

    assert snapshot["target"] == "agent-a"
