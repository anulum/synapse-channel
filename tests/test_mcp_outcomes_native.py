# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — native MCP outcome and lost-reply journeys
"""Use the actual stdio client/server and selectively lose a real hub reply."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent
from pydantic import AnyHttpUrl
from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from hub_e2e_helpers import running_hub
from mcp_http_helpers import access_token, https_server, provision
from synapse_channel.a2a_server import A2ABridge
from synapse_channel.a2a_store import A2ATaskStore
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.client.owner_registration import close_owner, register_owner
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.protocol import MessageType
from synapse_channel.mcp.bridge import SynapseHubBridge
from synapse_channel.mcp.http_agent import HttpHubAgent
from synapse_channel.mcp.http_auth import HttpTokenVerifier
from synapse_channel.mcp.http_config import load_http_auth_config
from synapse_channel.mcp.http_policy import HttpOperationPolicy
from synapse_channel.mcp.http_server import HttpMcpServer
from synapse_channel.mcp.http_views import ProjectMcpViews
from synapse_channel.mcp.outcome import McpOutcomeFailure


@pytest.mark.asyncio
@pytest.mark.parametrize("envelope", [False, True])
async def test_actual_https_refuses_malformed_registered_tool_results(
    tmp_path: Path, envelope: bool
) -> None:
    """The native SDK embedding API cannot turn a malformed tool result into remote success.

    The deliberately invalid registered handler performs no business mutation.
    This qualifies output refusal through real authenticated HTTPS, not vendor
    compatibility or a fabricated successful hub operation.
    """
    issuer = Ed25519PrivateKey.generate()
    path = tmp_path / "grants.json"
    trust = provision(path, issuer)
    verifier = HttpTokenVerifier(path)
    grant = load_http_auth_config(path).subjects["alice"].projects["ALPHA"]
    hub = SynapseHub(identity_trust_bundle=trust, require_identity_binding=True)
    async with running_hub(hub) as (_, uri):
        bridge = SynapseHubBridge(
            uri=uri,
            name=grant.seat,
            agent_factory=partial(
                HttpHubAgent,
                identity_key_path=grant.identity_key_file,
                identity_key_id=grant.identity_key_id,
                machine_identity=False,
            ),
        )
        listener = await register_owner(bridge.agent, 3)
        assert listener is not None
        try:
            server = HttpMcpServer(
                HttpOperationPolicy(verifier, "alice", "ALPHA", grant),
                ProjectMcpViews(bridge, "ALPHA"),
                token_verifier=verifier,
                json_response=True,
                auth=AuthSettings(
                    issuer_url=AnyHttpUrl(verifier.issuer),
                    resource_server_url=AnyHttpUrl(verifier.resource),
                    validate_token_resource=True,
                    required_scopes=["synapse:read"],
                ),
                transport_security=TransportSecuritySettings(
                    enable_dns_rebinding_protection=True,
                    allowed_hosts=["127.0.0.1:*"],
                    allowed_origins=[],
                ),
            )

            @server.tool(name="synapse_task_declare", structured_output=False)
            async def invalid_result(task_id: str, title: str) -> str | CallToolResult:
                """Return a malformed plugin value without declaring any native task."""
                if not envelope:
                    return "UNTRUSTED_PLUGIN_RESULT"
                return CallToolResult(
                    content=[TextContent(type="text", text="UNTRUSTED_PLUGIN_RESULT")],
                    structuredContent={"result": 7, "execution_outcome": []},
                )

            async with https_server(server.streamable_http_app(), tmp_path) as (url, tls):
                async with httpx.AsyncClient(
                    verify=tls,
                    timeout=5,
                    trust_env=False,
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
                            result = await session.call_tool(
                                "synapse_task_declare",
                                {"task_id": "ALPHA/invalid-result", "title": "No mutation"},
                                meta={"synapse/operation-id": "invalid-result"},
                            )
                            assert result.isError
                            assert "operation refused or unavailable" in result.model_dump_json()
                            assert "UNTRUSTED_PLUGIN_RESULT" not in result.model_dump_json()
                            assert not hub.blackboard.tasks and not hub.state.claims
        finally:
            await close_owner(bridge.agent, listener)


@pytest.mark.asyncio
async def test_actual_closed_socket_keeps_mcp_and_a2a_mutations_unknown(tmp_path: Path) -> None:
    """Close a real admitted transport while its public callback is still processing."""
    entered, release = asyncio.Event(), asyncio.Event()

    def factory(
        name: str,
        callback: Callable[[dict[str, Any]], Awaitable[None]],
        **options: Any,
    ) -> SynapseAgent:
        """Hold only the fixture's correlated snapshot callback across socket closure."""

        async def receive(frame: dict[str, Any]) -> None:
            """Retain real callback processing until the transport failure is observed."""
            if frame.get("request_id") == "native-close-barrier":
                entered.set()
                await release.wait()
            await callback(frame)

        return SynapseAgent(name, receive, **options)

    async with running_hub() as (hub, uri):
        bridge = SynapseHubBridge(uri=uri, name="TEST/closed", agent_factory=factory)
        listener = await register_owner(bridge.agent, 3)
        assert listener is not None
        try:
            await bridge.agent.send_message(
                MessageType.STATE_REQUEST, target="System", request_id="native-close-barrier"
            )
            await asyncio.wait_for(entered.wait(), 3)
            connection = bridge.agent.connection
            assert connection is not None
            await connection.close()
            with pytest.raises(McpOutcomeFailure) as failure:
                await bridge.task_declare("never-issued", "Actual transport failure")
            report = failure.value.outcome.to_json()
            assert report["state"] == report["external_effect"] == "unknown"
            assert report["observation"] == "send_unconfirmed"
            assert not report["automatic_retry"]
            assert "never-issued" not in hub.blackboard.tasks

            loop = asyncio.get_running_loop()

            def submit(coro: Coroutine[Any, Any, Any]) -> Any:
                """Run the real closed client's send on its owning event loop."""
                return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=3)

            a2a = A2ABridge(
                agent=bridge.agent,
                agent_card={},
                target="TEST/worker",
                store=A2ATaskStore(tmp_path / "state.json"),
                submit=submit,
            )
            with pytest.raises(ConnectionClosed):
                await asyncio.to_thread(
                    a2a.create_working_task,
                    {
                        "taskId": "failed-send",
                        "messageId": "failed-send-message",
                        "role": "ROLE_USER",
                        "parts": [{"text": "no transport replay"}],
                    },
                )
            task = a2a.get_task("failed-send")
            assert task is not None
            outcome = task["metadata"]["synapseExecutionOutcome"]
            assert task["status"]["state"] == "TASK_STATE_FAILED"
            assert outcome["state"] == outcome["external_effect"] == "unknown"
            assert not outcome["automatic_retry"]
        finally:
            release.set()
            await close_owner(bridge.agent, listener)


@asynccontextmanager
async def native_session(uri: str, root: Path) -> AsyncIterator[ClientSession]:
    """Run only the repository's finite MCP server, with isolated fixture state."""
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(root / "state")
    env["SYN_HOME"] = str(root / "syn-home")
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "synapse_channel.cli",
            "mcp",
            "--uri",
            uri,
            "--name",
            "TEST/outcome",
            "--request-timeout",
            "0.25",
        ],
        env=env,
    )
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def report(result: CallToolResult) -> dict[str, Any]:
    """Read actual structured content and require the legacy text to agree."""
    assert result.structuredContent is not None
    assert result.content and result.content[0].type == "text"
    assert result.structuredContent["result"] == result.content[0].text
    outcome = result.structuredContent["execution_outcome"]
    assert isinstance(outcome, dict)
    assert outcome["schema"] == "synapse.execution-outcome.v1"
    assert outcome["boundary"] == "mcp_exchange"
    assert not outcome["automatic_retry"]
    return outcome


@pytest.mark.asyncio
async def test_real_stdio_refusal_claim_and_receipt_have_scoped_reports(tmp_path: Path) -> None:
    """Preserve human text while distinguishing refusal, reply and remote effects."""
    async with running_hub() as (hub, uri):
        async with native_session(uri, tmp_path) as session:
            refused = await session.call_tool(
                "synapse_claim", {"task_id": "refused", "paths": ["x"], "task_only": True}
            )
            outcome = report(refused)
            assert outcome["state"] == outcome["external_effect"] == "not_started"
            assert "refused" not in hub.state.claims

            claimed = await session.call_tool(
                "synapse_claim", {"task_id": "native-owned", "task_only": True}
            )
            acquired = report(claimed)
            assert acquired["state"] == "confirmed_done"
            assert acquired["external_effect"] == "unknown"
            assert acquired["external_recovery"] == "inquire_or_operator"
            assert hub.state.claims["native-owned"].owner == "TEST/outcome"

            released = await session.call_tool("synapse_release", {"task_id": "native-owned"})
            settled = report(released)
            assert settled["state"] == "confirmed_done"
            assert settled["observation"] == "matching_release_receipt"
            assert settled["operation_id"] != acquired["operation_id"]
            assert "native-owned" not in hub.state.claims


@pytest.mark.asyncio
async def test_lost_real_grant_stays_unknown_and_inquiry_does_not_replay(tmp_path: Path) -> None:
    """A transport drops an actual winning grant; only a state inquiry follows."""
    counts = {"claims": 0, "dropped": 0}
    async with running_hub() as (hub, uri):

        async def proxy(front: ServerConnection) -> None:
            """Forward native frames while withholding the exact real grant."""
            async with connect(uri) as backend:

                async def requests() -> None:
                    """Count actual mutation frames received from the stdio backend."""
                    async for raw in front:
                        frame = json.loads(raw)
                        if frame.get("type") == MessageType.CLAIM:
                            counts["claims"] += 1
                        await backend.send(raw)

                async def replies() -> None:
                    """Drop only the grant after the actual hub mutated its state."""
                    async for raw in backend:
                        frame = json.loads(raw)
                        if (
                            frame.get("type") == MessageType.CLAIM_GRANTED
                            and frame.get("task_id") == "lost-grant"
                        ):
                            counts["dropped"] += 1
                            continue
                        await front.send(raw)

                jobs = [asyncio.create_task(requests()), asyncio.create_task(replies())]
                try:
                    await asyncio.wait(jobs, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for job in jobs:
                        job.cancel()
                    await asyncio.gather(*jobs, return_exceptions=True)

        async with serve(proxy, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            async with native_session(f"ws://127.0.0.1:{port}", tmp_path) as session:
                result = await session.call_tool(
                    "synapse_claim", {"task_id": "lost-grant", "task_only": True}
                )
                unknown = report(result)
                assert unknown["state"] == unknown["external_effect"] == "unknown"
                assert unknown["recovery"] == "inquire_or_operator"
                assert counts == {"claims": 1, "dropped": 1}
                assert hub.state.claims["lost-grant"].owner == "TEST/outcome"

                inquiry = await session.call_tool("synapse_state", {})
                assert inquiry.content and inquiry.content[0].type == "text"
                snapshot = json.loads(inquiry.content[0].text)
                matching = [
                    row
                    for row in snapshot["active_claims"]
                    if row["task_id"] == "lost-grant" and row["owner"] == "TEST/outcome"
                ]
                assert len(matching) == 1
                assert counts == {"claims": 1, "dropped": 1}
