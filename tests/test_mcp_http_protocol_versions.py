# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — MCP protocol version negotiation and JSON-RPC error codes
"""Version negotiation and error codes of the production HTTPS MCP router.

These tests pin what MCP SDK 1.30.0 behind the production router actually
answers on the wire, including two known deviations, so that an SDK upgrade
that changes any of them fails here instead of silently changing the
published contract:

- an unknown JSON-RPC method is answered ``-32602`` (JSON-RPC 2.0 names
  ``-32601``);
- a session negotiated at ``2025-11-25`` still accepts a request whose
  ``MCP-Protocol-Version`` header names another supported version.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hub_e2e_helpers import running_hub
from mcp_http_helpers import access_token, https_server, provision
from synapse_channel.core.hub import SynapseHub
from synapse_channel.mcp.http_application import build_http_mcp_app

LATEST = "2025-11-25"
SUPPORTED = ("2024-11-05", "2025-03-26", "2025-06-18", LATEST)


def _initialize(version: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": version,
            "capabilities": {},
            "clientInfo": {"name": "version-contract", "version": "1"},
        },
    }


async def test_versions_and_error_codes_on_the_production_router(tmp_path: Path) -> None:
    """Negotiation, header validation and JSON-RPC errors as SDK 1.30.0 answers them."""
    issuer = Ed25519PrivateKey.generate()
    grants = tmp_path / "grants.json"
    trust = provision(grants, issuer)
    async with running_hub(
        SynapseHub(identity_trust_bundle=trust, require_identity_binding=True)
    ) as (_, uri):
        app = build_http_mcp_app(
            auth_file=grants,
            project="ALPHA",
            hub_uri=uri,
            allowed_hosts=["127.0.0.1:*"],
            allowed_origins=[],
        )
        async with (
            https_server(app, tmp_path) as (url, tls),
            httpx.AsyncClient(
                verify=tls,
                timeout=5,
                trust_env=False,
                headers={
                    "Authorization": "Bearer " + access_token(issuer),
                    "Accept": "application/json, text/event-stream",
                },
            ) as client,
        ):
            endpoint = url + "/mcp"
            # Every supported version is negotiated as requested; an unknown one gets LATEST.
            for version in SUPPORTED:
                answer = await client.post(endpoint, json=_initialize(version))
                assert answer.status_code == 200
                assert answer.json()["result"]["protocolVersion"] == version
            unknown = await client.post(endpoint, json=_initialize("1999-01-01"))
            assert unknown.json()["result"]["protocolVersion"] == LATEST

            started = await client.post(endpoint, json=_initialize(LATEST))
            headers = {
                "Mcp-Session-Id": started.headers["mcp-session-id"],
                "MCP-Protocol-Version": LATEST,
            }
            ready = await client.post(
                endpoint,
                headers=headers,
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            )
            assert ready.status_code == 202

            async def call(body: dict[str, Any] | bytes, **extra: str) -> httpx.Response:
                merged = {**headers, **extra}
                if isinstance(body, bytes):
                    merged["Content-Type"] = "application/json"
                    return await client.post(endpoint, headers=merged, content=body)
                return await client.post(endpoint, headers=merged, json=body)

            listing = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
            bogus = await call(listing, **{"MCP-Protocol-Version": "bogus"})
            assert bogus.status_code == 400
            assert bogus.json()["error"]["code"] == -32600
            assert "Unsupported protocol version: bogus" in bogus.json()["error"]["message"]
            # Known deviation: another supported version in the header is accepted.
            other = await call(listing, **{"MCP-Protocol-Version": "2025-06-18"})
            assert other.status_code == 200
            assert other.json()["result"]["tools"]

            malformed = await call(b"{not json")
            assert malformed.status_code == 400
            assert malformed.json()["error"]["code"] == -32700

            # Known deviation: an unknown method is -32602, not JSON-RPC's -32601.
            method = await call({"jsonrpc": "2.0", "id": 3, "method": "no/such"})
            assert method.json()["error"]["code"] == -32602
            params = await call(
                {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": 5}}
            )
            assert params.json()["error"]["code"] == -32602

            # An unknown tool is a tool-level refusal with the generic text, not a protocol error.
            tool = await call(
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {"name": "no_tool", "arguments": {}},
                }
            )
            assert tool.json()["result"] == {
                "content": [
                    {"type": "text", "text": "remote MCP operation refused or unavailable"}
                ],
                "isError": True,
            }
