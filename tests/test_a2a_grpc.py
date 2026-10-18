# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — optional gRPC A2A binding tests
"""Exercise shipped gRPC SendMessage + GetTask against a live in-process peer."""

from __future__ import annotations

import asyncio
import secrets
import time
from pathlib import Path
from typing import Any, cast

import pytest

from a2a_server_helpers import RecordingAgent, _free_port
from synapse_channel.a2a_conformance import conformance_rows
from synapse_channel.a2a_grpc import (
    A2AGrpcClient,
    A2AGrpcPolicy,
    grpc_available,
    start_grpc_in_background,
)
from synapse_channel.a2a_server import A2ABridge
from synapse_channel.a2a_store import A2ATaskStore
from test_execution_outcomes_native import completed_task, live_bridge, request_message

pytestmark = pytest.mark.skipif(not grpc_available(), reason="grpcio not installed")


@pytest.mark.asyncio
async def test_native_grpc_exchange_preserves_operation_observation(tmp_path: Path) -> None:
    """The shipped JSON gRPC subset preserves outcomes across a real hub and peer reply."""
    async with live_bridge(tmp_path / "state.json") as (bridge, worker, http_port):
        port, token = _free_port(), secrets.token_urlsafe(24)
        server, thread = start_grpc_in_background(
            bridge, host="127.0.0.1", port=port, policy=A2AGrpcPolicy(bearer_token=token)
        )
        try:
            with A2AGrpcClient(
                f"127.0.0.1:{port}", bearer_token=token, timeout_seconds=3
            ) as client:
                message = request_message("grpc-native")
                del message["taskId"]
                sent = await asyncio.to_thread(client.send_message, {"message": message})
                task_id = sent["task"]["id"]
                observed = sent["task"]["metadata"]["synapseExecutionOutcome"]
                assert observed["state"] == "started"
                request = await worker.recorder.wait_for(
                    lambda row: row.get("metadata", {}).get("a2aTaskId") == task_id
                )
                await worker.agent.chat(
                    "native grpc reply", target="outcome-sender", metadata=request["metadata"]
                )
                await completed_task(http_port, task_id)
                retained = await asyncio.to_thread(client.get_task, task_id)
                result = retained["metadata"]["synapseExecutionOutcome"]
                assert result["operation_id"] == observed["operation_id"]
                assert result["state"] == "confirmed_done"
                assert result["external_effect"] == "unknown"
                assert not result["stop_confirmed"] and not result["effects_undone"]
        finally:
            server.stop(grace=0).wait(timeout=3)
            thread.join(timeout=3)
            assert not thread.is_alive()


def test_grpc_matrix_row_is_partial_not_unsupported() -> None:
    row = next(r for r in conformance_rows() if r.item == "gRPC")
    assert row.status == "partial"
    assert "a2a-grpc" in row.synapse_surface or "grpc" in row.synapse_surface.lower()


def test_grpc_send_message_and_get_task() -> None:
    bridge = A2ABridge(
        agent=RecordingAgent(),
        agent_card={"name": "grpc-bridge", "capabilities": {}},
        target="WORKER",
        store=A2ATaskStore(),
    )
    port = _free_port()
    server, _thread = start_grpc_in_background(bridge, host="127.0.0.1", port=port)
    time.sleep(0.05)
    try:
        with A2AGrpcClient(f"127.0.0.1:{port}") as client:
            sent = client.send_message(
                {
                    "message": {
                        "messageId": "grpc-msg-1",
                        "role": "ROLE_USER",
                        "parts": [{"text": "hello-grpc", "mediaType": "text/plain"}],
                    }
                }
            )
            assert "task" in sent
            task_id = str(sent["task"]["id"])
            assert task_id
            got = client.get_task(task_id)
            assert got["id"] == task_id
            blob = str(got)
            assert "hello-grpc" in blob or any(
                "hello-grpc" in text for _t, text in bridge.agent.messages
            )
    finally:
        server.stop(grace=None)


def test_grpc_get_unknown_task_returns_not_found() -> None:
    bridge = A2ABridge(
        agent=RecordingAgent(),
        agent_card={"name": "grpc-bridge"},
        target="WORKER",
        store=A2ATaskStore(),
    )
    port = _free_port()
    server, _thread = start_grpc_in_background(bridge, host="127.0.0.1", port=port)
    time.sleep(0.05)
    try:
        with A2AGrpcClient(f"127.0.0.1:{port}") as client:
            caught: object | None = None
            try:
                client.get_task("does-not-exist")
            except Exception as exc:  # gRPC raises transport-specific RpcError
                caught = exc
            assert caught is not None
            code = getattr(caught, "code", None)
            assert callable(code)
            assert code().name == "NOT_FOUND"
            assert cast(Any, caught).details() == "task not found"
    finally:
        server.stop(grace=None)


def test_grpc_policy_requires_bearer_before_any_bridge_effect() -> None:
    agent = RecordingAgent()
    bridge = A2ABridge(
        agent=agent,
        agent_card={"name": "grpc-bridge"},
        target="WORKER",
        store=A2ATaskStore(),
    )
    port = _free_port()
    server, _thread = start_grpc_in_background(
        bridge,
        host="127.0.0.1",
        port=port,
        policy=A2AGrpcPolicy(bearer_token="correct"),
    )
    time.sleep(0.05)
    payload = {
        "message": {
            "messageId": "grpc-auth-1",
            "role": "ROLE_USER",
            "parts": [{"text": "protected", "mediaType": "text/plain"}],
        }
    }
    try:
        for supplied in (None, "wrong"):
            caught: object | None = None
            try:
                with A2AGrpcClient(
                    f"127.0.0.1:{port}",
                    bearer_token=supplied,
                ) as client:
                    client.send_message(payload)
            except Exception as exc:
                caught = exc
            assert caught is not None
            code = getattr(caught, "code", None)
            assert callable(code)
            assert code().name == "UNAUTHENTICATED"
            assert cast(Any, caught).details() == "authentication required"
            assert bridge.store.list_tasks() == []
            assert agent.messages == []
            with pytest.raises(Exception) as read_error:
                with A2AGrpcClient(
                    f"127.0.0.1:{port}",
                    bearer_token=supplied,
                ) as client:
                    client.get_task("known-or-unknown")
            assert cast(Any, read_error.value).code().name == "UNAUTHENTICATED"
            assert cast(Any, read_error.value).details() == "authentication required"

        with A2AGrpcClient(
            f"127.0.0.1:{port}",
            bearer_token="correct",
        ) as client:
            sent = client.send_message(payload)
        assert sent["task"]["id"]
        assert len(bridge.store.list_tasks()) == 1
        assert agent.messages == [("WORKER", "protected")]
    finally:
        server.stop(grace=None)


def test_grpc_policy_refuses_non_positive_limits() -> None:
    with pytest.raises(ValueError, match="bearer_token must not be empty"):
        A2AGrpcPolicy(bearer_token="")
    with pytest.raises(ValueError, match="max_receive_message_bytes must be >= 1"):
        A2AGrpcPolicy(max_receive_message_bytes=0)
    with pytest.raises(ValueError, match="max_send_message_bytes must be >= 1"):
        A2AGrpcPolicy(max_send_message_bytes=0)
    with pytest.raises(ValueError, match="max_concurrent_rpcs must be >= 1"):
        A2AGrpcPolicy(max_concurrent_rpcs=0)
    with pytest.raises(ValueError, match="max_rpc_seconds must be finite and > 0"):
        A2AGrpcPolicy(max_rpc_seconds=0.0)
    with pytest.raises(ValueError, match="max_rpc_seconds must be finite and > 0"):
        A2AGrpcPolicy(max_rpc_seconds=float("inf"))


def test_grpc_client_refuses_non_positive_timeout() -> None:
    with pytest.raises(ValueError, match="timeout_seconds must be finite and > 0"):
        A2AGrpcClient("127.0.0.1:1", timeout_seconds=0.0)
    with pytest.raises(ValueError, match="timeout_seconds must be finite and > 0"):
        A2AGrpcClient("127.0.0.1:1", timeout_seconds=float("nan"))
    with pytest.raises(ValueError, match="bearer_token must not be empty"):
        A2AGrpcClient("127.0.0.1:1", bearer_token="")
