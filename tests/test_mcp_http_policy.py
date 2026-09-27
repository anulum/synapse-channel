# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — remote MCP operation authority through HTTPS
"""Exercise mutation grants over actual MCP HTTP sessions and a real hub."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl

from hub_e2e_helpers import running_hub
from mcp_http_helpers import access_token, https_server, write_policy
from mcp_server_helpers import start_bridge
from synapse_channel.mcp.bridge import SynapseHubBridge
from synapse_channel.mcp.http_auth import HttpTokenVerifier
from synapse_channel.mcp.http_config import load_http_auth_config
from synapse_channel.mcp.http_policy import HttpOperationPolicy
from synapse_channel.mcp.http_views import ProjectMcpViews


def build_policy_server(path: Path, bridge: SynapseHubBridge) -> FastMCP:
    """Register real bridge operations behind the production per-operation guard."""
    verifier = HttpTokenVerifier(path)
    grant = load_http_auth_config(path).subjects["alice"].projects["ALPHA"]
    policy = HttpOperationPolicy(verifier, "alice", "ALPHA", grant)
    views = ProjectMcpViews(bridge, "ALPHA")
    server = FastMCP(
        "policy-acceptance",
        token_verifier=verifier,
        json_response=True,
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(verifier.issuer),
            resource_server_url=AnyHttpUrl(verifier.resource),
            validate_token_resource=True,
            required_scopes=["synapse:read"],
        ),
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=["127.0.0.1:*"], allowed_origins=[]
        ),
        max_sessions=4,
        max_request_body_size=8192,
    )

    @server.tool(name="synapse_board")
    async def scoped_board() -> str:
        """Read the real project board after fresh operation admission."""
        await policy.authorize("synapse_board")
        return await views.board()

    @server.tool(name="synapse_claim")
    async def scoped_claim(task_id: str) -> str:
        """Claim through the existing business action only after mutation admission."""
        await policy.authorize("synapse_claim")
        return await bridge.claim(task_id)

    return server


@pytest.mark.parametrize(
    "operator_mutation,token_mutation", [(False, False), (False, True), (True, False), (True, True)]
)
async def test_mutations_need_operator_and_token_authority(
    tmp_path: Path,
    operator_mutation: bool,
    token_mutation: bool,
) -> None:
    """Only both grants together may change real hub state; live removal takes effect."""
    key = Ed25519PrivateKey.generate()
    path = tmp_path / "grants.json"
    write_policy(path, key)
    payload = load_http_auth_config(path).model_dump(mode="json")
    if operator_mutation:
        payload["subjects"]["alice"]["projects"]["ALPHA"]["tools"].append("synapse_claim")
    original = json.dumps(payload)
    path.write_text(original)
    scope = "synapse:read synapse:mutate" if token_mutation else "synapse:read"
    token = access_token(key, scope=scope)
    async with running_hub() as (hub, uri):
        handle = await start_bridge(uri, name="ALPHA/alice")
        try:
            server = build_policy_server(path, handle.bridge)
            async with https_server(server.streamable_http_app(), tmp_path) as (url, tls):
                async with httpx.AsyncClient(
                    verify=tls,
                    timeout=5,
                    trust_env=False,
                    headers={"Authorization": "Bearer " + token},
                ) as client:
                    async with streamable_http_client(url + "/mcp", http_client=client) as (
                        read,
                        write,
                        _,
                    ):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            assert not (await session.call_tool("synapse_board")).isError
                            result = await session.call_tool(
                                "synapse_claim", {"task_id": "ALPHA/task"}
                            )
                            if operator_mutation and token_mutation:
                                assert not result.isError
                                assert hub.state.claims["ALPHA/task"].owner == "ALPHA/alice"
                            else:
                                assert result.isError
                                assert not hub.state.claims
                            payload["subjects"]["alice"]["projects"]["ALPHA"]["tools"] = []
                            path.write_text(json.dumps(payload))
                            assert (await session.call_tool("synapse_board")).isError
                            assert (
                                await session.call_tool(
                                    "synapse_claim", {"task_id": "ALPHA/second"}
                                )
                            ).isError
                            assert "ALPHA/second" not in hub.state.claims
        finally:
            await handle.close()


async def test_foreign_authenticated_subject_cannot_use_a_bound_seat(tmp_path: Path) -> None:
    """A valid issuer token for another subject cannot call this server's bridge."""
    key = Ed25519PrivateKey.generate()
    path = tmp_path / "grants.json"
    write_policy(path, key)
    payload = load_http_auth_config(path).model_dump(mode="json")
    payload["subjects"]["bob"] = {
        "projects": {
            "BETA": {
                "seat": "BETA/bob",
                "task_prefix": "BETA/",
                "identity_key_file": str(tmp_path / "bob.pem"),
                "identity_key_id": "bob-1",
            }
        }
    }
    path.write_text(json.dumps(payload))
    async with running_hub() as (hub, uri):
        handle = await start_bridge(uri, name="ALPHA/alice")
        try:
            server = build_policy_server(path, handle.bridge)
            async with https_server(server.streamable_http_app(), tmp_path) as (url, tls):
                async with httpx.AsyncClient(
                    verify=tls,
                    timeout=5,
                    trust_env=False,
                    headers={"Authorization": "Bearer " + access_token(key, sub="bob")},
                ) as client:
                    async with streamable_http_client(url + "/mcp", http_client=client) as (
                        read,
                        write,
                        _,
                    ):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            assert (await session.call_tool("synapse_board")).isError
                            assert (
                                await session.call_tool("synapse_claim", {"task_id": "ALPHA/task"})
                            ).isError
                            assert not hub.state.claims
        finally:
            await handle.close()
