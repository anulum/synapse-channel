# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the A2A HTTP+JSON bridge

from __future__ import annotations

from http import HTTPStatus

from a2a_server_helpers import HandlerHarness, RecordingAgent
from synapse_channel.a2a_server import A2ABridge
from synapse_channel.a2a_store import A2ATaskStore


def test_json_rpc_message_send_dispatches_to_bridge() -> None:
    harness = HandlerHarness(
        "POST",
        "/rpc",
        body={
            "jsonrpc": "2.0",
            "id": "req-1",
            "method": "message/send",
            "params": {
                "message": {
                    "messageId": "m1",
                    "role": "ROLE_USER",
                    "parts": [{"text": "json rpc"}],
                }
            },
        },
    )

    status, body = harness.run()

    assert status == HTTPStatus.OK
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == "req-1"
    assert body["result"]["task"]["status"]["state"] == "TASK_STATE_WORKING"
    sent = harness.handler.bridge.agent.messages[0]
    assert sent[0] == "WORKER"
    assert sent[1].startswith("json rpc")
    assert "[A2A-TASK:" in sent[1]


def test_json_rpc_unknown_method_returns_method_not_found_error() -> None:
    status, body = HandlerHarness(
        "POST",
        "/rpc",
        body={
            "jsonrpc": "2.0",
            "id": "req-1",
            "method": "unknown/method",
            "params": {},
        },
    ).run()

    assert status == HTTPStatus.OK
    assert body == {
        "jsonrpc": "2.0",
        "id": "req-1",
        "error": {"code": -32601, "message": "Method not found"},
    }


def test_message_send_rejects_task_id_with_path_separator() -> None:
    harness = HandlerHarness(
        "POST",
        "/message:send",
        body={
            "message": {
                "taskId": "../task",
                "messageId": "m1",
                "role": "ROLE_USER",
                "parts": [{"text": "status"}],
            }
        },
    )

    status, body = harness.run()

    assert status == HTTPStatus.BAD_REQUEST
    assert body["detail"] == "message.taskId contains unsupported characters"


def test_message_send_rejects_context_id_with_path_separator() -> None:
    harness = HandlerHarness(
        "POST",
        "/message:send",
        body={
            "message": {
                "contextId": "ctx/../x",
                "messageId": "m1",
                "role": "ROLE_USER",
                "parts": [{"text": "status"}],
            }
        },
    )

    status, body = harness.run()

    assert status == HTTPStatus.BAD_REQUEST
    assert body["detail"] == "message.contextId contains unsupported characters"


def test_message_send_rejects_duplicate_task_id() -> None:
    bridge = A2ABridge(agent=RecordingAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
    bridge.create_completed_task(
        {
            "taskId": "task-a",
            "messageId": "m1",
            "role": "ROLE_USER",
            "parts": [{"text": "first"}],
        },
        target="WORKER",
    )
    harness = HandlerHarness(
        "POST",
        "/message:send",
        body={
            "message": {
                "taskId": "task-a",
                "messageId": "m2",
                "role": "ROLE_USER",
                "parts": [{"text": "second"}],
            }
        },
    )
    harness.handler.bridge = bridge

    status, body = harness.run()

    assert status == HTTPStatus.BAD_REQUEST
    assert body["detail"] == "message.taskId already exists"
    stored = bridge.store.get("task-a")
    assert stored is not None
    assert stored["history"][0]["messageId"] == "m1"


def test_message_send_rejects_oversized_parts_array() -> None:
    harness = HandlerHarness(
        "POST",
        "/message:send",
        body={
            "message": {
                "messageId": "m1",
                "role": "ROLE_USER",
                "parts": [{"text": "x"}] * 65,
            }
        },
    )

    status, body = harness.run()

    assert status == HTTPStatus.BAD_REQUEST
    assert body["detail"] == "message.parts exceeds maximum supported length"
    assert harness.handler.bridge.list_tasks()["totalSize"] == 0


def test_bad_json_returns_a2a_problem_json() -> None:
    harness = HandlerHarness("POST", "/message:send", body=b"{")

    status, body = harness.run()

    assert status == HTTPStatus.BAD_REQUEST
    assert body["title"] == "Invalid JSON"


def test_oversized_json_body_is_rejected_before_parse() -> None:
    harness = HandlerHarness(
        "POST",
        "/message:send",
        body=b"{}",
        headers={"Content-Length": str(1024 * 1024 + 1)},
    )

    status, body = harness.run()

    assert status == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    assert body["title"] == "Request body too large"
