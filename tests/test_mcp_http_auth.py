# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — authenticated MCP over real HTTPS
"""Exercise issuer-bound token verification through the SDK's HTTP auth boundary."""

from __future__ import annotations

import json
import logging
import time
from base64 import urlsafe_b64encode
from pathlib import Path

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import LATEST_PROTOCOL_VERSION
from pydantic import AnyHttpUrl

from mcp_http_helpers import access_token, https_server, write_policy
from synapse_channel.mcp.http_auth import HttpTokenVerifier


def sdk_server(policy_file: Path) -> FastMCP:
    """Build the installed SDK transport with the production token verifier."""
    verifier = HttpTokenVerifier(policy_file)
    return FastMCP(
        "auth-acceptance",
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


async def initialize(client: httpx.AsyncClient, token: str | None) -> httpx.Response:
    """Initialize via a network HTTP request rather than a verifier helper call."""
    headers = {"Accept": "application/json, text/event-stream"}
    if token is not None:
        headers["Authorization"] = ("Bearer " + token).rstrip()
    return await client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "auth-acceptance", "version": "1"},
            },
        },
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"aud": "https://other.example.test/mcp"},
        {"iss": "https://other.example.test"},
        {"sub": "unprovisioned"},
        {"exp": int(time.time()) - 5},
        {"iat": int(time.time()) + 100},
        {"scope": "synapse:mutate"},
        {"scope": "synapse:read administrator"},
        {"exp": int(time.time()) + 7200},
        {"iat": True},
        {"client_id": ""},
    ],
)
async def test_invalid_identity_refused_over_https(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    """Wrong audience, issuer, expiry, scopes and grant identities yield uniform 401."""
    key = Ed25519PrivateKey.generate()
    policy_file = tmp_path / "grants.json"
    write_policy(policy_file, key)
    async with https_server(sdk_server(policy_file).streamable_http_app(), tmp_path) as (url, tls):
        async with httpx.AsyncClient(
            base_url=url, verify=tls, timeout=5, trust_env=False
        ) as client:
            token = access_token(key, **changes)
            response = await initialize(client, token)
            assert response.status_code == 401
            assert token not in response.text
            assert "resource_metadata=" in response.headers["www-authenticate"]


async def test_authentication_and_live_revocation(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An admitted identity is refused after revocation without restarting HTTP."""
    key = Ed25519PrivateKey.generate()
    caplog.set_level(logging.DEBUG)
    policy_file = tmp_path / "grants.json"
    write_policy(policy_file, key)
    async with https_server(sdk_server(policy_file).streamable_http_app(), tmp_path) as (url, tls):
        async with httpx.AsyncClient(
            base_url=url, verify=tls, timeout=5, trust_env=False
        ) as client:
            assert (await initialize(client, None)).status_code == 401
            token = access_token(key)
            accepted = await initialize(client, token)
            assert accepted.status_code == 200
            assert accepted.json()["result"]["protocolVersion"] == LATEST_PROTOCOL_VERSION
            write_policy(policy_file, key, revoked=True)
            assert (await initialize(client, token)).status_code == 401
            assert token not in caplog.text
            assert "BEGIN PRIVATE KEY" not in caplog.text


async def test_bad_signatures_headers_and_policy_changes(tmp_path: Path) -> None:
    """Real HTTP refuses signature confusion, missing authority and changed trust roots."""
    key = Ed25519PrivateKey.generate()
    policy_file = tmp_path / "grants.json"
    write_policy(policy_file, key)
    token = access_token(key)
    claims: dict[str, object] = jwt.decode(token, options={"verify_signature": False})
    malformed_header = urlsafe_b64encode(b'{"alg":"EdDSA","kid":123}').rstrip(b"=").decode()
    malformed_body = malformed_header + "." + token.split(".")[1]
    malformed_signature = urlsafe_b64encode(key.sign(malformed_body.encode())).rstrip(b"=").decode()
    invalid_tokens = [
        "",
        "not-a-jwt",
        "x" * 8193,
        access_token(Ed25519PrivateKey.generate()),
        jwt.encode(claims, "a" * 32, algorithm="HS256", headers={"kid": "issuer-1"}),
        jwt.encode(claims, key, algorithm="EdDSA"),
        malformed_body + "." + malformed_signature,
        jwt.encode(claims, key, algorithm="EdDSA", headers={"kid": "unknown"}),
        access_token(key, scope=123),
        access_token(key, scope="x" * 257),
        access_token(key, sub=""),
        access_token(key, jti="x" * 257),
        access_token(key, client_id=123),
        access_token(key, exp=float(time.time()) + 60),
    ]
    async with https_server(sdk_server(policy_file).streamable_http_app(), tmp_path) as (url, tls):
        async with httpx.AsyncClient(
            base_url=url, verify=tls, timeout=5, trust_env=False
        ) as client:
            for invalid in invalid_tokens:
                assert (await initialize(client, invalid)).status_code == 401
            original = policy_file.read_text()
            payload: dict[str, object] = json.loads(original)
            payload["issuer"] = "https://changed.example.test"
            policy_file.write_text(json.dumps(payload))
            assert (await initialize(client, token)).status_code == 401
            payload["issuer"] = "https://issuer.example.test"
            payload["resource"] = "https://changed.example.test/mcp"
            policy_file.write_text(json.dumps(payload))
            assert (await initialize(client, token)).status_code == 401
            policy_file.write_text(original.replace('"projects":', '"enabled": false, "projects":'))
            assert (await initialize(client, token)).status_code == 401
            policy_file.write_text(
                original.replace(
                    '"projects":', f'"revoked_before": {int(time.time())}, "projects":'
                )
            )
            assert (await initialize(client, token)).status_code == 401
            policy_file.write_text(original)
            policy_file.chmod(0o644)
            assert (await initialize(client, token)).status_code == 401
            policy_file.chmod(0o600)
            policy_file.write_text("{")
            assert (await initialize(client, token)).status_code == 401
