# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the A2A HTTP+JSON bridge

from __future__ import annotations

import json
from http import HTTPStatus
from io import BytesIO
from pathlib import Path
from typing import Any

from synapse_channel.a2a_server import A2ABridge, A2ATaskStore, build_a2a_handler


class FakeAgent:
    """Small async agent stub for A2A bridge tests."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    async def chat(self, payload: str, *, target: str = "all") -> None:
        """Record one chat call."""
        self.messages.append((target, payload))


class HandlerHarness:
    """Instantiate one stdlib request handler without binding a socket."""

    def __init__(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        bridge = A2ABridge(
            agent=FakeAgent(),
            agent_card={
                "name": "SYNAPSE CHANNEL",
                "description": "bridge",
                "supportedInterfaces": [
                    {
                        "url": "https://example.test/a2a/v1",
                        "protocolBinding": "HTTP+JSON",
                        "protocolVersion": "1.0",
                    }
                ],
                "version": "0.0",
                "capabilities": {
                    "streaming": False,
                    "pushNotifications": False,
                    "extendedAgentCard": False,
                },
                "defaultInputModes": ["text/plain", "application/json"],
                "defaultOutputModes": ["text/plain", "application/json"],
                "skills": [],
            },
            target="WORKER",
            store=A2ATaskStore(),
        )
        handler_type = build_a2a_handler(bridge)
        payload = b"" if body is None else json.dumps(body).encode("utf-8")
        self.handler: Any = handler_type.__new__(handler_type)
        self.handler.command = method
        self.handler.path = path
        self.handler.request_version = "HTTP/1.1"
        self.handler.requestline = f"{method} {path} HTTP/1.1"
        handler: Any = self.handler
        handler.headers = {"Content-Length": str(len(payload)), **(headers or {})}
        self.handler.rfile = BytesIO(payload)
        self.handler.wfile = BytesIO()
        self.handler.close_connection = False
        self.handler.request = object()
        self.handler.client_address = ("127.0.0.1", 1)
        handler.server = object()
        self.handler.responses = type(self.handler).responses

    def run(self) -> tuple[int, dict[str, Any]]:
        """Run the handler method and return HTTP status plus decoded body."""
        if self.handler.command == "GET":
            self.handler.do_GET()
        elif self.handler.command == "POST":
            self.handler.do_POST()
        elif self.handler.command == "DELETE":
            self.handler.do_DELETE()
        else:
            raise AssertionError(self.handler.command)
        raw = self.handler.wfile.getvalue()
        header_blob, body = raw.split(b"\r\n\r\n", 1)
        status = int(header_blob.split(b" ", 2)[1])
        return status, json.loads(body.decode("utf-8"))

    def run_sse(self) -> tuple[int, dict[str, Any]]:
        """Run the POST handler and return HTTP status plus the first SSE data body."""
        if self.handler.command != "POST":
            raise AssertionError(self.handler.command)
        self.handler.do_POST()
        raw = self.handler.wfile.getvalue()
        header_blob, body = raw.split(b"\r\n\r\n", 1)
        status = int(header_blob.split(b" ", 2)[1])
        assert b"Content-Type: text/event-stream" in header_blob
        line = next(part for part in body.splitlines() if part.startswith(b"data: "))
        return status, json.loads(line.removeprefix(b"data: ").decode("utf-8"))


def test_task_store_persists_tasks_and_push_configs(tmp_path: Path) -> None:
    state_file = tmp_path / "a2a-state.json"
    first_store = A2ATaskStore(storage_path=state_file)
    first_store.put(
        {
            "id": "task-a",
            "contextId": "ctx",
            "status": {"state": "TASK_STATE_COMPLETED"},
            "history": [],
        }
    )
    first_store.put_push_config(
        "task-a",
        {"id": "cfg-a", "webhookUrl": "https://example.test/hook"},
    )

    second_store = A2ATaskStore(storage_path=state_file)

    assert second_store.get("task-a") is not None
    assert second_store.get_push_config("task-a", "cfg-a") is not None


def test_well_known_agent_card_endpoint_returns_card() -> None:
    status, body = HandlerHarness("GET", "/.well-known/agent-card.json").run()

    assert status == HTTPStatus.OK
    assert body["name"] == "SYNAPSE CHANNEL"
    assert body["supportedInterfaces"][0]["protocolBinding"] == "HTTP+JSON"


def test_bearer_auth_protects_extended_card_and_message_routes() -> None:
    bridge = A2ABridge(
        agent=FakeAgent(),
        agent_card={"name": "SYNAPSE CHANNEL"},
        target="WORKER",
        store=A2ATaskStore(),
        auth_token="secret",
    )
    public = HandlerHarness("GET", "/.well-known/agent-card.json")
    public.handler.bridge = bridge
    extended = HandlerHarness("GET", "/extendedAgentCard")
    extended.handler.bridge = bridge
    authorized = HandlerHarness(
        "GET",
        "/extendedAgentCard",
        headers={"Authorization": "Bearer secret"},
    )
    authorized.handler.bridge = bridge

    public_status, _ = public.run()
    extended_status, extended_body = extended.run()
    authorized_status, authorized_body = authorized.run()

    assert public_status == HTTPStatus.OK
    assert extended_status == HTTPStatus.UNAUTHORIZED
    assert extended_body["title"] == "Unauthorized"
    assert authorized_status == HTTPStatus.OK
    assert authorized_body["name"] == "SYNAPSE CHANNEL"


def test_message_send_creates_completed_task_and_forwards_text_to_synapse() -> None:
    harness = HandlerHarness(
        "POST",
        "/message:send",
        body={
            "message": {
                "messageId": "m1",
                "role": "ROLE_USER",
                "parts": [{"text": "status please"}],
                "metadata": {"target": "SC-NEUROCORE"},
            }
        },
    )

    status, body = harness.run()

    assert status == HTTPStatus.OK
    assert body["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert body["task"]["history"][0]["messageId"] == "m1"
    assert harness.handler.bridge.agent.messages == [("SC-NEUROCORE", "status please")]


def test_message_send_renders_file_parts_for_synapse_forwarding() -> None:
    harness = HandlerHarness(
        "POST",
        "/message:send",
        body={
            "message": {
                "messageId": "m1",
                "role": "ROLE_USER",
                "parts": [
                    {
                        "file": {
                            "uri": "https://example.test/report.pdf",
                            "name": "report.pdf",
                            "mimeType": "application/pdf",
                        }
                    }
                ],
            }
        },
    )

    status, _ = harness.run()

    assert status == HTTPStatus.OK
    assert harness.handler.bridge.agent.messages == [
        (
            "WORKER",
            "[file: report.pdf; application/pdf; https://example.test/report.pdf]",
        )
    ]


def test_message_stream_sends_sse_task_event_and_forwards_to_synapse() -> None:
    harness = HandlerHarness(
        "POST",
        "/message:stream",
        body={
            "message": {
                "messageId": "m1",
                "role": "ROLE_USER",
                "parts": [{"text": "stream status"}],
            }
        },
    )

    status, body = harness.run_sse()

    assert status == HTTPStatus.OK
    assert body["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert body["task"]["history"][0]["messageId"] == "m1"
    assert harness.handler.bridge.agent.messages == [("WORKER", "stream status")]


def test_subscribe_to_completed_task_returns_terminal_state_problem() -> None:
    bridge = A2ABridge(agent=FakeAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
    task = bridge.create_completed_task(
        {
            "messageId": "m1",
            "role": "ROLE_USER",
            "parts": [{"text": "hello"}],
        },
        target="WORKER",
    )
    harness = HandlerHarness("POST", f"/tasks/{task['id']}:subscribe")
    harness.handler.bridge = bridge

    status, body = harness.run()

    assert status == HTTPStatus.CONFLICT
    assert body["title"] == "Task is terminal"


def test_task_list_get_and_cancel_routes_use_store() -> None:
    bridge = A2ABridge(agent=FakeAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
    task = bridge.create_completed_task(
        {
            "messageId": "m1",
            "role": "ROLE_USER",
            "parts": [{"text": "hello"}],
        },
        target="WORKER",
    )

    tasks = bridge.list_tasks()
    assert tasks["tasks"][0]["id"] == task["id"]

    fetched = bridge.get_task(task["id"])
    assert fetched is not None
    assert fetched["id"] == task["id"]

    canceled = bridge.cancel_task(task["id"])
    assert canceled is not None
    assert canceled["status"]["state"] == "TASK_STATE_CANCELED"


def test_push_notification_config_lifecycle_routes_use_store() -> None:
    bridge = A2ABridge(agent=FakeAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
    task = bridge.create_completed_task(
        {
            "messageId": "m1",
            "role": "ROLE_USER",
            "parts": [{"text": "hello"}],
        },
        target="WORKER",
    )
    harness = HandlerHarness(
        "POST",
        f"/tasks/{task['id']}/pushNotificationConfigs",
        body={
            "pushNotificationConfig": {
                "webhookUrl": "https://example.test/hook",
                "authentication": {"scheme": "Bearer", "credentials": "token"},
            }
        },
    )
    harness.handler.bridge = bridge

    status, created = harness.run()

    assert status == HTTPStatus.OK
    config_id = created["id"]
    assert created["taskId"] == task["id"]
    assert created["webhookUrl"] == "https://example.test/hook"

    list_harness = HandlerHarness("GET", f"/tasks/{task['id']}/pushNotificationConfigs")
    list_harness.handler.bridge = bridge
    list_status, listed = list_harness.run()
    assert list_status == HTTPStatus.OK
    assert listed["pushNotificationConfigs"][0]["id"] == config_id

    get_harness = HandlerHarness(
        "GET",
        f"/tasks/{task['id']}/pushNotificationConfigs/{config_id}",
    )
    get_harness.handler.bridge = bridge
    get_status, fetched = get_harness.run()
    assert get_status == HTTPStatus.OK
    assert fetched["id"] == config_id

    delete_harness = HandlerHarness(
        "DELETE",
        f"/tasks/{task['id']}/pushNotificationConfigs/{config_id}",
    )
    delete_harness.handler.bridge = bridge
    delete_status, deleted = delete_harness.run()
    assert delete_status == HTTPStatus.OK
    assert deleted["deleted"] is True


def test_send_message_stores_push_notification_config_from_request() -> None:
    bridge = A2ABridge(agent=FakeAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())

    response = bridge.send_message(
        {
            "message": {
                "messageId": "m1",
                "role": "ROLE_USER",
                "parts": [{"text": "hello"}],
            },
            "configuration": {
                "taskPushNotificationConfig": {
                    "pushNotificationConfig": {"webhookUrl": "https://example.test/hook"}
                }
            },
        }
    )

    task_id = response["task"]["id"]
    configs = bridge.list_push_notification_configs(task_id)
    assert configs["pushNotificationConfigs"][0]["webhookUrl"] == "https://example.test/hook"


def test_send_message_delivers_push_notification_to_configured_webhook() -> None:
    deliveries: list[dict[str, Any]] = []
    bridge = A2ABridge(
        agent=FakeAgent(),
        agent_card={},
        target="WORKER",
        store=A2ATaskStore(),
        push_deliverer=deliveries.append,
    )

    response = bridge.send_message(
        {
            "message": {
                "messageId": "m1",
                "role": "ROLE_USER",
                "parts": [{"text": "hello"}],
            },
            "configuration": {
                "taskPushNotificationConfig": {
                    "pushNotificationConfig": {
                        "webhookUrl": "https://example.test/hook",
                        "authentication": {
                            "scheme": "Bearer",
                            "credentials": "push-token",
                        },
                    }
                }
            },
        }
    )

    assert deliveries == [
        {
            "url": "https://example.test/hook",
            "headers": {"Authorization": "Bearer push-token"},
            "payload": {"task": response["task"]},
        }
    ]


def test_cancel_task_delivers_push_notification_to_stored_configs() -> None:
    deliveries: list[dict[str, Any]] = []
    bridge = A2ABridge(
        agent=FakeAgent(),
        agent_card={},
        target="WORKER",
        store=A2ATaskStore(),
        push_deliverer=deliveries.append,
    )
    task = bridge.create_completed_task(
        {
            "messageId": "m1",
            "role": "ROLE_USER",
            "parts": [{"text": "hello"}],
        },
        target="WORKER",
    )
    bridge.create_push_notification_config(
        task["id"],
        {
            "webhookUrl": "https://example.test/hook",
            "authentication": {"scheme": "Bearer", "credentials": "push-token"},
        },
    )

    canceled = bridge.cancel_task(task["id"])

    assert canceled is not None
    assert deliveries == [
        {
            "url": "https://example.test/hook",
            "headers": {"Authorization": "Bearer push-token"},
            "payload": {"task": canceled},
        }
    ]


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
    assert body["result"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert harness.handler.bridge.agent.messages == [("WORKER", "json rpc")]


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


def test_task_list_supports_page_size_and_page_token() -> None:
    bridge = A2ABridge(agent=FakeAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
    for task_id in ("task-a", "task-b"):
        bridge.create_completed_task(
            {
                "taskId": task_id,
                "messageId": f"message-{task_id}",
                "role": "ROLE_USER",
                "parts": [{"text": task_id}],
            },
            target="WORKER",
        )

    first = HandlerHarness("GET", "/tasks?pageSize=1")
    first.handler.bridge = bridge
    first_status, first_body = first.run()
    second = HandlerHarness("GET", f"/tasks?pageSize=1&pageToken={first_body['nextPageToken']}")
    second.handler.bridge = bridge
    second_status, second_body = second.run()

    assert first_status == HTTPStatus.OK
    assert first_body["tasks"][0]["id"] == "task-a"
    assert first_body["nextPageToken"] == "1"
    assert second_status == HTTPStatus.OK
    assert second_body["tasks"][0]["id"] == "task-b"
    assert second_body["nextPageToken"] == ""


def test_get_task_supports_history_length_query() -> None:
    bridge = A2ABridge(agent=FakeAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
    task = bridge.create_completed_task(
        {
            "taskId": "task-a",
            "messageId": "message-a",
            "role": "ROLE_USER",
            "parts": [{"text": "task-a"}],
        },
        target="WORKER",
    )
    harness = HandlerHarness("GET", f"/tasks/{task['id']}?historyLength=0")
    harness.handler.bridge = bridge

    status, body = harness.run()

    assert status == HTTPStatus.OK
    assert body["id"] == "task-a"
    assert body["history"] == []


def test_bad_json_returns_a2a_problem_json() -> None:
    harness = HandlerHarness("POST", "/message:send")
    harness.handler.rfile = BytesIO(b"{")
    harness.handler.headers = {"Content-Length": "1"}

    status, body = harness.run()

    assert status == HTTPStatus.BAD_REQUEST
    assert body["title"] == "Invalid JSON"
