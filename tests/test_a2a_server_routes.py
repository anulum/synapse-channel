# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the A2A HTTP+JSON bridge

from __future__ import annotations

from http import HTTPStatus
from pathlib import Path

import pytest

from a2a_server_helpers import HandlerHarness, RecordingAgent
from synapse_channel.a2a_errors import A2AStoreError
from synapse_channel.a2a_server import A2ABridge
from synapse_channel.a2a_store import A2ATaskStore


def test_well_known_agent_card_endpoint_returns_card() -> None:
    status, body = HandlerHarness("GET", "/.well-known/agent-card.json").run()

    assert status == HTTPStatus.OK
    assert body["name"] == "SYNAPSE CHANNEL"
    assert body["supportedInterfaces"][0]["protocolBinding"] == "HTTP+JSON"


def test_bearer_auth_protects_extended_card_and_message_routes() -> None:
    bridge = A2ABridge(
        agent=RecordingAgent(),
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
    assert sent[1] == "status please"
    assert "[A2A-TASK:" not in sent[1]
    assert harness.handler.bridge.agent.message_metadata[0] == {
        "a2aTaskId": body["task"]["id"],
        "a2aContextId": body["task"]["contextId"],
    }


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
    assert sent[1] == "[file: report.pdf; application/pdf; https://example.test/report.pdf]"
    assert "[A2A-TASK:" not in sent[1]
    assert harness.handler.bridge.agent.message_metadata[0]["a2aTaskId"]
    assert harness.handler.bridge.agent.message_metadata[0]["a2aContextId"]


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
    assert sent[1] == "stream status"
    assert "[A2A-TASK:" not in sent[1]
    assert harness.handler.bridge.agent.message_metadata[0] == {
        "a2aTaskId": body["task"]["id"],
        "a2aContextId": body["task"]["contextId"],
    }


def test_subscribe_to_completed_task_returns_terminal_state_problem() -> None:
    bridge = A2ABridge(agent=RecordingAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
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
    bridge = A2ABridge(agent=RecordingAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
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


def test_task_list_supports_page_size_and_page_token() -> None:
    bridge = A2ABridge(agent=RecordingAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
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
    bridge = A2ABridge(agent=RecordingAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
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


def test_unknown_get_routes_return_problem_json() -> None:
    status, body = HandlerHarness("GET", "/missing").run()

    assert status == HTTPStatus.NOT_FOUND
    assert body["title"] == "Not Found"


def test_get_unknown_task_and_colon_task_return_not_found() -> None:
    bridge = A2ABridge(agent=RecordingAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
    unknown = HandlerHarness("GET", "/tasks/missing")
    unknown.handler.bridge = bridge
    colon = HandlerHarness("GET", "/tasks/task-a:cancel")
    colon.handler.bridge = bridge

    unknown_status, unknown_body = unknown.run()
    colon_status, colon_body = colon.run()

    assert unknown_status == HTTPStatus.NOT_FOUND
    assert unknown_body["detail"] == "Unknown task: missing"
    assert colon_status == HTTPStatus.NOT_FOUND
    assert colon_body["title"] == "Not Found"


def test_push_config_get_unknown_task_and_config_return_not_found() -> None:
    bridge = A2ABridge(agent=RecordingAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
    task = bridge.create_completed_task(
        {"messageId": "m1", "role": "ROLE_USER", "parts": [{"text": "hello"}]},
        target="WORKER",
    )
    unknown_task = HandlerHarness("GET", "/tasks/missing/pushNotificationConfigs")
    unknown_task.handler.bridge = bridge
    unknown_config = HandlerHarness("GET", f"/tasks/{task['id']}/pushNotificationConfigs/missing")
    unknown_config.handler.bridge = bridge

    task_status, task_body = unknown_task.run()
    config_status, config_body = unknown_config.run()

    assert task_status == HTTPStatus.NOT_FOUND
    assert task_body["detail"] == "Unknown task: missing"
    assert config_status == HTTPStatus.NOT_FOUND
    assert config_body["detail"] == "Unknown push notification config: missing"


def test_post_push_config_error_routes_return_problem_json() -> None:
    bridge = A2ABridge(agent=RecordingAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
    task = bridge.create_completed_task(
        {"messageId": "m1", "role": "ROLE_USER", "parts": [{"text": "hello"}]},
        target="WORKER",
    )
    with_config_id = HandlerHarness(
        "POST", f"/tasks/{task['id']}/pushNotificationConfigs/cfg-a", body={}
    )
    with_config_id.handler.bridge = bridge
    invalid_config = HandlerHarness(
        "POST",
        f"/tasks/{task['id']}/pushNotificationConfigs",
        body={"pushNotificationConfig": "bad"},
    )
    invalid_config.handler.bridge = bridge
    missing_webhook = HandlerHarness(
        "POST", f"/tasks/{task['id']}/pushNotificationConfigs", body={}
    )
    missing_webhook.handler.bridge = bridge
    unknown_task = HandlerHarness(
        "POST",
        "/tasks/missing/pushNotificationConfigs",
        body={"webhookUrl": "https://example.test/hook"},
    )
    unknown_task.handler.bridge = bridge

    id_status, id_body = with_config_id.run()
    invalid_status, invalid_body = invalid_config.run()
    webhook_status, webhook_body = missing_webhook.run()
    unknown_status, unknown_body = unknown_task.run()

    assert id_status == HTTPStatus.NOT_FOUND
    assert id_body["title"] == "Not Found"
    assert invalid_status == HTTPStatus.BAD_REQUEST
    assert invalid_body["title"] == "Invalid push notification config"
    assert webhook_status == HTTPStatus.BAD_REQUEST
    assert webhook_body["detail"] == "pushNotificationConfig.webhookUrl is required"
    assert unknown_status == HTTPStatus.NOT_FOUND
    assert unknown_body["detail"] == "Unknown task: missing"


def test_post_push_config_maps_typed_quota_to_too_many_requests() -> None:
    store = A2ATaskStore(max_push_configs_per_task=1)
    bridge = A2ABridge(agent=RecordingAgent(), agent_card={}, target="WORKER", store=store)
    task = bridge.create_completed_task(
        {"messageId": "m1", "role": "ROLE_USER", "parts": [{"text": "hello"}]},
        target="WORKER",
    )
    bridge.create_push_notification_config(
        str(task["id"]),
        {"id": "cfg-a", "webhookUrl": "https://example.test/a"},
    )
    harness = HandlerHarness(
        "POST",
        f"/tasks/{task['id']}/pushNotificationConfigs",
        body={"id": "cfg-b", "webhookUrl": "https://example.test/b"},
    )
    harness.handler.bridge = bridge

    status, body = harness.run()

    assert status == HTTPStatus.TOO_MANY_REQUESTS
    assert body["title"] == "Too Many Requests"
    assert body["detail"] == "pushNotificationConfig limit exceeded"


def test_stream_invalid_message_returns_problem_json() -> None:
    status, body = HandlerHarness("POST", "/message:stream", body={"message": "bad"}).run()

    assert status == HTTPStatus.BAD_REQUEST
    assert body["title"] == "Invalid A2A message"
    assert body["detail"] == "message must be an object"


def test_message_send_redacts_typed_internal_failure_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = A2ABridge(agent=RecordingAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())

    def fail_send(_payload: object) -> object:
        raise A2AStoreError("Invalid A2A state file: /private/hub.json")

    monkeypatch.setattr(bridge, "send_message", fail_send)
    harness = HandlerHarness("POST", "/message:send", body={})
    harness.handler.bridge = bridge

    status, body = harness.run()

    assert status == HTTPStatus.INTERNAL_SERVER_ERROR
    assert body["title"] == "Internal Server Error"
    assert "detail" not in body
    assert "/private/hub.json" not in str(body)


def test_rpc_accepts_malformed_content_length_as_empty_body() -> None:
    status, body = HandlerHarness(
        "POST", "/rpc", body=b"{}", headers={"Content-Length": "bad"}
    ).run()

    assert status == HTTPStatus.OK
    assert body["error"]["message"] == "Invalid Request"


def test_non_object_json_body_returns_problem_json() -> None:
    status, body = HandlerHarness("POST", "/message:send", body=b"[]").run()

    assert status == HTTPStatus.BAD_REQUEST
    assert body["title"] == "Invalid request body"


def test_cancel_and_subscribe_unknown_tasks_return_not_found() -> None:
    cancel_status, cancel_body = HandlerHarness("POST", "/tasks/missing:cancel").run()
    subscribe_status, subscribe_body = HandlerHarness("POST", "/tasks/missing:subscribe").run()

    assert cancel_status == HTTPStatus.NOT_FOUND
    assert cancel_body["detail"] == "Unknown task: missing"
    assert subscribe_status == HTTPStatus.NOT_FOUND
    assert subscribe_body["detail"] == "Unknown task: missing"


def test_subscribe_open_task_returns_sse_snapshot() -> None:
    bridge = A2ABridge(agent=RecordingAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
    task = bridge.create_completed_task(
        {"messageId": "m1", "role": "ROLE_USER", "parts": [{"text": "hello"}]},
        target="WORKER",
    )
    harness = HandlerHarness("POST", f"/tasks/{task['id']}:subscribe")
    harness.handler.bridge = bridge

    status, body = harness.run_sse()

    assert status == HTTPStatus.OK
    assert body["task"]["id"] == task["id"]


def test_subscribe_open_task_sends_bounded_local_replay_events() -> None:
    bridge = A2ABridge(agent=RecordingAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
    task = bridge.create_completed_task(
        {"messageId": "m1", "role": "ROLE_USER", "parts": [{"text": "hello"}]},
        target="WORKER",
    )
    replayed = dict(task)
    replayed["status"] = {"state": "TASK_STATE_SUBMITTED"}
    bridge.store.put(replayed)
    bridge._events.publish(str(task["id"]), replayed)
    harness = HandlerHarness("POST", f"/tasks/{task['id']}:subscribe")
    harness.handler.bridge = bridge

    status, events = harness.run_sse_events()

    assert status == HTTPStatus.OK
    assert [event["task"]["status"]["state"] for event in events] == [
        "TASK_STATE_WORKING",
        "TASK_STATE_SUBMITTED",
    ]


def test_subscribe_after_restart_returns_snapshot_without_durable_replay(tmp_path: Path) -> None:
    state_file = tmp_path / "a2a-state.json"
    first_bridge = A2ABridge(
        agent=RecordingAgent(),
        agent_card={},
        target="WORKER",
        store=A2ATaskStore(storage_path=state_file),
        task_timeout_seconds=0.0,
    )
    task = first_bridge.create_completed_task(
        {"messageId": "m1", "role": "ROLE_USER", "parts": [{"text": "hello"}]},
        target="WORKER",
    )
    replayed = dict(task)
    replayed["status"] = {"state": "TASK_STATE_SUBMITTED"}
    first_bridge.store.put(replayed)
    first_bridge._events.publish(str(task["id"]), replayed)
    restarted_bridge = A2ABridge(
        agent=RecordingAgent(),
        agent_card={},
        target="WORKER",
        store=A2ATaskStore(storage_path=state_file),
        task_timeout_seconds=0.0,
    )
    harness = HandlerHarness("POST", f"/tasks/{task['id']}:subscribe")
    harness.handler.bridge = restarted_bridge

    status, body = harness.run()
    stored = restarted_bridge.store.get(str(task["id"]))

    assert status == HTTPStatus.CONFLICT
    assert body["title"] == "Task is terminal"
    assert stored is not None
    assert stored["status"]["state"] == "TASK_STATE_FAILED"


def test_delete_push_config_error_routes_return_problem_json() -> None:
    bridge = A2ABridge(agent=RecordingAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
    task = bridge.create_completed_task(
        {"messageId": "m1", "role": "ROLE_USER", "parts": [{"text": "hello"}]},
        target="WORKER",
    )
    bad_path = HandlerHarness("DELETE", "/tasks")
    bad_path.handler.bridge = bridge
    unknown_task = HandlerHarness("DELETE", "/tasks/missing/pushNotificationConfigs/cfg-a")
    unknown_task.handler.bridge = bridge
    missing_config = HandlerHarness("DELETE", f"/tasks/{task['id']}/pushNotificationConfigs")
    missing_config.handler.bridge = bridge

    bad_status, bad_body = bad_path.run()
    unknown_status, unknown_body = unknown_task.run()
    missing_status, missing_body = missing_config.run()

    assert bad_status == HTTPStatus.NOT_FOUND
    assert bad_body["title"] == "Not Found"
    assert unknown_status == HTTPStatus.NOT_FOUND
    assert unknown_body["detail"] == "Unknown task: missing"
    assert missing_status == HTTPStatus.NOT_FOUND
    assert missing_body["detail"] == "Missing push notification config id."


def test_data_none_branches_return_media_type_problem_json() -> None:
    bridge = A2ABridge(agent=RecordingAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
    task = bridge.create_completed_task(
        {"messageId": "m1", "role": "ROLE_USER", "parts": [{"text": "hello"}]},
        target="WORKER",
    )
    push = HandlerHarness(
        "POST",
        f"/tasks/{task['id']}/pushNotificationConfigs",
        body={},
        headers={"Content-Type": "text/plain"},
    )
    push.handler.bridge = bridge
    stream = HandlerHarness(
        "POST", "/message:stream", body={}, headers={"Content-Type": "text/plain"}
    )
    stream.handler.bridge = bridge
    rpc = HandlerHarness("POST", "/rpc", body={}, headers={"Content-Type": "text/plain"})
    rpc.handler.bridge = bridge

    for harness in (push, stream, rpc):
        status, body = harness.run()
        assert status == HTTPStatus.UNSUPPORTED_MEDIA_TYPE
        assert body["title"] == "Unsupported Media Type"


def test_cancel_route_success_and_unknown_post_route() -> None:
    bridge = A2ABridge(agent=RecordingAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
    task = bridge.create_completed_task(
        {"messageId": "m1", "role": "ROLE_USER", "parts": [{"text": "hello"}]},
        target="WORKER",
    )
    cancel = HandlerHarness("POST", f"/tasks/{task['id']}:cancel")
    cancel.handler.bridge = bridge
    unknown = HandlerHarness("POST", "/unknown")
    unknown.handler.bridge = bridge

    cancel_status, cancel_body = cancel.run()
    unknown_status, unknown_body = unknown.run()

    assert cancel_status == HTTPStatus.OK
    assert cancel_body["status"]["state"] == "TASK_STATE_CANCELED"
    assert unknown_status == HTTPStatus.NOT_FOUND
    assert unknown_body["title"] == "Not Found"


def test_delete_route_requires_authorization() -> None:
    bridge = A2ABridge(
        agent=RecordingAgent(),
        agent_card={},
        target="WORKER",
        store=A2ATaskStore(),
        auth_token="secret",
    )
    harness = HandlerHarness("DELETE", "/tasks/task-a/pushNotificationConfigs/cfg-a")
    harness.handler.bridge = bridge

    status, body = harness.run()

    assert status == HTTPStatus.UNAUTHORIZED
    assert body["title"] == "Unauthorized"
