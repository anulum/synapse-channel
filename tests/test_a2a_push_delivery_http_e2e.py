# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real HTTP and restart proof for A2A push outcomes

from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path
from typing import Any
from urllib.error import URLError

from a2a_server_helpers import RecordingAgent
from hub_e2e_helpers import _free_port
from synapse_channel.a2a_http import make_a2a_http_server
from synapse_channel.a2a_server import A2ABridge
from synapse_channel.a2a_store import A2ATaskStore


def _request_json(
    port: int,
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Send one authenticated request to the real stdlib A2A server."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2.0)
    raw = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Authorization": "Bearer bridge-secret"}
    if raw is not None:
        headers["Content-Type"] = "application/json"
    try:
        connection.request(method, path, body=raw, headers=headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def test_real_http_failure_keeps_task_and_evidence_survives_restart(tmp_path: Path) -> None:
    """A real protected route exposes dead-letter evidence after task acceptance."""
    state_file = tmp_path / "a2a-state.json"
    port = _free_port()
    attempts = 0

    def fail(_delivery: dict[str, Any]) -> None:
        nonlocal attempts
        attempts += 1
        raise URLError("receiver diagnostic with secret-like-value")

    store = A2ATaskStore(state_file)
    bridge = A2ABridge(
        agent=RecordingAgent(),
        agent_card={"name": "SYNAPSE CHANNEL"},
        target="WORKER",
        store=store,
        auth_token="bridge-secret",
        allowed_authorities=(f"127.0.0.1:{port}",),
        push_deliverer=fail,
        push_retry_delays_seconds=(0.0, 0.0),
        push_sleep=lambda _seconds: None,
        push_clock=iter((10.0, 11.0, 12.0)).__next__,
    )
    server = make_a2a_http_server(bridge=bridge, host="127.0.0.1", port=port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        send_status, sent = _request_json(
            port,
            "POST",
            "/message:send",
            body={
                "message": {
                    "taskId": "task-http",
                    "messageId": "message-http",
                    "role": "ROLE_USER",
                    "parts": [{"text": "work"}],
                },
                "configuration": {
                    "taskPushNotificationConfig": {
                        "pushNotificationConfig": {
                            "id": "cfg-http",
                            "webhookUrl": "https://receiver.example/hook",
                            "authentication": {
                                "scheme": "Bearer",
                                "credentials": "webhook-secret",
                            },
                        }
                    }
                },
            },
        )
        task = sent["task"]
        evidence_status, evidence_body = _request_json(
            port,
            "GET",
            "/tasks/task-http/pushNotificationDeliveries",
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert send_status == http.client.OK
    assert task["status"]["state"] == "TASK_STATE_WORKING"
    assert evidence_status == http.client.OK
    evidence = evidence_body["pushNotificationDeliveries"]
    assert [attempt["state"] for attempt in evidence] == [
        "retry-scheduled",
        "retry-scheduled",
        "dead-lettered",
    ]
    assert len({attempt["deliveryId"] for attempt in evidence}) == 1
    assert attempts == 3
    assert "webhook-secret" not in repr(evidence)
    assert "secret-like-value" not in repr(evidence)

    reloaded = A2ATaskStore(state_file)
    reloaded_task = reloaded.get("task-http")
    reloaded_evidence = reloaded.list_push_delivery_attempts("task-http")
    assert reloaded_task is not None
    assert reloaded_task["status"]["state"] == "TASK_STATE_FAILED"
    assert reloaded_evidence == evidence
