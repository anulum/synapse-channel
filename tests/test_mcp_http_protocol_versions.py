# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — MCP protocol version negotiation and JSON-RPC error codes
"""Version negotiation and error codes of the production HTTPS MCP router.

These tests pin what MCP SDK 2.3.0 behind the production router actually
answers on the wire, including one known deviation, so that an SDK upgrade
that changes any of them fails here instead of silently changing the
published contract:

- a session negotiated at ``2025-11-25`` still accepts a request whose
  ``MCP-Protocol-Version`` header names another supported version.

SDK 2 also serves the stateless ``2026-07-28`` revision to any request whose
header names a version outside the handshake revisions. The router refuses
those requests before the SDK sees them, as SDK 1.30 refused an unsupported
version, so the published contract stays the four handshake revisions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx2
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hub_e2e_helpers import running_hub
from mcp_http_helpers import access_token, https_server, provision
from synapse_channel.core.hub import SynapseHub
from synapse_channel.mcp.http_application import build_http_mcp_app

LATEST = "2025-11-25"
STATELESS = "2026-07-28"
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
    """Negotiation, header validation and JSON-RPC errors as SDK 2.3.0 answers them."""
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
            httpx2.AsyncClient(
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
            for requested in ("1999-01-01", STATELESS):
                unknown = await client.post(endpoint, json=_initialize(requested))
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

            async def call(body: dict[str, Any] | bytes, **extra: str) -> httpx2.Response:
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
            # The stateless revision is refused even with its complete envelope.
            envelope = {
                "io.modelcontextprotocol/protocolVersion": STATELESS,
                "io.modelcontextprotocol/clientCapabilities": {},
            }
            stateless_requests: tuple[tuple[str, dict[str, object], str | None], ...] = (
                ("tools/list", {"_meta": envelope}, None),
                (
                    "tools/call",
                    {"name": "synapse_board", "arguments": {}, "_meta": envelope},
                    "synapse_board",
                ),
            )
            for rpc_method, rpc_params, tool_name in stateless_requests:
                stateless_headers = {"MCP-Protocol-Version": STATELESS, "Mcp-Method": rpc_method}
                if tool_name is not None:
                    stateless_headers["Mcp-Name"] = tool_name
                stateless = await client.post(
                    endpoint,
                    headers=stateless_headers,
                    json={"jsonrpc": "2.0", "id": 9, "method": rpc_method, "params": rpc_params},
                )
                assert stateless.status_code == 400
                assert stateless.json()["error"] == {
                    "code": -32600,
                    "message": "Bad Request: Unsupported protocol version: 2026-07-28. "
                    "Supported versions: " + ", ".join(SUPPORTED),
                }
            # Known deviation: another supported version in the header is accepted.
            other = await call(listing, **{"MCP-Protocol-Version": "2025-06-18"})
            assert other.status_code == 200
            assert other.json()["result"]["tools"]

            malformed = await call(b"{not json")
            assert malformed.status_code == 400
            assert malformed.json()["error"]["code"] == -32700

            # SDK 1.30 answered an unknown method -32602; SDK 2 answers JSON-RPC's -32601.
            method = await call({"jsonrpc": "2.0", "id": 3, "method": "no/such"})
            assert method.json()["error"]["code"] == -32601
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
