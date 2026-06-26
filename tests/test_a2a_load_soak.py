# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded local load and soak tests for the A2A bridge

from __future__ import annotations

import contextlib
import http.client
import json
import socket
import threading
from pathlib import Path
from typing import Any

from a2a_server_helpers import RecordingAgent
from synapse_channel.a2a_http import make_a2a_http_server
from synapse_channel.a2a_server import A2ABridge
from synapse_channel.a2a_store import A2ATaskStore


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


def _message(task_id: str) -> dict[str, Any]:
    return {
        "taskId": task_id,
        "messageId": f"message-{task_id}",
        "role": "ROLE_USER",
        "parts": [{"text": f"work {task_id}"}],
    }


def _post_json(port: int, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    payload = json.dumps(body).encode("utf-8")
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5.0)
    try:
        connection.request(
            "POST",
            path,
            body=payload,
            headers={"Content-Length": str(len(payload))},
        )
        response = connection.getresponse()
        raw_body = response.read()
        return response.status, json.loads(raw_body.decode("utf-8"))
    finally:
        connection.close()


def test_real_http_send_path_persists_bounded_request_churn(tmp_path: Path) -> None:
    state_file = tmp_path / "a2a-state.json"
    agent = RecordingAgent()
    bridge = A2ABridge(
        agent=agent,
        agent_card={},
        target="WORKER",
        store=A2ATaskStore(storage_path=state_file, max_tasks=32),
    )
    port = _free_port()
    server = make_a2a_http_server(bridge=bridge, host="127.0.0.1", port=port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for index in range(16):
            status, body = _post_json(
                port,
                "/message:send",
                {"message": _message(f"task-{index:02d}")},
            )
            assert status == http.client.OK
            assert body["task"]["status"]["state"] == "TASK_STATE_WORKING"
    finally:
        server.shutdown()
        server.server_close()
        with contextlib.suppress(RuntimeError):
            thread.join(timeout=2.0)

    reloaded = A2ATaskStore(storage_path=state_file)
    assert len(reloaded.list_tasks()) == 16
    assert len(agent.messages) == 16


def test_webhook_failures_do_not_block_bounded_completion_pressure() -> None:
    attempts: list[dict[str, Any]] = []

    def failing_deliverer(delivery: dict[str, Any]) -> None:
        attempts.append(delivery)
        raise TimeoutError("offline webhook")

    bridge = A2ABridge(
        agent=RecordingAgent(),
        agent_card={},
        target="WORKER",
        store=A2ATaskStore(),
        push_deliverer=failing_deliverer,
    )

    for index in range(12):
        task = bridge.create_working_task(_message(f"task-{index:02d}"), target="WORKER")
        bridge.create_push_notification_config(
            str(task["id"]),
            {"webhookUrl": "https://example.test/hook"},
        )
        bridge.handle_synapse_frame(
            {
                "type": "chat",
                "sender": "WORKER",
                "payload": f"done {index}\n[A2A-TASK:{task['id']} contextId={task['contextId']}]",
            }
        )

    assert len(attempts) == 12
    assert {
        task["status"]["state"] for task in bridge.list_tasks(state="TASK_STATE_COMPLETED")["tasks"]
    } == {"TASK_STATE_COMPLETED"}


def test_subscriber_fanout_pressure_delivers_terminal_update_and_cleans_up() -> None:
    bridge = A2ABridge(
        agent=RecordingAgent(),
        agent_card={},
        target="WORKER",
        store=A2ATaskStore(),
    )
    task = bridge.create_working_task(_message("fanout"), target="WORKER")
    received: list[list[dict[str, Any]]] = []
    received_lock = threading.Lock()
    ready = threading.Barrier(13)

    def subscribe() -> None:
        ready.wait(timeout=2.0)
        events = bridge.subscribe_task_events(str(task["id"]), wait_seconds=1.0) or []
        with received_lock:
            received.append(events)

    threads = [threading.Thread(target=subscribe) for _ in range(12)]
    for thread in threads:
        thread.start()
    ready.wait(timeout=2.0)
    bridge.handle_synapse_frame(
        {
            "type": "chat",
            "sender": "WORKER",
            "payload": f"done\n[A2A-TASK:{task['id']} contextId={task['contextId']}]",
        }
    )
    for thread in threads:
        thread.join(timeout=2.0)

    assert len(received) == 12
    assert all(
        events[-1]["task"]["status"]["state"] == "TASK_STATE_COMPLETED" for events in received
    )
    assert bridge._events._subscribers == {}
