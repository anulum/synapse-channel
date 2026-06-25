# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — edge-behaviour tests for the A2A HTTP bridge core

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.error import URLError

import pytest

from a2a_server_helpers import RecordingAgent
from hub_e2e_helpers import _free_port
from synapse_channel.a2a_server import (
    A2ABridge,
    SynapseAgentRuntime,
    _http_push_deliverer,
    _push_config_path,
    make_a2a_http_server,
)
from synapse_channel.a2a_store import A2ATaskStore


def _bridge(**kwargs: Any) -> A2ABridge:
    return A2ABridge(
        agent=kwargs.pop("agent", RecordingAgent()),
        agent_card={"name": "SYNAPSE CHANNEL"},
        target=kwargs.pop("target", "WORKER"),
        store=kwargs.pop("store", A2ATaskStore()),
        **kwargs,
    )


def _message(task_id: str = "task-a", *, parts: list[Any] | None = None) -> dict[str, Any]:
    return {
        "taskId": task_id,
        "messageId": f"message-{task_id}",
        "role": "ROLE_USER",
        "parts": parts if parts is not None else [{"text": "work"}],
    }


def test_http_push_deliverer_posts_json_to_real_http_server() -> None:
    received: list[dict[str, Any]] = []

    class PushHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers["Content-Length"])
            received.append(
                {
                    "content_type": self.headers["Content-Type"],
                    "auth": self.headers["Authorization"],
                    "body": json.loads(self.rfile.read(length).decode("utf-8")),
                }
            )
            self.send_response(204)
            self.end_headers()

        def log_message(self, _format: str, *_args: Any) -> None:
            return None

    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), PushHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        _http_push_deliverer(
            {
                "url": f"http://127.0.0.1:{port}/hook",
                "headers": {"Authorization": "Bearer token"},
                "payload": {"task": {"id": "task-a"}},
            }
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert received == [
        {
            "content_type": "application/a2a+json",
            "auth": "Bearer token",
            "body": {"task": {"id": "task-a"}},
        }
    ]


def test_make_a2a_http_server_serves_agent_card_over_real_http() -> None:
    import http.client

    bridge = _bridge()
    port = _free_port()
    server = make_a2a_http_server(bridge=bridge, host="127.0.0.1", port=port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2.0)
        try:
            connection.request("GET", "/.well-known/agent-card.json")
            response = connection.getresponse()
            body = json.loads(response.read().decode("utf-8"))
        finally:
            connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert response.status == 200
    assert body["name"] == "SYNAPSE CHANNEL"


def test_bridge_submitter_path_runs_agent_chat() -> None:
    submitted: list[str] = []

    def submit(coro: Any) -> Any:
        submitted.append(type(coro).__name__)
        coro.close()
        return None

    bridge = _bridge(submit=submit)
    task = bridge.create_working_task(_message())

    assert task["status"]["state"] == "TASK_STATE_WORKING"
    assert submitted == ["coroutine"]


def test_synapse_agent_runtime_start_run_and_stop() -> None:
    class RuntimeAgent:
        def __init__(self) -> None:
            self.running = True
            self.connected = False

        async def connect(self) -> None:
            self.connected = True

        async def wait_until_ready(self, timeout: float) -> bool:
            return self.connected

    async def value() -> str:
        return "ok"

    agent = RuntimeAgent()
    runtime = SynapseAgentRuntime(agent)  # type: ignore[arg-type] # test adapter implements runtime protocol
    try:
        assert runtime.start(ready_timeout=0.1) is True
        assert runtime.run(value()) == "ok"
    finally:
        runtime.stop()
        runtime._thread.join(timeout=2.0)

    assert agent.running is False


def test_push_config_path_rejects_empty_task_id() -> None:
    assert _push_config_path("/tasks//pushNotificationConfigs") is None


def test_message_text_renders_supported_part_shapes_and_skips_empty_parts() -> None:
    agent = RecordingAgent()
    bridge = _bridge(agent=agent)
    parts = [
        "bad",
        {"data": {"b": 2, "a": 1}},
        {"url": "https://example.test/item"},
        {"file": {}},
        {"raw": "secret"},
    ]

    bridge.create_working_task(_message(parts=parts))

    sent = agent.messages[0][1]
    assert '{"a": 1, "b": 2}' in sent
    assert "https://example.test/item" in sent
    assert "[raw omitted]" in sent


def test_message_without_rendered_text_still_creates_working_task_without_chat() -> None:
    agent = RecordingAgent()
    bridge = _bridge(agent=agent)

    task = bridge.create_working_task(_message(parts=[{}, "bad"]))

    assert task["status"]["state"] == "TASK_STATE_WORKING"
    assert agent.messages == []


def test_synapse_target_metadata_and_fallback_target_are_used() -> None:
    agent = RecordingAgent()
    bridge = _bridge(agent=agent)
    message = _message()
    message["metadata"] = {"synapseTarget": "SPECIALIST"}
    empty_target = _message("task-c")
    empty_target["metadata"] = {"target": ""}

    bridge.create_working_task(message)
    bridge.create_working_task(_message("task-b"), target="FALLBACK")
    bridge.create_working_task(empty_target)

    assert [target for target, _payload in agent.messages] == [
        "SPECIALIST",
        "FALLBACK",
        "WORKER",
    ]


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({}, "message must be an object"),
        (
            {"message": {"role": "ROLE_USER", "parts": [{"text": "x"}]}},
            "message.messageId is required",
        ),
        (
            {"message": {"messageId": "m1", "role": "ROLE_AGENT", "parts": [{"text": "x"}]}},
            "message.role must be ROLE_USER",
        ),
    ],
)
def test_send_message_validation_errors(payload: dict[str, Any], expected: str) -> None:
    bridge = _bridge()

    with pytest.raises(ValueError, match=expected):
        bridge.send_message(payload)


def test_request_push_config_ignores_missing_or_malformed_configuration() -> None:
    bridge = _bridge()
    task = bridge.create_working_task(_message())

    assert bridge._store_request_push_config({}, task_id=str(task["id"])) is None
    assert (
        bridge._store_request_push_config({"configuration": "bad"}, task_id=str(task["id"])) is None
    )
    assert (
        bridge._store_request_push_config(
            {"configuration": {"taskPushNotificationConfig": "bad"}},
            task_id=str(task["id"]),
        )
        is None
    )
    assert (
        bridge._store_request_push_config(
            {"configuration": {"taskPushNotificationConfig": {"pushNotificationConfig": "bad"}}},
            task_id=str(task["id"]),
        )
        is None
    )
    assert (
        bridge._store_request_push_config(
            {
                "configuration": {
                    "taskPushNotificationConfig": {
                        "pushNotificationConfig": {"webhookUrl": "https://example.test/hook"}
                    }
                }
            },
            task_id="missing",
        )
        is None
    )


def test_push_delivery_suppresses_network_errors_and_omits_incomplete_auth() -> None:
    deliveries: list[dict[str, Any]] = []

    def deliver(delivery: dict[str, Any]) -> None:
        deliveries.append(delivery)
        raise URLError("offline")

    bridge = _bridge(push_deliverer=deliver)
    task = bridge.create_working_task(_message())

    bridge._deliver_push_notification(
        task=task,
        config={
            "webhookUrl": "https://example.test/hook",
            "authentication": {"scheme": "Bearer", "credentials": ""},
        },
    )

    assert deliveries[0]["headers"] == {}


def test_status_update_handles_non_mapping_metadata() -> None:
    bridge = _bridge()
    task = bridge.create_working_task(_message())
    task["metadata"] = "bad"

    updated = bridge._set_task_status(task, state="TASK_STATE_COMPLETED")

    assert updated["status"] == {"state": "TASK_STATE_COMPLETED"}


def test_pending_and_sender_matching_edge_paths() -> None:
    bridge = _bridge()
    assert bridge._pending_task_for_sender("WORKER") is None
    bridge._pending_by_target["WORKER"] = ["missing"]
    assert bridge._pending_task_for_sender("WORKER") is None
    assert "WORKER" not in bridge._pending_by_target
    assert bridge._sender_matches_task({"metadata": "bad"}, "ANY") is True


def test_synapse_frame_ignores_unmatched_or_invalid_frames() -> None:
    bridge = _bridge()
    task = bridge.create_working_task(_message(), target="WORKER")

    frames = [
        {"type": "presence", "sender": "WORKER", "payload": "ignored"},
        {"type": "chat", "sender": "OTHER", "payload": "ignored"},
        {"type": "chat", "sender": "WORKER", "payload": "reply [A2A-TASK:missing contextId=x]"},
        {"type": "chat", "sender": "WORKER", "payload": f"reply [A2A-TASK:{task['id']}]"},
        {
            "type": "chat",
            "sender": "WORKER",
            "payload": f"reply [A2A-TASK:{task['id']} contextId=wrong]",
        },
    ]
    for frame in frames:
        bridge.handle_synapse_frame(frame)

    stored = bridge.store.get(str(task["id"]))
    assert stored is not None
    assert stored["status"]["state"] == "TASK_STATE_WORKING"

    bridge.handle_synapse_frame(
        {
            "type": "chat",
            "sender": "WORKER",
            "payload": f"done [A2A-TASK:{task['id']} contextId={task['contextId']}]",
        }
    )
    bridge.handle_synapse_frame(
        {
            "type": "chat",
            "sender": "WORKER",
            "payload": f"again [A2A-TASK:{task['id']} contextId={task['contextId']}]",
        }
    )
    completed = bridge.store.get(str(task["id"]))
    assert completed is not None
    assert completed["status"]["state"] == "TASK_STATE_COMPLETED"


def test_timeout_edge_paths() -> None:
    disabled = _bridge(task_timeout_seconds=0.0)
    assert disabled.expire_timed_out_tasks(now=999.0) == []

    bridge = _bridge(task_timeout_seconds=10.0)
    open_task = bridge.create_working_task(_message())
    open_task["metadata"] = "bad"
    bridge.store.put(open_task)
    fresh = bridge.create_working_task(_message("task-b"))
    fresh["metadata"]["updatedAt"] = 100.0
    bridge.store.put(fresh)

    failed = bridge.expire_timed_out_tasks(now=105.0)

    assert [task["id"] for task in failed] == ["task-a"]
    stored_fresh = bridge.store.get("task-b")
    assert stored_fresh is not None
    assert stored_fresh["status"]["state"] == "TASK_STATE_WORKING"


def test_get_subscribe_and_push_config_unknown_paths() -> None:
    bridge = _bridge()

    assert bridge.get_task("missing") is None
    assert bridge.subscribe_task("missing") is None
    assert bridge.subscribe_task_events("missing") is None
    assert (
        bridge.create_push_notification_config("missing", {"webhookUrl": "https://example.test"})
        is None
    )
    task = bridge.create_working_task(_message())
    with pytest.raises(ValueError, match="webhookUrl is required"):
        bridge.create_push_notification_config(str(task["id"]), {})


def test_get_task_trims_only_list_history() -> None:
    bridge = _bridge()
    task = bridge.create_working_task(_message())
    task["history"] = "bad"
    bridge.store.put(task)

    fetched = bridge.get_task(str(task["id"]), history_length=1)

    assert fetched is not None
    assert fetched["history"] == "bad"


def test_json_rpc_accepts_null_params_and_successful_push_config_set() -> None:
    bridge = _bridge()
    task = bridge.create_working_task(_message())

    null_params = bridge.handle_json_rpc(
        {"jsonrpc": "2.0", "id": "none", "method": "tasks/list", "params": None}
    )
    created = bridge.handle_json_rpc(
        {
            "jsonrpc": "2.0",
            "id": "set-push",
            "method": "tasks/pushNotificationConfig/set",
            "params": {
                "taskId": str(task["id"]),
                "pushNotificationConfig": {"webhookUrl": "https://example.test/hook-2"},
            },
        }
    )

    assert null_params["result"]["totalSize"] >= 1
    assert created["result"]["webhookUrl"] == "https://example.test/hook-2"


def test_json_rpc_success_and_error_methods() -> None:
    bridge = _bridge()
    task = bridge.create_working_task(_message())
    config = bridge.create_push_notification_config(
        str(task["id"]), {"webhookUrl": "https://example.test/hook"}
    )
    assert config is not None

    assert bridge.handle_json_rpc({"jsonrpc": "2.0", "id": "bad"})["error"]["code"] == -32600
    assert (
        bridge.handle_json_rpc(
            {"jsonrpc": "2.0", "id": "bad", "method": "tasks/list", "params": []}
        )["error"]["code"]
        == -32602
    )
    assert (
        bridge.handle_json_rpc(
            {
                "jsonrpc": "2.0",
                "id": "stream",
                "method": "message/stream",
                "params": {"message": _message("stream")},
            }
        )["result"]["task"]["id"]
        == "stream"
    )
    assert (
        bridge.handle_json_rpc(
            {
                "jsonrpc": "2.0",
                "id": "get",
                "method": "tasks/get",
                "params": {"taskId": str(task["id"]), "historyLength": "bad"},
            }
        )["result"]["id"]
        == task["id"]
    )
    assert (
        bridge.handle_json_rpc(
            {
                "jsonrpc": "2.0",
                "id": "get-missing",
                "method": "tasks/get",
                "params": {"taskId": "missing"},
            }
        )["error"]["message"]
        == "Unknown task: missing"
    )
    assert (
        bridge.handle_json_rpc(
            {
                "jsonrpc": "2.0",
                "id": "list",
                "method": "tasks/list",
                "params": {"status": "TASK_STATE_WORKING", "pageSize": "bad"},
            }
        )["result"]["totalSize"]
        >= 1
    )
    assert (
        bridge.handle_json_rpc(
            {
                "jsonrpc": "2.0",
                "id": "cancel",
                "method": "tasks/cancel",
                "params": {"taskId": str(task["id"])},
            }
        )["result"]["status"]["state"]
        == "TASK_STATE_CANCELED"
    )
    assert (
        bridge.handle_json_rpc(
            {
                "jsonrpc": "2.0",
                "id": "cancel-missing",
                "method": "tasks/cancel",
                "params": {"taskId": "missing"},
            }
        )["error"]["message"]
        == "Unknown task: missing"
    )
    assert (
        bridge.handle_json_rpc(
            {
                "jsonrpc": "2.0",
                "id": "set-missing-config",
                "method": "tasks/pushNotificationConfig/set",
                "params": {"taskId": str(task["id"])},
            }
        )["error"]["message"]
        == "pushNotificationConfig is required"
    )
    assert (
        bridge.handle_json_rpc(
            {
                "jsonrpc": "2.0",
                "id": "set-missing-task",
                "method": "tasks/pushNotificationConfig/set",
                "params": {
                    "taskId": "missing",
                    "pushNotificationConfig": {"webhookUrl": "https://example.test/hook"},
                },
            }
        )["error"]["message"]
        == "Unknown task: missing"
    )
    assert bridge.handle_json_rpc(
        {
            "jsonrpc": "2.0",
            "id": "list-push",
            "method": "tasks/pushNotificationConfig/list",
            "params": {"taskId": str(task["id"])},
        }
    )["result"] == [config]
    assert (
        bridge.handle_json_rpc(
            {
                "jsonrpc": "2.0",
                "id": "get-push",
                "method": "tasks/pushNotificationConfig/get",
                "params": {"taskId": str(task["id"]), "configId": str(config["id"])},
            }
        )["result"]
        == config
    )
    assert (
        bridge.handle_json_rpc(
            {
                "jsonrpc": "2.0",
                "id": "get-push-missing",
                "method": "tasks/pushNotificationConfig/get",
                "params": {"taskId": str(task["id"]), "configId": "missing"},
            }
        )["error"]["message"]
        == "Unknown push notification config: missing"
    )
    assert bridge.handle_json_rpc(
        {
            "jsonrpc": "2.0",
            "id": "delete-push",
            "method": "tasks/pushNotificationConfig/delete",
            "params": {"taskId": str(task["id"]), "configId": str(config["id"])},
        }
    )["result"] == {"deleted": True}
    assert bridge.handle_json_rpc(
        {"jsonrpc": "2.0", "id": "card", "method": "agent/getAuthenticatedExtendedCard"}
    )["result"] == {"name": "SYNAPSE CHANNEL"}
    assert (
        bridge.handle_json_rpc({"jsonrpc": "2.0", "id": "nope", "method": "unknown"})["error"][
            "code"
        ]
        == -32601
    )
