# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — official A2A SDK/TCK HTTP+JSON regressions
"""Pin defects found by a2a-sdk 1.1.0 and official TCK commit 5996b79."""

from __future__ import annotations

import json
from http import HTTPStatus
from typing import Any

import pytest

from a2a_server_helpers import HandlerHarness, RecordingAgent
from synapse_channel import a2a_errors, a2a_http_protocol, a2a_task_flow
from synapse_channel.a2a_server import A2ABridge
from synapse_channel.a2a_store import A2ATaskStore

V1_HEADERS = {"A2A-Version": "1.0", "Content-Type": "application/json"}


def _request(harness: HandlerHarness) -> tuple[int, dict[str, str], dict[str, Any]]:
    status, headers, raw = harness._request()
    return status, headers, json.loads(raw.decode("utf-8"))


def _message(*, task_id: str | None = None, context_id: str | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {
        "messageId": "official-tck-message",
        "role": "ROLE_USER",
        "parts": [{"text": "official TCK probe"}],
    }
    if task_id is not None:
        message["taskId"] = task_id
    if context_id is not None:
        message["contextId"] = context_id
    return message


def _existing_task() -> tuple[A2ABridge, dict[str, Any]]:
    bridge = A2ABridge(
        agent=RecordingAgent(),
        agent_card={},
        target="WORKER",
        store=A2ATaskStore(),
    )
    task = bridge.create_working_task(
        {
            "taskId": "task-existing",
            "contextId": "context-existing",
            "messageId": "first-message",
            "role": "ROLE_USER",
            "parts": [{"text": "first"}],
        }
    )
    return bridge, task


def test_http_json_response_uses_normative_media_type_and_iso_timestamps() -> None:
    harness = HandlerHarness(
        "POST",
        "/message:send",
        body={"message": _message()},
        headers=V1_HEADERS,
    )

    status, headers, body = _request(harness)

    assert status == HTTPStatus.OK
    assert headers["Content-Type"] == "application/json"
    metadata = body["task"]["metadata"]
    assert metadata["createdAt"].endswith("Z")
    assert metadata["updatedAt"].endswith("Z")
    stored = harness.handler.bridge.store.get(body["task"]["id"])
    assert stored is not None
    assert isinstance(stored["metadata"]["createdAt"], float)
    assert isinstance(stored["metadata"]["updatedAt"], float)


@pytest.mark.parametrize("version", ["99.0", "garbage", "1"])
def test_explicit_unsupported_version_returns_aip193_error(version: str) -> None:
    harness = HandlerHarness(
        "POST",
        "/message:send",
        body={"message": _message()},
        headers={"A2A-Version": version, "Content-Type": "application/json"},
    )

    status, headers, body = _request(harness)

    assert status == HTTPStatus.BAD_REQUEST
    assert headers["Content-Type"] == "application/json"
    assert body["error"]["code"] == HTTPStatus.BAD_REQUEST
    assert body["error"]["status"] == "INVALID_ARGUMENT"
    assert body["error"]["details"][0] == {
        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
        "domain": "a2a-protocol.org",
        "metadata": {"requestedVersion": version},
        "reason": "VERSION_NOT_SUPPORTED",
    }


@pytest.mark.parametrize("version", [None, "", "1.0", "1.0.9"])
def test_compatible_or_patch_versions_are_accepted(version: str | None) -> None:
    headers = {"Content-Type": "application/json"}
    if version is not None:
        headers["A2A-Version"] = version
    status, _headers, _body = _request(
        HandlerHarness(
            "POST",
            "/message:send",
            body={"message": _message()},
            headers=headers,
        )
    )
    assert status == HTTPStatus.OK


def test_version_query_parameter_is_validated_when_header_is_absent() -> None:
    harness = HandlerHarness(
        "POST",
        "/message:send?A2A-Version=2.0",
        body={"message": _message()},
        headers={"Content-Type": "application/json"},
    )
    status, _headers, body = _request(harness)
    assert status == HTTPStatus.BAD_REQUEST
    assert body["error"]["details"][0]["reason"] == "VERSION_NOT_SUPPORTED"


def test_v1_unknown_message_task_id_returns_task_not_found() -> None:
    harness = HandlerHarness(
        "POST",
        "/message:send",
        body={"message": _message(task_id="missing")},
        headers=V1_HEADERS,
    )

    status, headers, body = _request(harness)

    assert status == HTTPStatus.NOT_FOUND
    assert headers["Content-Type"] == "application/json"
    assert body["error"]["code"] == HTTPStatus.NOT_FOUND
    assert body["error"]["details"][0]["reason"] == "TASK_NOT_FOUND"
    assert harness.handler.bridge.list_tasks()["totalSize"] == 0


def test_v1_existing_task_continues_and_infers_context() -> None:
    bridge, original = _existing_task()
    harness = HandlerHarness(
        "POST",
        "/message:send",
        body={"message": _message(task_id="task-existing")},
        headers=V1_HEADERS,
    )
    harness.handler.bridge = bridge

    status, _headers, body = _request(harness)

    assert status == HTTPStatus.OK
    task = body["task"]
    assert task["id"] == original["id"]
    assert task["contextId"] == original["contextId"]
    assert task["history"][-1]["contextId"] == "context-existing"
    assert len(task["history"]) == 2
    assert bridge.agent.messages[-1] == ("WORKER", "official TCK probe")


def test_v1_continuation_survives_malformed_legacy_history() -> None:
    bridge, original = _existing_task()
    original["history"] = "legacy-malformed"
    bridge.store.put(original)

    result = bridge.send_message(
        {"message": _message(task_id="task-existing")},
        protocol_version="1.0",
    )

    assert result["task"]["history"] == "legacy-malformed"
    assert bridge.agent.messages[-1] == ("WORKER", "official TCK probe")


def test_v1_inline_push_config_uses_direct_task_config_shape() -> None:
    deliveries: list[dict[str, Any]] = []
    bridge = A2ABridge(
        agent=RecordingAgent(),
        agent_card={},
        target="WORKER",
        store=A2ATaskStore(),
        push_deliverer=deliveries.append,
    )

    result = bridge.send_message(
        {
            "message": _message(),
            "configuration": {
                "taskPushNotificationConfig": {
                    "id": "tck-push-config",
                    "url": "https://example.test/hook",
                    "authentication": {
                        "scheme": "Bearer",
                        "credentials": "redacted-test-token",
                    },
                }
            },
        },
        protocol_version="1.0",
    )

    task_id = result["task"]["id"]
    stored = bridge.list_push_notification_configs(task_id)["pushNotificationConfigs"]
    assert stored[0]["id"] == "tck-push-config"
    assert stored[0]["webhookUrl"] == "https://example.test/hook"
    assert deliveries[0]["url"] == "https://example.test/hook"
    assert deliveries[0]["headers"] == {"Authorization": "Bearer redacted-test-token"}


def test_v1_task_and_context_mismatch_fails_closed() -> None:
    bridge, _original = _existing_task()
    harness = HandlerHarness(
        "POST",
        "/message:send",
        body={"message": _message(task_id="task-existing", context_id="wrong-context")},
        headers=V1_HEADERS,
    )
    harness.handler.bridge = bridge

    status, _headers, body = _request(harness)

    assert status == HTTPStatus.BAD_REQUEST
    assert body["error"]["message"] == "message.contextId does not match message.taskId"
    stored = bridge.store.get("task-existing")
    assert stored is not None
    assert len(stored["history"]) == 1


def test_v1_terminal_task_rejects_followup() -> None:
    bridge, original = _existing_task()
    bridge.handle_synapse_frame(
        {
            "type": "chat",
            "sender": "WORKER",
            "payload": "done",
            "metadata": {
                "a2aTaskId": original["id"],
                "a2aContextId": original["contextId"],
            },
        }
    )
    harness = HandlerHarness(
        "POST",
        "/message:send",
        body={"message": _message(task_id="task-existing")},
        headers=V1_HEADERS,
    )
    harness.handler.bridge = bridge

    status, _headers, body = _request(harness)

    assert status == HTTPStatus.CONFLICT
    assert body["error"]["details"][0]["reason"] == "TASK_NOT_CANCELABLE"
    stored = bridge.store.get("task-existing")
    assert stored is not None
    assert stored["status"]["state"] == "TASK_STATE_COMPLETED"


def test_http_protocol_helpers_cover_safe_projection_and_error_mapping() -> None:
    projected = a2a_http_protocol.to_wire_json(
        {
            "createdAt": 0.0,
            "nested": [{"updatedAt": 1.25, "timestamp": True}],
            "huge": {"lastModified": 10**400},
            "finiteButUnrepresentable": {"updatedAt": 1e300},
        }
    )
    assert projected == {
        "createdAt": "1970-01-01T00:00:00.000Z",
        "nested": [{"updatedAt": "1970-01-01T00:00:01.250Z", "timestamp": True}],
        "huge": {"lastModified": 10**400},
        "finiteButUnrepresentable": {"updatedAt": "1970-01-01T00:00:00.000Z"},
    }
    assert a2a_http_protocol.error_info_reason(a2a_errors.A2ANotFoundError()) == "TASK_NOT_FOUND"
    assert (
        a2a_http_protocol.error_info_reason(a2a_errors.A2AConflictError()) == "TASK_NOT_CANCELABLE"
    )
    assert a2a_http_protocol.error_info_reason(a2a_errors.A2AValidationError()) is None
    assert a2a_http_protocol.parse_push_config_path("/other") is None
    assert a2a_http_protocol.parse_push_config_path("/tasks//pushNotificationConfigs") is None
    assert a2a_task_flow.stored_task_target({}, default="fallback") == "fallback"
    assert a2a_task_flow.stored_task_target({"metadata": {}}, default="fallback") == "fallback"


def test_problem_response_without_a2a_reason_retains_empty_details() -> None:
    body = a2a_http_protocol.problem_response(HTTPStatus.IM_A_TEAPOT, "Short and stout")
    assert body["error"] == {
        "code": HTTPStatus.IM_A_TEAPOT,
        "status": "IM_A_TEAPOT",
        "message": "Short and stout",
        "details": [],
    }
