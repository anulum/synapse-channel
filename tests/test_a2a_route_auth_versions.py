# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — A2A 1.0 authentication, version and cancellation contract per route
"""Per-route A2A 1.0 contract over the product's own HTTP server.

Each route that can create, read, stream or configure task state refuses a
missing bearer with the same AIP-193 ``UNAUTHENTICATED`` body and has no
effect. An unsupported ``A2A-Version`` is refused with ``VERSION_NOT_SUPPORTED``
on the JSON-RPC and streaming routes too, before any task exists. Cancelling a
task is idempotent and never sends a frame to the hub: local cancellation
does not claim to stop a remote agent.
"""

from __future__ import annotations

from typing import Any

import pytest

from a2a_server_helpers import HandlerHarness, RecordingAgent
from synapse_channel.a2a_server import A2ABridge
from synapse_channel.a2a_store import A2ATaskStore

BEARER = "route-contract-bearer"
UNAUTHENTICATED_BODY = {
    "error": {"code": 401, "details": [], "message": "Unauthorized", "status": "UNAUTHENTICATED"},
    "status": 401,
    "title": "Unauthorized",
    "type": "about:blank",
}


def _message(text: str) -> dict[str, Any]:
    return {"message": {"messageId": f"m-{text}", "role": "ROLE_USER", "parts": [{"text": text}]}}


def _bridge(*, auth: bool) -> tuple[A2ABridge, RecordingAgent]:
    agent = RecordingAgent()
    bridge = A2ABridge(
        agent=agent,
        agent_card={"name": "route-contract", "capabilities": {"streaming": True}},
        target="WORKER",
        store=A2ATaskStore(),
        auth_token=BEARER if auth else None,
        allowed_authorities=("a2a.example.test",),
    )
    return bridge, agent


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/message:stream", _message("stream")),
        ("POST", "/tasks/task-x:subscribe", None),
        ("GET", "/tasks/task-x/pushNotificationConfigs", None),
        ("POST", "/tasks/task-x/pushNotificationConfigs", {"url": "https://hooks.example.test/a"}),
    ],
)
def test_missing_bearer_is_refused_with_the_aip193_body_and_no_effect(
    method: str, path: str, body: dict[str, Any] | None
) -> None:
    """Stream, subscribe and push-config routes share the exact refusal body."""
    bridge, agent = _bridge(auth=True)

    status, payload = HandlerHarness(method, path, body=body, bridge=bridge).run()

    assert status == 401
    assert payload == UNAUTHENTICATED_BODY
    assert bridge.store.list_tasks() == []
    assert agent.messages == []


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/rpc", {"jsonrpc": "2.0", "id": 1, "method": "message/send", "params": _message("rpc")}),
        ("/message:stream", _message("stream")),
    ],
)
def test_unsupported_version_is_refused_on_rpc_and_stream_before_any_task(
    path: str, body: dict[str, Any]
) -> None:
    """``A2A-Version: 9.0`` yields VERSION_NOT_SUPPORTED with the requested version."""
    bridge, agent = _bridge(auth=False)

    status, payload = HandlerHarness(
        "POST", path, body=body, headers={"A2A-Version": "9.0"}, bridge=bridge
    ).run()

    assert status == 400
    assert payload["title"] == "Version not supported"
    assert payload["error"]["status"] == "INVALID_ARGUMENT"
    assert payload["error"]["details"] == [
        {
            "@type": "type.googleapis.com/google.rpc.ErrorInfo",
            "domain": "a2a-protocol.org",
            "metadata": {"requestedVersion": "9.0"},
            "reason": "VERSION_NOT_SUPPORTED",
        }
    ]
    assert bridge.store.list_tasks() == []
    assert agent.messages == []


def test_cancel_is_idempotent_and_sends_nothing_to_the_hub() -> None:
    """A second cancel returns the same CANCELED task; the hub saw only the original send."""
    bridge, agent = _bridge(auth=True)
    headers = {"Authorization": f"Bearer {BEARER}", "A2A-Version": "1.0"}
    status, created = HandlerHarness(
        "POST", "/message:send", body=_message("work"), headers=headers, bridge=bridge
    ).run()
    assert status == 200
    task_id = created["task"]["id"]
    assert agent.messages == [("WORKER", "work")]

    first_status, first = HandlerHarness(
        "POST", f"/tasks/{task_id}:cancel", headers=headers, bridge=bridge
    ).run()
    second_status, second = HandlerHarness(
        "POST", f"/tasks/{task_id}:cancel", headers=headers, bridge=bridge
    ).run()

    assert (first_status, second_status) == (200, 200)
    assert first["status"]["state"] == second["status"]["state"] == "TASK_STATE_CANCELED"
    assert first == second
    assert agent.messages == [("WORKER", "work")]
