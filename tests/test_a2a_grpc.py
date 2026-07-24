# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — optional gRPC A2A binding tests
"""Exercise shipped gRPC SendMessage + GetTask against a live in-process peer."""

from __future__ import annotations

import time

import pytest

from a2a_server_helpers import RecordingAgent, _free_port
from synapse_channel.a2a_conformance import conformance_rows
from synapse_channel.a2a_grpc import (
    A2AGrpcClient,
    grpc_available,
    start_grpc_in_background,
)
from synapse_channel.a2a_server import A2ABridge
from synapse_channel.a2a_store import A2ATaskStore

pytestmark = pytest.mark.skipif(not grpc_available(), reason="grpcio not installed")


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
    finally:
        server.stop(grace=None)
