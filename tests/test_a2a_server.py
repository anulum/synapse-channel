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

    def __init__(self, method: str, path: str, *, body: dict[str, Any] | None = None) -> None:
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
        handler.headers = {"Content-Length": str(len(payload))}
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
        else:
            raise AssertionError(self.handler.command)
        raw = self.handler.wfile.getvalue()
        header_blob, body = raw.split(b"\r\n\r\n", 1)
        status = int(header_blob.split(b" ", 2)[1])
        return status, json.loads(body.decode("utf-8"))


def test_well_known_agent_card_endpoint_returns_card() -> None:
    status, body = HandlerHarness("GET", "/.well-known/agent-card.json").run()

    assert status == HTTPStatus.OK
    assert body["name"] == "SYNAPSE CHANNEL"
    assert body["supportedInterfaces"][0]["protocolBinding"] == "HTTP+JSON"


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


def test_bad_json_returns_a2a_problem_json() -> None:
    harness = HandlerHarness("POST", "/message:send")
    harness.handler.rfile = BytesIO(b"{")
    harness.handler.headers = {"Content-Length": "1"}

    status, body = harness.run()

    assert status == HTTPStatus.BAD_REQUEST
    assert body["title"] == "Invalid JSON"
