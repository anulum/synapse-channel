# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — remote MCP mutation envelope and instruction identity
"""Keep an explicit MCP instruction string and refuse an unreadable mutation."""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mcp.types import CallToolResult, TextContent

from mcp_http_helpers import provision
from synapse_channel.build_identity import mcp_instructions
from synapse_channel.mcp.bridge import SynapseHubBridge
from synapse_channel.mcp.http_auth import HttpTokenVerifier
from synapse_channel.mcp.http_config import load_http_auth_config
from synapse_channel.mcp.http_policy import HttpOperationPolicy
from synapse_channel.mcp.http_server import HttpMcpServer, qualify_mutation_result
from synapse_channel.mcp.http_views import ProjectMcpViews


def test_qualify_mutation_result_rejects_an_unreadable_envelope() -> None:
    """A missing or mistyped remote envelope never becomes a successful mutation."""
    with pytest.raises(RuntimeError, match="remote MCP mutation result unavailable"):
        qualify_mutation_result(
            "not-a-result",
            name="synapse_send",
            task_id="ALPHA/task",
            seat="ALPHA/alice",
        )
    missing = CallToolResult(
        content=[TextContent(type="text", text="hidden")],
        structuredContent=None,
    )
    with pytest.raises(RuntimeError, match="remote MCP mutation result unavailable"):
        qualify_mutation_result(
            missing,
            name="synapse_send",
            task_id="ALPHA/task",
            seat="ALPHA/alice",
        )
    bad_text = CallToolResult(
        content=[TextContent(type="text", text="hidden")],
        structuredContent={"result": 7, "execution_outcome": {"state": "unknown"}},
    )
    with pytest.raises(RuntimeError, match="remote MCP mutation result unavailable"):
        qualify_mutation_result(
            bad_text,
            name="synapse_send",
            task_id="ALPHA/task",
            seat="ALPHA/alice",
        )
    bad_outcome = CallToolResult(
        content=[TextContent(type="text", text="hidden")],
        structuredContent={"result": "sent to ALPHA/bob", "execution_outcome": ["unknown"]},
    )
    with pytest.raises(RuntimeError, match="remote MCP mutation result unavailable"):
        qualify_mutation_result(
            bad_outcome,
            name="synapse_release",
            task_id="ALPHA/task",
            seat="ALPHA/alice",
        )


def test_qualify_mutation_result_keeps_a_match_and_refuses_a_mismatch() -> None:
    """A confirmed envelope stays. An error or a different receipt is refused."""
    confirmed = CallToolResult(
        content=[TextContent(type="text", text="sent to ALPHA/bob")],
        structuredContent={
            "result": "sent to ALPHA/bob",
            "execution_outcome": {"state": "confirmed_done"},
        },
        isError=False,
    )
    qualify_mutation_result(
        confirmed,
        name="synapse_send",
        task_id="ALPHA/task",
        seat="ALPHA/alice",
    )
    assert confirmed.isError is False
    assert confirmed.structuredContent is not None
    assert confirmed.structuredContent["result"] == "sent to ALPHA/bob"

    release = CallToolResult(
        content=[
            TextContent(
                type="text",
                text="released 'ALPHA/task' with receipt owner 'ALPHA/alice'",
            )
        ],
        structuredContent={
            "result": "released 'ALPHA/task' with receipt owner 'ALPHA/alice'",
            "execution_outcome": {"state": "confirmed_done"},
        },
    )
    qualify_mutation_result(
        release,
        name="synapse_release",
        task_id="ALPHA/task",
        seat="ALPHA/alice",
    )
    assert release.isError is False

    refused = CallToolResult(
        content=[TextContent(type="text", text="sent to ALPHA/bob")],
        structuredContent={
            "result": "sent to someone else",
            "execution_outcome": {"state": "confirmed_done"},
        },
        isError=True,
    )
    qualify_mutation_result(
        refused,
        name="synapse_send",
        task_id="ALPHA/task",
        seat="ALPHA/alice",
    )
    assert refused.isError is True
    assert refused.structuredContent == {
        "result": "remote MCP operation refused or unavailable",
        "execution_outcome": {"state": "confirmed_done"},
    }
    visible = refused.content[0]
    assert isinstance(visible, TextContent)
    assert visible.text == "remote MCP operation refused or unavailable"


def test_remote_server_rejects_invalid_operation_limits(tmp_path: Path) -> None:
    """A zero timeout or a tiny reply cap is refused before the server starts."""
    issuer = Ed25519PrivateKey.generate()
    grants = tmp_path / "grants.json"
    provision(grants, issuer)
    verifier = HttpTokenVerifier(grants)
    grant = load_http_auth_config(grants).subjects["alice"].projects["ALPHA"]
    bridge = SynapseHubBridge(uri="ws://127.0.0.1:1", name=grant.seat, request_timeout=0.05)
    views = ProjectMcpViews(bridge, "ALPHA")
    policy = HttpOperationPolicy(verifier, "alice", "ALPHA", grant)
    with pytest.raises(ValueError, match="invalid remote MCP operation limits"):
        HttpMcpServer(policy, views, operation_timeout=0)
    with pytest.raises(ValueError, match="invalid remote MCP operation limits"):
        HttpMcpServer(policy, views, reply_bytes=1023)


def test_explicit_mcp_instructions_stay_on_the_server(tmp_path: Path) -> None:
    """A non-empty instruction string is kept; a blank one uses the build identity."""
    issuer = Ed25519PrivateKey.generate()
    grants = tmp_path / "grants.json"
    provision(grants, issuer)
    verifier = HttpTokenVerifier(grants)
    grant = load_http_auth_config(grants).subjects["alice"].projects["ALPHA"]
    bridge = SynapseHubBridge(uri="ws://127.0.0.1:1", name=grant.seat, request_timeout=0.05)
    views = ProjectMcpViews(bridge, "ALPHA")
    policy = HttpOperationPolicy(verifier, "alice", "ALPHA", grant)
    custom = "operator supplied MCP instructions for ALPHA"
    kept = HttpMcpServer(policy, views, instructions=custom)
    assert kept.instructions == custom
    replaced = HttpMcpServer(policy, views, instructions=" \n\t")
    assert replaced.instructions == mcp_instructions()
    assert replaced.instructions is not None
    assert custom not in replaced.instructions
