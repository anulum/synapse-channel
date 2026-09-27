# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — native HTTP transport boundary tests
"""Exercise transport refusal and capacity through actual production sockets."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from hub_e2e_helpers import running_hub
from mcp_http_helpers import access_token, https_server, provision
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.message_auth import EventSignatureKey, EventSignatureTrustBundle
from synapse_channel.mcp.http_application import build_http_mcp_app


def initialize_request(identifier: int = 1) -> dict[str, object]:
    """Build the published MCP initialization message, without an SDK transport shim."""
    return {
        "jsonrpc": "2.0",
        "id": identifier,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "transport-boundary", "version": "1"},
        },
    }


async def test_plaintext_and_websocket_are_refused(tmp_path: Path) -> None:
    """The production router rejects actual unencrypted HTTP and WSS upgrades."""
    issuer = Ed25519PrivateKey.generate()
    path = tmp_path / "grants.json"
    trust = provision(path, issuer)
    async with running_hub(
        SynapseHub(
            identity_trust_bundle=trust,
            require_identity_binding=True,
        )
    ) as (_, uri):
        for plaintext in (True, False):
            app = build_http_mcp_app(
                auth_file=path,
                project="ALPHA",
                hub_uri=uri,
                allowed_hosts=["127.0.0.1:*"],
                allowed_origins=[],
            )
            async with https_server(app, tmp_path, plaintext=plaintext) as (url, tls):
                if plaintext:
                    async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                        response = await client.get(url + "/mcp")
                        assert response.status_code == 400
                        assert response.json() == {"error": "HTTPS required"}
                else:
                    with pytest.raises(InvalidStatus) as rejected:
                        async with connect(
                            url.replace("https://", "wss://") + "/mcp",
                            ssl=tls,
                        ):
                            pytest.fail("WebSocket transport was admitted")
                    assert rejected.value.response.status_code == 403


async def test_sse_connection_counts_toward_global_capacity(tmp_path: Path) -> None:
    """One real persistent GET exhausts the configured global request budget."""
    if os.environ.get("SYNAPSE_CAPACITY_NATIVE_CHILD") != "1":
        environment = os.environ.copy()
        environment["SYNAPSE_CAPACITY_NATIVE_CHILD"] = "1"
        command = [sys.executable, "-m"]
        if "COVERAGE_FILE" in environment:
            command.extend(
                [
                    "coverage",
                    "run",
                    "--parallel-mode",
                    "--branch",
                    "--source=synapse_channel.mcp.http_application",
                    "-m",
                ]
            )
        command.extend(
            [
                "pytest",
                "-q",
                "--no-cov",
                str(Path(__file__).resolve())
                + "::"
                + "test_sse_connection_counts_toward_global_capacity",
            ]
        )
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 20)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        assert process.returncode == 0, (stdout.decode(), stderr.decode())
        return
    issuer = Ed25519PrivateKey.generate()
    path = tmp_path / "grants.json"
    trust = provision(path, issuer)
    async with running_hub(
        SynapseHub(
            identity_trust_bundle=trust,
            require_identity_binding=True,
        )
    ) as (_, uri):
        app = build_http_mcp_app(
            auth_file=path,
            project="ALPHA",
            hub_uri=uri,
            allowed_hosts=["127.0.0.1:*"],
            allowed_origins=[],
            max_requests=1,
        )
        async with https_server(app, tmp_path) as (url, tls):
            async with httpx.AsyncClient(
                verify=tls,
                timeout=5,
                trust_env=False,
                headers={
                    "Authorization": "Bearer " + access_token(issuer),
                    "Accept": "application/json, text/event-stream",
                },
            ) as client:
                initialized = await client.post(url + "/mcp", json=initialize_request())
                initialized.raise_for_status()
                session = initialized.headers["mcp-session-id"]
                headers = {
                    "Mcp-Session-Id": session,
                    "MCP-Protocol-Version": "2025-11-25",
                }
                response = await client.post(
                    url + "/mcp",
                    headers=headers,
                    json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                )
                assert response.status_code == 202
                async with client.stream("GET", url + "/mcp", headers=headers) as stream:
                    assert stream.status_code == 200
                    refused = await client.post(
                        url + "/mcp",
                        headers=headers,
                        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                    )
                    assert refused.status_code == 503
                    assert refused.json() == {"error": "request capacity reached"}


async def test_body_and_session_budgets_are_enforced(tmp_path: Path) -> None:
    """Actual wire requests cannot exceed per-principal body and session limits."""
    issuer = Ed25519PrivateKey.generate()
    path = tmp_path / "grants.json"
    trust = provision(path, issuer)
    async with running_hub(
        SynapseHub(
            identity_trust_bundle=trust,
            require_identity_binding=True,
        )
    ) as (_, uri):
        app = build_http_mcp_app(
            auth_file=path,
            project="ALPHA",
            hub_uri=uri,
            allowed_hosts=["127.0.0.1:*"],
            allowed_origins=[],
            request_bytes=1024,
            max_sessions=1,
        )
        async with https_server(app, tmp_path) as (url, tls):
            async with httpx.AsyncClient(
                verify=tls,
                timeout=5,
                trust_env=False,
                headers={
                    "Authorization": "Bearer " + access_token(issuer),
                    "Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json",
                },
            ) as client:
                first = await client.post(url + "/mcp", json=initialize_request())
                assert first.status_code == 200
                second = await client.post(url + "/mcp", json=initialize_request(2))
                assert second.status_code == 503
                oversized = await client.post(
                    url + "/mcp",
                    headers={"Mcp-Session-Id": first.headers["mcp-session-id"]},
                    content=" " * 2048,
                )
                assert oversized.status_code == 413


def test_project_without_provisioned_subjects_is_refused(tmp_path: Path) -> None:
    """An unknown project cannot create unbounded or client-selected bridge identities."""
    issuer = Ed25519PrivateKey.generate()
    path = tmp_path / "grants.json"
    provision(path, issuer)
    with pytest.raises(ValueError, match="no provisioned subjects"):
        build_http_mcp_app(
            auth_file=path,
            project="UNKNOWN",
            hub_uri="ws://127.0.0.1:1",
            allowed_hosts=["127.0.0.1:*"],
            allowed_origins=[],
        )


async def test_hub_admission_failure_cleans_up_provisioned_connections(tmp_path: Path) -> None:
    """A genuinely mismatched native trust bundle prevents HTTP application startup."""
    issuer = Ed25519PrivateKey.generate()
    path = tmp_path / "grants.json"
    trust = provision(path, issuer)
    # The hub trusts these actual keys, but only for a different native seat.
    grant = trust.keys["alice-1"]
    keys = dict(trust.keys)
    keys["alice-1"] = EventSignatureKey(
        key_id=grant.key_id,
        public_key=grant.public_key,
        senders=frozenset({"OTHER/alice"}),
    )
    trust = EventSignatureTrustBundle(keys=keys, replay_cache=trust.replay_cache)
    hub = SynapseHub(identity_trust_bundle=trust, require_identity_binding=True)
    async with running_hub(hub) as (_, uri):
        app = build_http_mcp_app(
            auth_file=path,
            project="ALPHA",
            hub_uri=uri,
            allowed_hosts=["127.0.0.1:*"],
            allowed_origins=[],
            ready_timeout=0.1,
        )
        with pytest.raises(RuntimeError, match="identity was not admitted"):
            async with app.router.lifespan_context(app):
                pytest.fail("unauthorized hub identity became ready")
        assert "ALPHA/alice" not in hub.online_agents()
        assert "ALPHA/bob" not in hub.online_agents()


async def test_unreachable_hub_prevents_application_startup(tmp_path: Path) -> None:
    """A genuinely unavailable TCP endpoint fails the finite native ready handshake."""
    issuer = Ed25519PrivateKey.generate()
    path = tmp_path / "grants.json"
    provision(path, issuer)
    app = build_http_mcp_app(
        auth_file=path,
        project="ALPHA",
        hub_uri="ws://127.0.0.1:1",
        allowed_hosts=["127.0.0.1:*"],
        allowed_origins=[],
        ready_timeout=0.05,
    )
    with pytest.raises(RuntimeError, match="identity was not admitted"):
        async with app.router.lifespan_context(app):
            pytest.fail("unreachable native endpoint admitted HTTPS service")
