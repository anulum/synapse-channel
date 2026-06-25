# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the A2A HTTP+JSON bridge

from __future__ import annotations

from http import HTTPStatus

from a2a_server_helpers import FakeAgent, HandlerHarness
from synapse_channel.a2a_server import A2ABridge
from synapse_channel.a2a_store import A2ATaskStore


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
    assert sent[1].startswith(
        "[file: report.pdf; application/pdf; https://example.test/report.pdf]"
    )
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
