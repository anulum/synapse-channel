# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — stable HTTP mutation keys supplied as tool arguments
"""Exercise operation identity compatibility through actual HTTPS and durable effects."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import httpx2
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import RequestParamsMeta

from cli_lock_release_helpers import release_reply_proxy
from hub_e2e_helpers import running_hub
from mcp_http_helpers import SessionIdRecorder, access_token, https_server, provision
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.mcp.bridge import SynapseHubBridge
from synapse_channel.mcp.http_application import build_http_mcp_app
from synapse_channel.mcp.http_config import MUTATION_TOOLS
from synapse_channel.mcp.http_operation_arguments import mutation_arguments
from synapse_channel.mcp.registration import build_mcp_server


def _meta(value: object) -> RequestParamsMeta:
    """Return request metadata that carries ``value`` as the operation identifier."""
    return cast("RequestParamsMeta", {"synapse/operation-id": value})


@dataclass
class OperationSession:
    """The concrete SDK session and its independently inspectable native state."""

    session: ClientSession
    hub: SynapseHub
    journal: EventStore
    client: httpx2.AsyncClient
    session_id: Callable[[], str | None]
    wire_metadata: list[dict[str, Any]]
    fault: dict[str, bool | int]


@pytest.fixture
def remote(tmp_path: Path) -> Callable[[str], AbstractAsyncContextManager[OperationSession]]:
    """Retain issuer/native keys and the journal across bounded HTTP incarnations."""
    issuer = Ed25519PrivateKey.generate()
    grants = tmp_path / "grants.json"
    trust = provision(grants, issuer)

    @asynccontextmanager
    async def serve(scope: str) -> AsyncIterator[OperationSession]:
        journal = EventStore(tmp_path / "operations.db")
        hub = SynapseHub(
            hub_id="syn-operation-arguments",
            journal=journal,
            identity_trust_bundle=trust,
            require_identity_binding=True,
            require_fencing_epoch=True,
        )
        metadata: list[dict[str, Any]] = []
        recorder = SessionIdRecorder()

        async def capture(request: httpx2.Request) -> None:
            """Observe actual tool metadata without retaining headers or bearer material."""
            if request.method == "POST":
                body = json.loads(request.content)
                if body.get("method") == "tools/call":
                    metadata.append(body["params"].get("_meta", {}))

        try:
            async with (
                running_hub(hub) as (_, direct),
                release_reply_proxy(direct, drop_confirmation=True) as (uri, fault),
            ):
                fault["active"] = False
                app = build_http_mcp_app(
                    auth_file=grants,
                    project="ALPHA",
                    hub_uri=uri,
                    allowed_hosts=["127.0.0.1:*"],
                    allowed_origins=[],
                    operation_timeout=5,
                )
                async with https_server(app, tmp_path) as (url, tls):
                    async with httpx2.AsyncClient(
                        base_url=url,
                        verify=tls,
                        timeout=10,
                        trust_env=False,
                        headers={"Authorization": "Bearer " + access_token(issuer, scope=scope)},
                        event_hooks={"request": [capture], "response": [recorder]},
                    ) as client:
                        async with streamable_http_client(url + "/mcp", http_client=client) as (
                            read,
                            write,
                        ):
                            async with ClientSession(read, write) as session:
                                await session.initialize()
                                yield OperationSession(
                                    session,
                                    hub,
                                    journal,
                                    client,
                                    lambda: recorder.value,
                                    metadata,
                                    fault,
                                )
        finally:
            journal.close()

    return serve


async def test_all_mutations_accept_argument_keys_and_keep_native_receipts(
    remote: Callable[[str], AbstractAsyncContextManager[OperationSession]],
) -> None:
    """No custom MCP metadata is needed to exercise all six admitted mutation tools."""
    async with remote("synapse:read synapse:mutate") as runtime:
        session, hub = runtime.session, runtime.hub
        actions: list[tuple[str, dict[str, Any]]] = [
            ("synapse_task_declare", {"task_id": "ALPHA/primary", "title": "Argument keyed task"}),
            ("synapse_claim", {"task_id": "ALPHA/primary", "task_only": True}),
            ("synapse_task_update", {"task_id": "ALPHA/primary", "suggested_owner": "ALPHA/bob"}),
            ("synapse_send", {"target": "ALPHA/bob", "message": "argument keyed message"}),
            ("synapse_release", {"task_id": "ALPHA/primary"}),
        ]
        for number, (name, business) in enumerate(actions):
            arguments = {**business, "operation_id": f"argument-{number}"}
            first = await session.call_tool(name, arguments)
            assert not first.is_error
            replay = await session.call_tool(name, business, meta=_meta(f"argument-{number}"))
            assert not replay.is_error
            assert replay.structured_content == first.structured_content
        assert "ALPHA/primary" not in hub.state.claims
        assert hub.blackboard.tasks["ALPHA/primary"].version == 2
        messages = [
            row
            for row in runtime.journal.read_all()
            if row.kind == "chat" and row.payload.get("payload") == "argument keyed message"
        ]
        assert len(messages) == 1
        assert not (
            await session.call_tool(
                "synapse_task_declare",
                {"task_id": "ALPHA/handoff", "title": "Handoff", "operation_id": "handoff-declare"},
            )
        ).is_error
        assert not (
            await session.call_tool(
                "synapse_claim",
                {"task_id": "ALPHA/handoff", "task_only": True, "operation_id": "handoff-claim"},
            )
        ).is_error
        assert not (
            await session.call_tool(
                "synapse_handoff",
                {
                    "task_id": "ALPHA/handoff",
                    "to_agent": "ALPHA/bob",
                    "operation_id": "handoff-effect",
                },
            )
        ).is_error
        assert hub.state.claims["ALPHA/handoff"].owner == "ALPHA/bob"


async def test_http_discovery_preserves_stdio_and_accepts_matching_dual_keys(
    remote: Callable[[str], AbstractAsyncContextManager[OperationSession]],
) -> None:
    """Argument keys are visible only on HTTP mutations; metadata clients stay valid."""
    local = build_mcp_server(SynapseHubBridge(request_timeout=0.1))
    before = {tool.name: tool.model_dump() for tool in await local.list_tools()}
    async with remote("synapse:read synapse:mutate") as runtime:
        tools = {tool.name: tool for tool in (await runtime.session.list_tools()).tools}
        for name, tool in tools.items():
            properties = tool.input_schema["properties"]
            assert ("operation_id" in properties) == (name in MUTATION_TOOLS)
            if name in MUTATION_TOOLS:
                identity = properties["operation_id"]
                assert identity["minLength"] == 1 and identity["maxLength"] == 128
                assert "retry" in identity["description"].replace("retries", "retry")
                assert "operation_id" not in tool.input_schema.get("required", [])
        for operation in ("a", "a" + ":" * 127):
            result = await runtime.session.call_tool(
                "synapse_task_declare",
                {
                    "task_id": "ALPHA/" + operation,
                    "title": "Matched identity",
                    "operation_id": operation,
                },
                meta=_meta(operation),
            )
            assert not result.is_error
        assert (await runtime.session.call_tool("synapse_board", {})).is_error is False
    assert {tool.name: tool.model_dump() for tool in await local.list_tools()} == before


@pytest.mark.parametrize(
    "argument,metadata",
    [
        ({}, None),
        ({}, {"synapse/operation-id": None}),
        ({}, {"synapse/operation-id": 7}),
        ({}, {"synapse/operation-id": ""}),
        ({}, {"synapse/operation-id": "has whitespace"}),
        ({}, {"synapse/operation-id": "x" * 129}),
        ({"operation_id": None}, None),
        ({"operation_id": True}, None),
        ({"operation_id": ""}, None),
        ({"operation_id": "x" * 129}, None),
        ({"operation_id": "argument"}, {"synapse/operation-id": "metadata"}),
        ({"operation_id": "argument"}, {"synapse/operation-id": 7}),
    ],
)
async def test_missing_invalid_or_conflicting_keys_have_no_native_effect(
    remote: Callable[[str], AbstractAsyncContextManager[OperationSession]],
    argument: dict[str, Any],
    metadata: dict[str, Any] | None,
) -> None:
    """Rejected identity never executes a task and does not poison the next operation."""
    async with remote("synapse:read synapse:mutate") as runtime:
        refused = await runtime.session.call_tool(
            "synapse_task_declare",
            {"task_id": "ALPHA/refused", "title": "private-refused-title", **argument},
            meta=cast("RequestParamsMeta | None", metadata),
        )
        assert refused.is_error
        assert not runtime.hub.blackboard.tasks and not runtime.hub.state.claims
        accepted = await runtime.session.call_tool(
            "synapse_task_declare",
            {"task_id": "ALPHA/accepted", "title": "Known intent", "operation_id": "accepted-key"},
        )
        assert not accepted.is_error
        assert set(runtime.hub.blackboard.tasks) == {"ALPHA/accepted"}


async def test_argument_keys_cannot_add_mutation_authority(
    remote: Callable[[str], AbstractAsyncContextManager[OperationSession]],
) -> None:
    """A valid retry key cannot turn a read-only issuer token into mutation authority."""
    async with remote("synapse:read") as runtime:
        assert (
            not {tool.name for tool in (await runtime.session.list_tools()).tools} & MUTATION_TOOLS
        )
        result = await runtime.session.call_tool(
            "synapse_task_declare",
            {"task_id": "ALPHA/not-authorised", "title": "No effect", "operation_id": "valid-key"},
        )
        assert result.is_error
        assert not runtime.hub.blackboard.tasks and not runtime.hub.state.claims


async def test_argument_to_metadata_replay_survives_http_and_hub_restart(
    remote: Callable[[str], AbstractAsyncContextManager[OperationSession]],
) -> None:
    """A restarted adapter reconciles the original retained declaration and release."""
    for incarnation in range(2):
        async with remote("synapse:read synapse:mutate") as runtime:
            business = {"task_id": "ALPHA/restart", "title": "Retained original"}
            result = await runtime.session.call_tool(
                "synapse_task_declare",
                business if incarnation else {**business, "operation_id": "restart-declare"},
                meta=_meta("restart-declare") if incarnation else None,
            )
            assert not result.is_error
            assert runtime.hub.blackboard.tasks["ALPHA/restart"].version == 1
            if not incarnation:
                claimed = await runtime.session.call_tool(
                    "synapse_claim",
                    {
                        "task_id": "ALPHA/restart",
                        "task_only": True,
                        "operation_id": "restart-claim",
                    },
                )
                assert not claimed.is_error
            release = await runtime.session.call_tool(
                "synapse_release",
                {"task_id": "ALPHA/restart", "operation_id": "restart-release"},
            )
            assert not release.is_error
            assert "ALPHA/restart" not in runtime.hub.state.claims


async def test_null_metadata_is_distinguished_from_what_the_sdk_omits(
    remote: Callable[[str], AbstractAsyncContextManager[OperationSession]],
) -> None:
    """Actual JSON null conflicts; SDK omission leaves the valid argument as the sole key."""
    async with remote("synapse:read synapse:mutate") as runtime:
        accepted = await runtime.session.call_tool(
            "synapse_task_declare",
            {"task_id": "ALPHA/sdk-null", "title": "SDK omits null", "operation_id": "sdk-null"},
            meta=_meta(None),
        )
        assert not accepted.is_error
        assert "synapse/operation-id" not in runtime.wire_metadata[-1]
        session_id = runtime.session_id()
        assert session_id is not None
        response = await runtime.client.post(
            "/mcp",
            headers={
                "Mcp-Session-Id": session_id,
                "MCP-Protocol-Version": "2025-11-25",
                "Accept": "application/json, text/event-stream",
            },
            json={
                "jsonrpc": "2.0",
                "id": 7000,
                "method": "tools/call",
                "params": {
                    "name": "synapse_task_declare",
                    "arguments": {
                        "task_id": "ALPHA/raw-null",
                        "title": "No effect",
                        "operation_id": "raw-null",
                    },
                    "_meta": {"synapse/operation-id": None},
                },
            },
        )
        assert response.status_code == 200 and response.json()["result"]["isError"] is True
        assert runtime.wire_metadata[-1] == {"synapse/operation-id": None}
        assert set(runtime.hub.blackboard.tasks) == {"ALPHA/sdk-null"}


def test_the_identifier_is_taken_out_of_the_business_arguments() -> None:
    """The shared tool receives the caller's arguments without the adapter's own key."""
    given = {"task_id": "ALPHA/task", "title": "Kept", "operation_id": "key-1"}
    operation, business = mutation_arguments(given, None)
    assert operation == "key-1"
    assert business == {"task_id": "ALPHA/task", "title": "Kept"}
    assert given == {"task_id": "ALPHA/task", "title": "Kept", "operation_id": "key-1"}

    from_metadata, unchanged = mutation_arguments(
        {"task_id": "ALPHA/task"}, {"synapse/operation-id": "key-2"}
    )
    assert from_metadata == "key-2"
    assert unchanged == {"task_id": "ALPHA/task"}


async def test_a_mutation_on_a_task_the_board_does_not_hold_is_refused(
    remote: Callable[[str], AbstractAsyncContextManager[OperationSession]],
) -> None:
    """Only a declaration may name a task the board does not know."""
    async with remote("synapse:read synapse:mutate") as runtime:
        refused = await runtime.session.call_tool(
            "synapse_claim",
            {"task_id": "ALPHA/never-declared", "task_only": True, "operation_id": "claim-1"},
        )
        assert refused.is_error
        assert not runtime.hub.state.claims and not runtime.hub.blackboard.tasks
