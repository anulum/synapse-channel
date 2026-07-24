# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — residual official TCK structured response scenarios
"""Exercise production send dispatch for the five TCK residual failure classes.

The official A2A TCK (commit ``5996b79``) residual suite is:

* four artifact scenarios (text, raw file, file URL, structured data)
* one direct Message response (not a Task wrapper)

These tests drive :meth:`A2ABridge.send_message` and the live HTTP+JSON edge
so residual claims are grounded in shipped handlers, not a parallel mock.
"""

from __future__ import annotations

import base64
import json
from http import HTTPStatus
from typing import Any

import pytest

from a2a_server_helpers import HandlerHarness, RecordingAgent
from synapse_channel.a2a_scenario_responses import (
    TCK_DATA_ARTIFACT_BODY,
    TCK_DIRECT_MESSAGE_BODY,
    TCK_FILE_MEDIA_TYPE,
    TCK_FILE_NAME,
    TCK_FILE_RAW_BYTES,
    TCK_FILE_URL,
    TCK_TEXT_ARTIFACT_BODY,
)
from synapse_channel.a2a_server import A2ABridge
from synapse_channel.a2a_store import A2ATaskStore

V1_HEADERS = {"A2A-Version": "1.0", "Content-Type": "application/json"}
SESSION = "unitdead"


def _tck_message_id(name: str) -> str:
    return f"tck-{name}-{SESSION}"


def _send_body(message_id: str) -> dict[str, Any]:
    return {
        "message": {
            "messageId": message_id,
            "role": "ROLE_USER",
            "parts": [{"text": "TCK artifact test"}],
        }
    }


def _bridge() -> A2ABridge:
    return A2ABridge(
        agent=RecordingAgent(),
        agent_card={"name": "residual-bridge"},
        target="WORKER",
        store=A2ATaskStore(),
    )


def _http_send(message_id: str) -> tuple[int, dict[str, Any], A2ABridge]:
    harness = HandlerHarness(
        "POST",
        "/message:send",
        body=_send_body(message_id),
        headers=V1_HEADERS,
        bridge=_bridge(),
    )
    status, _headers, raw = harness._request()
    body = json.loads(raw.decode("utf-8"))
    return status, body, harness.handler.bridge


def test_direct_message_response_via_dispatch() -> None:
    bridge = _bridge()
    result = bridge.send_message(_send_body(_tck_message_id("message-response")))
    assert "task" not in result
    message = result["message"]
    assert message["role"] == "ROLE_AGENT"
    assert message["parts"][0]["text"] == TCK_DIRECT_MESSAGE_BODY
    # No SYNAPSE forward for immediate Message profile.
    assert bridge.agent.messages == []


def test_direct_message_response_via_http_json() -> None:
    status, body, bridge = _http_send(_tck_message_id("message-response"))
    assert status == HTTPStatus.OK
    assert "task" not in body
    assert body["message"]["parts"][0]["text"] == TCK_DIRECT_MESSAGE_BODY
    assert bridge.agent.messages == []


@pytest.mark.parametrize(
    ("name", "check"),
    [
        (
            "artifact-text",
            lambda part: part.get("text") == TCK_TEXT_ARTIFACT_BODY,
        ),
        (
            "artifact-file",
            lambda part: (
                part.get("filename") == TCK_FILE_NAME
                and part.get("mediaType") == TCK_FILE_MEDIA_TYPE
                and base64.b64decode(part["raw"]) == TCK_FILE_RAW_BYTES
            ),
        ),
        (
            "artifact-file-url",
            lambda part: (
                part.get("url") == TCK_FILE_URL
                and part.get("filename") == TCK_FILE_NAME
                and part.get("mediaType") == TCK_FILE_MEDIA_TYPE
            ),
        ),
        (
            "artifact-data",
            lambda part: part.get("data") == TCK_DATA_ARTIFACT_BODY,
        ),
    ],
)
def test_structured_artifact_completed_task(name: str, check: Any) -> None:
    bridge = _bridge()
    result = bridge.send_message(_send_body(_tck_message_id(name)))
    task = result["task"]
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert task["artifacts"], "expected artifacts on completed task"
    artifact = task["artifacts"][0]
    assert artifact.get("artifactId")
    part = artifact["parts"][0]
    assert check(part)
    # Immediate scenario path does not forward to SYNAPSE.
    assert bridge.agent.messages == []
    # Task is durable in the store for GET.
    stored = bridge.store.get(str(task["id"]))
    assert stored is not None
    assert stored["artifacts"][0]["artifactId"] == artifact["artifactId"]


def test_structured_artifact_via_http_json_and_get() -> None:
    status, body, bridge = _http_send(_tck_message_id("artifact-text"))
    assert status == HTTPStatus.OK
    task = body["task"]
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert task["artifacts"][0]["parts"][0]["text"] == TCK_TEXT_ARTIFACT_BODY
    task_id = task["id"]
    get_harness = HandlerHarness(
        "GET",
        f"/tasks/{task_id}",
        headers=V1_HEADERS,
        bridge=bridge,
    )
    get_status, _headers, raw = get_harness._request()
    got = json.loads(raw.decode("utf-8"))
    assert get_status == HTTPStatus.OK
    assert got["artifacts"][0]["parts"][0]["text"] == TCK_TEXT_ARTIFACT_BODY


def test_metadata_scenario_override() -> None:
    bridge = _bridge()
    result = bridge.send_message(
        {
            "message": {
                "messageId": "not-a-tck-id",
                "role": "ROLE_USER",
                "parts": [{"text": "manual"}],
                "metadata": {"a2aScenario": "artifact-data"},
            }
        }
    )
    assert result["task"]["artifacts"][0]["parts"][0]["data"] == TCK_DATA_ARTIFACT_BODY


def test_default_path_still_returns_working_task() -> None:
    bridge = _bridge()
    result = bridge.send_message(_send_body("ordinary-message"))
    task = result["task"]
    assert task["status"]["state"] == "TASK_STATE_WORKING"
    assert task.get("artifacts") == []
    assert bridge.agent.messages
