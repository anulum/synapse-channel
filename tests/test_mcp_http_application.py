# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real remote MCP application acceptance
"""Exercise the production HTTPS entry point and provisioned native hub identities."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError
from pydantic import AnyUrl

from hub_e2e_helpers import running_hub
from mcp_http_helpers import access_token, https_server, provision
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.mcp.http_application import build_http_mcp_app
from synapse_channel.mcp.http_config import MUTATION_TOOLS, READ_TOOLS


async def test_native_identities_mutations_resources_and_reconnect(tmp_path: Path) -> None:
    """Actual HTTPS tools preserve hub scope, stable replay and subject session ownership."""
    issuer = Ed25519PrivateKey.generate()
    path = tmp_path / "grants.json"
    trust = provision(path, issuer)
    store = EventStore(tmp_path / "hub.db")
    hub = SynapseHub(journal=store, identity_trust_bundle=trust, require_identity_binding=True)
    token = access_token(issuer, scope="synapse:read synapse:mutate")
    try:
        async with running_hub(hub) as (_, uri):
            app = build_http_mcp_app(
                auth_file=path,
                project="ALPHA",
                hub_uri=uri,
                allowed_hosts=["127.0.0.1:*"],
                allowed_origins=[],
                operation_timeout=7,
            )
            async with https_server(app, tmp_path) as (url, tls):
                assert {"ALPHA/alice", "ALPHA/bob"} <= set(hub.online_agents())
                async with httpx.AsyncClient(
                    verify=tls,
                    timeout=10,
                    trust_env=False,
                    headers={"Authorization": "bEaReR " + token},
                ) as client:
                    for incarnation in range(2):
                        async with streamable_http_client(url + "/mcp", http_client=client) as (
                            read,
                            write,
                            session_id,
                        ):
                            async with ClientSession(read, write) as session:
                                initialized = await session.initialize()
                                assert initialized.protocolVersion == "2025-11-25"
                                assert {
                                    tool.name for tool in (await session.list_tools()).tools
                                } == (READ_TOOLS | MUTATION_TOOLS)
                                declared = await session.call_tool(
                                    "synapse_task_declare",
                                    {"task_id": "ALPHA/task", "title": "Remote task"},
                                    meta={"synapse/operation-id": "declare-task"},
                                )
                                assert not declared.isError
                                assert hub.blackboard.tasks["ALPHA/task"].project == "ALPHA"
                                assert hub.blackboard.tasks["ALPHA/task"].version == 1
                                if incarnation:
                                    continue
                                assert (await session.list_resources()).resources
                                assert (
                                    len((await session.list_resource_templates()).resourceTemplates)
                                    == 3
                                )
                                for resource in (
                                    "synapse://board",
                                    "synapse://state",
                                    "synapse://manifest",
                                    "synapse://directory",
                                    "synapse://task/ALPHA%2Ftask",
                                    "synapse://agent/ALPHA%2Falice",
                                    "synapse://resource-kind/cpu",
                                ):
                                    await session.read_resource(AnyUrl(resource))
                                for resource in (
                                    "synapse://agent/BETA%2Fforeign",
                                    "file:///etc/passwd",
                                    "synapse://task/",
                                    "synapse://task/" + "x" * 257,
                                    "synapse://unknown/ALPHA%2Ftask",
                                ):
                                    with pytest.raises(McpError, match="resource refused"):
                                        await session.read_resource(AnyUrl(resource))
                                for name, args in (
                                    ("synapse_claim", {"task_id": "BETA/task"}),
                                    ("synapse_claim", {"task_id": "ALPHA/task", "paths": ["src"]}),
                                    ("synapse_send", {"target": "all", "message": "refused"}),
                                    (
                                        "synapse_send",
                                        {"target": "ALPHA/bob,BETA/foreign", "message": "refused"},
                                    ),
                                    (
                                        "synapse_send",
                                        {"target": "ALPHA/bob ALPHA/alice", "message": "refused"},
                                    ),
                                    (
                                        "synapse_release",
                                        {"task_id": "ALPHA/task", "changed_files": ["src"]},
                                    ),
                                    ("synapse_claim", {"task_id": "ALPHA/missing"}),
                                    (
                                        "synapse_task_declare",
                                        {
                                            "task_id": "ALPHA/malformed",
                                            "title": "refused",
                                            "depends_on": "ALPHA/task",
                                        },
                                    ),
                                    (
                                        "synapse_task_declare",
                                        {
                                            "task_id": "ALPHA/second",
                                            "title": "refused",
                                            "depends_on": ["BETA/task"],
                                        },
                                    ),
                                    (
                                        "synapse_handoff",
                                        {
                                            "task_id": "ALPHA/task",
                                            "to_agent": "BETA/foreign",
                                        },
                                    ),
                                    (
                                        "synapse_task_update",
                                        {
                                            "task_id": "ALPHA/task",
                                            "suggested_owner": "BETA/foreign",
                                        },
                                    ),
                                ):
                                    result = await session.call_tool(
                                        name,
                                        args,
                                        meta={"synapse/operation-id": "refused"},
                                    )
                                    assert result.isError
                                    assert "BETA/foreign" not in result.model_dump_json()
                                assert not hub.state.claims
                                conflict = await session.call_tool(
                                    "synapse_task_declare",
                                    {"task_id": "ALPHA/task", "title": "Conflicting retry"},
                                    meta={"synapse/operation-id": "declare-task"},
                                )
                                assert conflict.isError
                                assert hub.blackboard.tasks["ALPHA/task"].title == "Remote task"
                                assert (
                                    await session.call_tool(
                                        "synapse_claim",
                                        {"task_id": "ALPHA/task"},
                                    )
                                ).isError
                                # A remote pathless claim would cover the server's worktree.
                                assert (
                                    await session.call_tool(
                                        "synapse_claim",
                                        {"task_id": "ALPHA/task"},
                                        meta={"synapse/operation-id": "claim-task-pathless"},
                                    )
                                ).isError
                                claimed = await session.call_tool(
                                    "synapse_claim",
                                    {"task_id": "ALPHA/task", "task_only": True},
                                    meta={"synapse/operation-id": "claim-task"},
                                )
                                assert not claimed.isError
                                assert hub.state.claims["ALPHA/task"].owner == "ALPHA/alice"
                                identity = session_id()
                                assert identity
                                response = await client.post(
                                    url + "/mcp",
                                    headers={
                                        "Authorization": "Bearer "
                                        + access_token(issuer, sub="bob"),
                                        "Mcp-Session-Id": identity,
                                        "MCP-Protocol-Version": "2025-11-25",
                                        "Accept": "application/json, text/event-stream",
                                    },
                                    json={"jsonrpc": "2.0", "id": 7, "method": "tools/list"},
                                )
                                assert response.status_code == 404
                                released = await session.call_tool(
                                    "synapse_release",
                                    {"task_id": "ALPHA/task"},
                                    meta={"synapse/operation-id": "release-task"},
                                )
                                assert not released.isError
                                assert not hub.state.claims
                                replay = await session.call_tool(
                                    "synapse_release",
                                    {"task_id": "ALPHA/task"},
                                    meta={"synapse/operation-id": "release-task"},
                                )
                                assert replay.structuredContent == released.structuredContent
                                for name, arguments, operation in (
                                    (
                                        "synapse_task_declare",
                                        {
                                            "task_id": "ALPHA/next",
                                            "title": "Dependent task",
                                            "depends_on": ["ALPHA/task"],
                                        },
                                        "declare-next",
                                    ),
                                    (
                                        "synapse_task_update",
                                        {"task_id": "ALPHA/next", "suggested_owner": "ALPHA/bob"},
                                        "update-next",
                                    ),
                                    (
                                        "synapse_send",
                                        {"target": "ALPHA/bob", "message": "Native directed chat"},
                                        "send-next",
                                    ),
                                    (
                                        "synapse_claim",
                                        {"task_id": "ALPHA/next", "task_only": True},
                                        "claim-next",
                                    ),
                                    (
                                        "synapse_handoff",
                                        {"task_id": "ALPHA/next", "to_agent": "ALPHA/bob"},
                                        "handoff-next",
                                    ),
                                ):
                                    assert not (
                                        await session.call_tool(
                                            name,
                                            arguments,
                                            meta={"synapse/operation-id": operation},
                                        )
                                    ).isError
                                assert hub.state.claims["ALPHA/next"].owner == "ALPHA/bob"
                                assert hub.blackboard.tasks["ALPHA/next"].suggested_owner == (
                                    "ALPHA/bob"
                                )
                                denied = await session.call_tool(
                                    "synapse_release",
                                    {"task_id": "ALPHA/next"},
                                    meta={"synapse/operation-id": "not-owner"},
                                )
                                assert denied.isError
                assert "BETA/foreign" not in hub.online_agents()
    finally:
        store.close()


async def test_http_admission_origin_metadata_and_revocation(tmp_path: Path) -> None:
    """Exercise actual noauth, hostile authority, foreign grant and live revocation."""
    issuer = Ed25519PrivateKey.generate()
    path = tmp_path / "grants.json"
    trust = provision(path, issuer)
    hub = SynapseHub(identity_trust_bundle=trust, require_identity_binding=True)
    async with running_hub(hub) as (_, uri):
        app = build_http_mcp_app(
            auth_file=path,
            project="ALPHA",
            hub_uri=uri,
            allowed_hosts=["127.0.0.1:*"],
            allowed_origins=["https://trusted.example.test"],
        )
        async with https_server(app, tmp_path) as (url, tls):
            async with httpx.AsyncClient(verify=tls, timeout=5, trust_env=False) as client:
                metadata = await client.get(url + "/.well-known/oauth-protected-resource/mcp")
                assert metadata.status_code == 200
                unauthenticated = await client.get(url + "/mcp")
                assert unauthenticated.status_code == 401
                assert (
                    'resource_metadata="https://mcp.example.test/.well-known/oauth-protected-resource/mcp"'
                    in unauthenticated.headers["www-authenticate"]
                )
                for headers in (
                    {"Host": "evil.example.test"},
                    {"Origin": "https://evil.example.test"},
                ):
                    assert (await client.get(url + "/mcp", headers=headers)).status_code in (
                        400,
                        403,
                        421,
                    )
                assert (
                    await client.get(
                        url + "/mcp",
                        headers={"Authorization": "Bearer " + access_token(issuer, sub="foreign")},
                    )
                ).status_code == 403
                policy = json.loads(path.read_text())
                policy["revoked_token_ids"] = ["token-1"]
                path.write_text(json.dumps(policy))
                assert (
                    await client.get(
                        url + "/mcp",
                        headers={"Authorization": "Bearer " + access_token(issuer)},
                    )
                ).status_code == 401


@pytest.mark.parametrize(
    "limits",
    [
        {"allowed_hosts": []},
        {"max_requests": 0},
        {"max_sessions": 0},
        {"request_bytes": 1023},
        {"reply_bytes": 1023},
        {"operation_timeout": 0},
        {"ready_timeout": 0},
    ],
)
def test_invalid_http_limits_are_refused(tmp_path: Path, limits: dict[str, Any]) -> None:
    """Reject invalid operator profiles before any connection or HTTP startup."""
    issuer = Ed25519PrivateKey.generate()
    path = tmp_path / "grants.json"
    provision(path, issuer)
    settings: dict[str, Any] = {
        "auth_file": path,
        "project": "ALPHA",
        "hub_uri": "ws://127.0.0.1:1",
        "allowed_hosts": ["127.0.0.1:*"],
        "allowed_origins": [],
    }
    settings.update(limits)
    with pytest.raises(ValueError, match="limits"):
        build_http_mcp_app(**settings)


async def test_mutation_retry_survives_a_real_hub_and_http_restart(tmp_path: Path) -> None:
    """Repeat a committed operation after both real transports and the journal reopen."""
    issuer = Ed25519PrivateKey.generate()
    path = tmp_path / "grants.json"
    trust = provision(path, issuer)
    for incarnation in range(2):
        store = EventStore(tmp_path / "restart.db")
        hub = SynapseHub(journal=store, identity_trust_bundle=trust, require_identity_binding=True)
        try:
            async with running_hub(hub) as (_, uri):
                app = build_http_mcp_app(
                    auth_file=path,
                    project="ALPHA",
                    hub_uri=uri,
                    allowed_hosts=["127.0.0.1:*"],
                    allowed_origins=[],
                )
                async with https_server(app, tmp_path) as (url, tls):
                    async with httpx.AsyncClient(
                        verify=tls,
                        timeout=10,
                        trust_env=False,
                        headers={
                            "Authorization": "Bearer "
                            + access_token(
                                issuer,
                                scope="synapse:read synapse:mutate",
                            )
                        },
                    ) as client:
                        async with streamable_http_client(url + "/mcp", http_client=client) as (
                            read,
                            write,
                            _,
                        ):
                            async with ClientSession(read, write) as session:
                                await session.initialize()
                                declared = await session.call_tool(
                                    "synapse_task_declare",
                                    {"task_id": "ALPHA/persisted", "title": "Durable retry"},
                                    meta={"synapse/operation-id": "restart-declare"},
                                )
                                assert not declared.isError
                                board = await session.call_tool("synapse_board")
                                assert not board.isError
                                assert hub.blackboard.tasks["ALPHA/persisted"].version == 1
                                if incarnation:
                                    assert (
                                        hub.blackboard.tasks["ALPHA/persisted"].title
                                        == "Durable retry"
                                    )
        finally:
            store.close()
