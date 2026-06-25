# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the A2A HTTP+JSON bridge

from __future__ import annotations

import json
import threading
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


def test_task_store_reports_corrupt_state_file(tmp_path: Path) -> None:
    state_file = tmp_path / "a2a-state.json"
    state_file.write_text("{not valid json", encoding="utf-8")

    try:
        A2ATaskStore(storage_path=state_file)
    except ValueError as exc:
        assert "Invalid A2A state file" in str(exc)
        assert str(state_file) in str(exc)
    else:
        raise AssertionError("corrupt A2A state file was accepted")


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


def test_message_send_rejects_explicit_non_json_content_type() -> None:
    harness = HandlerHarness(
        "POST",
        "/message:send",
        body={
            "message": {
                "messageId": "m1",
                "role": "ROLE_USER",
                "parts": [{"text": "status"}],
            }
        },
        headers={"Content-Type": "text/plain"},
    )

    status, body = harness.run()

    assert status == HTTPStatus.UNSUPPORTED_MEDIA_TYPE
    assert body["title"] == "Unsupported Media Type"


def test_message_send_accepts_a2a_json_content_type() -> None:
    harness = HandlerHarness(
        "POST",
        "/message:send",
        body={
            "message": {
                "messageId": "m1",
                "role": "ROLE_USER",
                "parts": [{"text": "status"}],
            }
        },
        headers={"Content-Type": "application/a2a+json; charset=utf-8"},
    )

    status, body = harness.run()

    assert status == HTTPStatus.OK
    assert body["task"]["status"]["state"] == "TASK_STATE_WORKING"


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
    assert body["task"]["status"]["state"] == "TASK_STATE_WORKING"
    assert body["task"]["history"][0]["messageId"] == "m1"
    sent = harness.handler.bridge.agent.messages[0]
    assert sent[0] == "SC-NEUROCORE"
    assert sent[1].startswith("status please")
    assert "[A2A-TASK:" in sent[1]


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
    sent = harness.handler.bridge.agent.messages[0]
    assert sent[0] == "WORKER"
    assert sent[1].startswith("[file: report.pdf; application/pdf; https://example.test/report.pdf]")
    assert "[A2A-TASK:" in sent[1]


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
    assert body["task"]["status"]["state"] == "TASK_STATE_WORKING"
    assert body["task"]["history"][0]["messageId"] == "m1"
    sent = harness.handler.bridge.agent.messages[0]
    assert sent[0] == "WORKER"
    assert sent[1].startswith("stream status")
    assert "[A2A-TASK:" in sent[1]


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
    task["status"]["state"] = "TASK_STATE_COMPLETED"
    bridge.store.put(task)
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
    bridge = A2ABridge(agent=FakeAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
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


def test_handle_synapse_frame_correlates_reply_and_completes_task() -> None:
    bridge = A2ABridge(agent=FakeAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
    task = bridge.create_completed_task(
        {
            "messageId": "m1",
            "role": "ROLE_USER",
            "parts": [{"text": "compute the answer"}],
        },
        target="WORKER",
    )
    assert task["status"]["state"] == "TASK_STATE_WORKING"
    reply_frame = {
        "type": "chat",
        "sender": "WORKER",
        "payload": (
            "the answer is 42\n[A2A-TASK:"
            + task["id"]
            + " contextId="
            + task["contextId"]
            + "]"
        ),
    }
    bridge.handle_synapse_frame(reply_frame)
    updated = bridge.store.get(task["id"])
    assert updated is not None
    assert updated["status"]["state"] == "TASK_STATE_COMPLETED"
    assert len(updated.get("history", [])) >= 2
    assert any("42" in str(h) for h in updated.get("history", []))


def test_handle_synapse_frame_rejects_marker_from_wrong_sender() -> None:
    bridge = A2ABridge(agent=FakeAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
    task = bridge.create_completed_task(
        {
            "messageId": "m1",
            "role": "ROLE_USER",
            "parts": [{"text": "compute the answer"}],
        },
        target="WORKER",
    )

    bridge.handle_synapse_frame(
        {
            "type": "chat",
            "sender": "OTHER",
            "payload": f"wrong actor\n[A2A-TASK:{task['id']} contextId={task['contextId']}]",
        }
    )

    updated = bridge.store.get(task["id"])
    assert updated is not None
    assert updated["status"]["state"] == "TASK_STATE_WORKING"


def test_handle_synapse_frame_strips_correlation_marker_from_reply() -> None:
    bridge = A2ABridge(agent=FakeAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
    task = bridge.create_completed_task(
        {
            "messageId": "m1",
            "role": "ROLE_USER",
            "parts": [{"text": "compute the answer"}],
        },
        target="WORKER",
    )

    bridge.handle_synapse_frame(
        {
            "type": "chat",
            "sender": "WORKER",
            "payload": f"answer body\n[A2A-TASK:{task['id']} contextId={task['contextId']}]",
        }
    )

    updated = bridge.store.get(task["id"])
    assert updated is not None
    status_message = updated["status"]["message"]
    assert status_message["parts"][0]["text"] == "answer body"


def test_fallback_correlation_preserves_fifo_tasks_for_same_sender() -> None:
    bridge = A2ABridge(agent=FakeAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
    first = bridge.create_completed_task(
        {
            "taskId": "task-a",
            "messageId": "m1",
            "role": "ROLE_USER",
            "parts": [{"text": "first"}],
        },
        target="WORKER",
    )
    second = bridge.create_completed_task(
        {
            "taskId": "task-b",
            "messageId": "m2",
            "role": "ROLE_USER",
            "parts": [{"text": "second"}],
        },
        target="WORKER",
    )

    bridge.handle_synapse_frame({"type": "chat", "sender": "WORKER", "payload": "first reply"})

    first_updated = bridge.store.get(first["id"])
    second_updated = bridge.store.get(second["id"])
    assert first_updated is not None
    assert second_updated is not None
    assert first_updated["status"]["state"] == "TASK_STATE_COMPLETED"
    assert second_updated["status"]["state"] == "TASK_STATE_WORKING"


def test_completion_delivers_push_notification_to_stored_config() -> None:
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
    bridge.create_push_notification_config(task["id"], {"webhookUrl": "https://example.test/hook"})

    bridge.handle_synapse_frame(
        {
            "type": "chat",
            "sender": "WORKER",
            "payload": f"done\n[A2A-TASK:{task['id']} contextId={task['contextId']}]",
        }
    )

    assert len(deliveries) == 1
    assert deliveries[0]["payload"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"


def test_late_correlated_reply_does_not_complete_canceled_task() -> None:
    bridge = A2ABridge(agent=FakeAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
    task = bridge.create_completed_task(
        {
            "messageId": "m1",
            "role": "ROLE_USER",
            "parts": [{"text": "hello"}],
        },
        target="WORKER",
    )
    canceled = bridge.cancel_task(task["id"])
    assert canceled is not None

    bridge.handle_synapse_frame(
        {
            "type": "chat",
            "sender": "WORKER",
            "payload": f"late\n[A2A-TASK:{task['id']} contextId={task['contextId']}]",
        }
    )

    updated = bridge.store.get(task["id"])
    assert updated is not None
    assert updated["status"]["state"] == "TASK_STATE_CANCELED"
    assert updated.get("artifacts") == []


def test_duplicate_correlated_reply_does_not_append_second_completion() -> None:
    bridge = A2ABridge(agent=FakeAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
    task = bridge.create_completed_task(
        {
            "messageId": "m1",
            "role": "ROLE_USER",
            "parts": [{"text": "hello"}],
        },
        target="WORKER",
    )
    frame = {
        "type": "chat",
        "sender": "WORKER",
        "payload": f"done\n[A2A-TASK:{task['id']} contextId={task['contextId']}]",
    }

    bridge.handle_synapse_frame(frame)
    completed = bridge.store.get(task["id"])
    assert completed is not None
    history_len = len(completed.get("history", []))
    artifact_len = len(completed.get("artifacts", []))
    bridge.handle_synapse_frame(frame)

    updated = bridge.store.get(task["id"])
    assert updated is not None
    assert updated["status"]["state"] == "TASK_STATE_COMPLETED"
    assert len(updated.get("history", [])) == history_len
    assert len(updated.get("artifacts", [])) == artifact_len


def test_push_notification_config_rejects_non_http_webhook_url() -> None:
    bridge = A2ABridge(agent=FakeAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
    task = bridge.create_completed_task(
        {
            "messageId": "m1",
            "role": "ROLE_USER",
            "parts": [{"text": "hello"}],
        },
        target="WORKER",
    )

    try:
        bridge.create_push_notification_config(task["id"], {"webhookUrl": "file:///tmp/hook"})
    except ValueError as exc:
        assert str(exc) == "pushNotificationConfig.webhookUrl must use http or https"
    else:
        raise AssertionError("non-HTTP webhook URL was accepted")


def test_push_notification_config_rejects_missing_webhook_host() -> None:
    bridge = A2ABridge(agent=FakeAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
    task = bridge.create_completed_task(
        {
            "messageId": "m1",
            "role": "ROLE_USER",
            "parts": [{"text": "hello"}],
        },
        target="WORKER",
    )

    try:
        bridge.create_push_notification_config(task["id"], {"webhookUrl": "https:///hook"})
    except ValueError as exc:
        assert str(exc) == "pushNotificationConfig.webhookUrl must include a host"
    else:
        raise AssertionError("hostless webhook URL was accepted")


def test_timeout_marks_open_task_failed() -> None:
    bridge = A2ABridge(
        agent=FakeAgent(),
        agent_card={},
        target="WORKER",
        store=A2ATaskStore(),
        task_timeout_seconds=1.0,
    )
    task = bridge.create_completed_task(
        {
            "messageId": "m1",
            "role": "ROLE_USER",
            "parts": [{"text": "hello"}],
        },
        target="WORKER",
    )
    task["metadata"]["updatedAt"] = 10.0
    bridge.store.put(task)

    failed = bridge.expire_timed_out_tasks(now=12.0)

    assert len(failed) == 1
    assert failed[0]["status"]["state"] == "TASK_STATE_FAILED"


def test_state_file_recovery_fails_stale_working_tasks(tmp_path: Path) -> None:
    state_file = tmp_path / "a2a-state.json"
    store = A2ATaskStore(storage_path=state_file)
    store.put(
        {
            "id": "task-a",
            "contextId": "ctx",
            "status": {"state": "TASK_STATE_WORKING"},
            "history": [],
            "artifacts": [],
            "metadata": {"synapseTarget": "WORKER", "updatedAt": 1.0},
        }
    )
    loaded = A2ATaskStore(storage_path=state_file)

    A2ABridge(
        agent=FakeAgent(),
        agent_card={},
        target="WORKER",
        store=loaded,
        task_timeout_seconds=1.0,
    )

    recovered = loaded.get("task-a")
    assert recovered is not None
    assert recovered["status"]["state"] == "TASK_STATE_FAILED"


def test_subscription_queue_receives_terminal_update() -> None:
    bridge = A2ABridge(agent=FakeAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
    task = bridge.create_completed_task(
        {
            "messageId": "m1",
            "role": "ROLE_USER",
            "parts": [{"text": "hello"}],
        },
        target="WORKER",
    )
    events: list[dict[str, Any]] = []

    def collect_events() -> None:
        subscribed = bridge.subscribe_task_events(task["id"], wait_seconds=1.0) or []
        events.extend(subscribed)

    worker = threading.Thread(
        target=collect_events,
    )
    worker.start()
    bridge.handle_synapse_frame(
        {
            "type": "chat",
            "sender": "WORKER",
            "payload": f"done\n[A2A-TASK:{task['id']} contextId={task['contextId']}]",
        }
    )
    worker.join(timeout=2.0)

    assert [event["task"]["status"]["state"] for event in events] == [
        "TASK_STATE_WORKING",
        "TASK_STATE_COMPLETED",
    ]


def test_bad_json_returns_a2a_problem_json() -> None:
    harness = HandlerHarness("POST", "/message:send")
    harness.handler.rfile = BytesIO(b"{")
    harness.handler.headers = {"Content-Length": "1"}

    status, body = harness.run()

    assert status == HTTPStatus.BAD_REQUEST
    assert body["title"] == "Invalid JSON"
