# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — authenticated MCP dispatch resource limits
"""Exercise bounded replies and ambiguous mutation outcomes through real HTTPS."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError
from pydantic import AnyUrl

from hub_e2e_helpers import running_hub
from mcp_http_helpers import access_token, https_server, provision
from synapse_channel.core.acl import BOARD, AclPolicy, AclRule
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.mcp.http_application import build_http_mcp_app


async def test_reply_budget_retains_committed_mutation_identity(tmp_path: Path) -> None:
    """An oversized reply is private, and retry does not repeat the committed write."""
    issuer = Ed25519PrivateKey.generate()
    grants = tmp_path / "grants.json"
    trust = provision(grants, issuer)
    store = EventStore(tmp_path / "hub.db")
    hub = SynapseHub(journal=store, identity_trust_bundle=trust, require_identity_binding=True)
    marker = "PRIVATE_OVERSIZED_TASK_TITLE_" + "x" * 2048
    try:
        async with running_hub(hub) as (_, uri):
            app = build_http_mcp_app(
                auth_file=grants,
                project="ALPHA",
                hub_uri=uri,
                allowed_hosts=["127.0.0.1:*"],
                allowed_origins=[],
                reply_bytes=1024,
            )
            async with https_server(app, tmp_path) as (url, tls):
                async with httpx.AsyncClient(
                    verify=tls,
                    trust_env=False,
                    timeout=5,
                    headers={
                        "Authorization": "Bearer "
                        + access_token(issuer, scope="synapse:read synapse:mutate")
                    },
                ) as client:
                    async with streamable_http_client(url + "/mcp", http_client=client) as (
                        read,
                        write,
                        _,
                    ):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            for _ in range(2):
                                result = await session.call_tool(
                                    "synapse_task_declare",
                                    {"task_id": "ALPHA/oversized", "title": marker},
                                    meta={"synapse/operation-id": "oversized-declaration"},
                                )
                                assert result.isError
                                assert "PRIVATE_OVERSIZED" not in result.model_dump_json()
                                assert hub.blackboard.tasks["ALPHA/oversized"].version == 1
                                assert hub.blackboard.tasks["ALPHA/oversized"].title == marker
                            board = await session.call_tool("synapse_board")
                            assert board.isError
                            assert "PRIVATE_OVERSIZED" not in board.model_dump_json()
                            with pytest.raises(McpError, match="resource refused") as error:
                                await session.read_resource(AnyUrl("synapse://board"))
                            assert "PRIVATE_OVERSIZED" not in str(error.value)
    finally:
        store.close()


async def test_native_refusal_obeys_dispatch_deadline_and_recovers(tmp_path: Path) -> None:
    """A native-denied write is bounded, and its exact retry can recover after admission."""
    issuer = Ed25519PrivateKey.generate()
    grants = tmp_path / "grants.json"
    trust = provision(grants, issuer)
    hub = SynapseHub(
        identity_trust_bundle=trust,
        require_identity_binding=True,
        acl_policy=AclPolicy(),
        require_acl=True,
    )
    async with running_hub(hub) as (_, uri):
        app = build_http_mcp_app(
            auth_file=grants,
            project="ALPHA",
            hub_uri=uri,
            allowed_hosts=["127.0.0.1:*"],
            allowed_origins=[],
            operation_timeout=0.1,
        )
        async with https_server(app, tmp_path) as (url, tls):
            async with httpx.AsyncClient(
                verify=tls,
                trust_env=False,
                timeout=5,
                headers={
                    "Authorization": "Bearer "
                    + access_token(issuer, scope="synapse:read synapse:mutate")
                },
            ) as client:
                async with streamable_http_client(url + "/mcp", http_client=client) as (
                    read,
                    write,
                    _,
                ):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        arguments = {"task_id": "ALPHA/deadline", "title": "Deadline retry"}
                        meta = {"synapse/operation-id": "deadline-declare"}
                        refused = await asyncio.wait_for(
                            session.call_tool("synapse_task_declare", arguments, meta=meta), 2
                        )
                        assert refused.isError
                        assert "operation refused or unavailable" in refused.model_dump_json()
                        assert not hub.blackboard.tasks
                        assert not hub.state.claims
                        assert hub.acl_policy is not None
                        hub.acl_policy.rules.append(AclRule(BOARD, "board", "*", namespace="ALPHA"))
                        recovered = await asyncio.wait_for(
                            session.call_tool("synapse_task_declare", arguments, meta=meta), 2
                        )
                        assert not recovered.isError
                        assert hub.blackboard.tasks["ALPHA/deadline"].version == 1
