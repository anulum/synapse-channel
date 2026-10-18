# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — authenticated HTTPS MCP backend recovery
"""Keep provisioned principals isolated across backend hub restarts."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from mcp_http_helpers import access_token, https_server, provision
from mcp_reconnect_helpers import stop_hub, wait_for_identity
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.mcp.http_application import build_http_mcp_app


async def test_https_principals_recover_without_replaying_offline_mutations(tmp_path: Path) -> None:
    """Existing SDK sessions and principal ownership survive two private outages."""
    issuer = Ed25519PrivateKey.generate()
    grants = tmp_path / "grants.json"
    trust = provision(grants, issuer)
    store = EventStore(tmp_path / "hub.db")
    hub = SynapseHub(journal=store, identity_trust_bundle=trust, require_identity_binding=True)
    task = asyncio.create_task(hub.serve("127.0.0.1", 0))
    try:
        host, port = await hub.wait_until_serving()
        app = build_http_mcp_app(
            auth_file=grants,
            project="ALPHA",
            hub_uri=f"ws://{host}:{port}",
            allowed_hosts=["127.0.0.1:*"],
            allowed_origins=[],
            operation_timeout=0.5,
        )
        async with https_server(app, tmp_path) as (url, tls):
            async with httpx.AsyncClient(
                verify=tls,
                timeout=10,
                trust_env=False,
                headers={
                    "Authorization": "Bearer "
                    + access_token(issuer, scope="synapse:read synapse:mutate")
                },
            ) as client:
                async with streamable_http_client(url + "/mcp", http_client=client) as (
                    read,
                    write,
                    session_id,
                ):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        await wait_for_identity(session, hub, "ALPHA/alice", also=("ALPHA/bob",))
                        assert {"ALPHA/alice", "ALPHA/bob"} <= set(hub.online_agents())
                        original_session = session_id()
                        assert original_session
                        await asyncio.sleep(1.1)
                        for incarnation in range(2):
                            await stop_hub(task)
                            assert (await session.list_tools()).tools
                            rejected = await session.call_tool(
                                "synapse_task_declare",
                                {
                                    "task_id": f"ALPHA/offline-{incarnation}",
                                    "title": "must not replay",
                                },
                                meta={"synapse/operation-id": f"offline-{incarnation}"},
                            )
                            assert rejected.isError
                            await asyncio.sleep(0.7)
                            hub = SynapseHub(
                                journal=store,
                                identity_trust_bundle=trust,
                                require_identity_binding=True,
                            )
                            task = asyncio.create_task(hub.serve(host, port))
                            await hub.wait_until_serving()
                            await wait_for_identity(
                                session, hub, "ALPHA/alice", also=("ALPHA/bob",)
                            )
                            assert {"ALPHA/alice", "ALPHA/bob"} <= set(hub.online_agents())
                            assert session_id() == original_session
                            assert f"ALPHA/offline-{incarnation}" not in hub.blackboard.tasks
                            task_id = f"ALPHA/online-{incarnation}"
                            declared = await session.call_tool(
                                "synapse_task_declare",
                                {"task_id": task_id, "title": "after reconnect"},
                                meta={"synapse/operation-id": f"online-{incarnation}"},
                            )
                            assert not declared.isError
                            assert hub.blackboard.tasks[task_id].version == 1
                            assert hub.blackboard.tasks[task_id].created_by == "ALPHA/alice"
                            cross_principal = await client.post(
                                url + "/mcp",
                                headers={
                                    "Authorization": "Bearer " + access_token(issuer, sub="bob"),
                                    "Mcp-Session-Id": original_session,
                                    "MCP-Protocol-Version": "2025-11-25",
                                    "Accept": "application/json, text/event-stream",
                                },
                                json={"jsonrpc": "2.0", "id": 7, "method": "tools/list"},
                            )
                            assert cross_principal.status_code == 404
    finally:
        await stop_hub(task)
        store.close()
