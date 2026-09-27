# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — native HTTP operation correlation
"""Verify caller operation identities through real HTTPS and the native journal."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from hub_e2e_helpers import running_hub
from mcp_http_helpers import access_token, https_server, provision
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.mcp.http_application import build_http_mcp_app


async def test_operation_keys_remain_bound_to_native_principals(tmp_path: Path) -> None:
    """Same keys deduplicate each seat's writes without conflating distinct subjects."""
    issuer = Ed25519PrivateKey.generate()
    grants = tmp_path / "grants.json"
    trust = provision(grants, issuer)
    store = EventStore(tmp_path / "hub.db")
    hub = SynapseHub(journal=store, identity_trust_bundle=trust, require_identity_binding=True)
    bearers: list[str] = []
    try:
        async with running_hub(hub) as (_, uri):
            app = build_http_mcp_app(
                auth_file=grants,
                project="ALPHA",
                hub_uri=uri,
                allowed_hosts=["127.0.0.1:*"],
                allowed_origins=[],
            )
            async with https_server(app, tmp_path) as (url, tls):
                for subject in ("alice", "bob"):
                    bearer = access_token(
                        issuer,
                        sub=subject,
                        jti=subject + "-token",
                        scope="synapse:read synapse:mutate",
                    )
                    bearers.append(bearer)
                    async with httpx.AsyncClient(
                        verify=tls,
                        trust_env=False,
                        timeout=5,
                        headers={"Authorization": "Bearer " + bearer},
                    ) as client:
                        async with streamable_http_client(url + "/mcp", http_client=client) as (
                            read,
                            write,
                            _,
                        ):
                            async with ClientSession(read, write) as session:
                                await session.initialize()
                                for _ in range(2):
                                    declared = await session.call_tool(
                                        "synapse_task_declare",
                                        {"task_id": "ALPHA/" + subject, "title": subject},
                                        meta={"synapse/operation-id": "same-declaration"},
                                    )
                                    assert not declared.isError
                                    sent = await session.call_tool(
                                        "synapse_send",
                                        {"target": "ALPHA/observer", "message": subject},
                                        meta={"synapse/operation-id": "same-message"},
                                    )
                                    assert not sent.isError
                                task = hub.blackboard.tasks["ALPHA/" + subject]
                                assert task.created_by == "ALPHA/" + subject
                                assert task.project == "ALPHA"
                                assert task.version == 1

                async def recorded_senders() -> None:
                    """Wait for genuine chat durability after transport submission."""
                    while {
                        event.payload.get("sender") for event in store.iter_events(kinds=["chat"])
                    } != {"ALPHA/alice", "ALPHA/bob"}:
                        await asyncio.sleep(0.01)

                await asyncio.wait_for(recorded_senders(), 3)
                chats = list(store.iter_events(kinds=["chat"]))
                assert chats
                for event in chats:
                    assert event.payload["client_msg_id"] == "same-message"
                    assert event.payload["target"] == "ALPHA/observer"
                    assert event.payload["payload"] in {"alice", "bob"}
                    assert not any(bearer in json.dumps(event.payload) for bearer in bearers)
    finally:
        store.close()
