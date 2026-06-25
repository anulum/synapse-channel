# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — security tests for the A2A bridge

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from io import BytesIO
from typing import Any

import pytest

from synapse_channel import cli_a2a
from synapse_channel.a2a_server import A2ABridge, build_a2a_handler
from synapse_channel.a2a_store import A2ATaskStore


class FakeAgent:
    """Small async agent stub for A2A bridge security tests."""

    async def chat(self, payload: str, *, target: str = "all") -> None:
        """Accept one chat call without touching the live SYNAPSE bus."""


def _bridge() -> A2ABridge:
    return A2ABridge(agent=FakeAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())


class HandlerHarness:
    """Instantiate one A2A request handler without binding a socket."""

    def __init__(
        self,
        bridge: A2ABridge,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        handler_type = build_a2a_handler(bridge)
        payload = b"" if body is None else json.dumps(body).encode("utf-8")
        handler: Any = handler_type.__new__(handler_type)
        handler.command = method
        handler.path = path
        handler.request_version = "HTTP/1.1"
        handler.requestline = f"{method} {path} HTTP/1.1"
        handler.headers = {"Content-Length": str(len(payload)), **(headers or {})}
        handler.rfile = BytesIO(payload)
        handler.wfile = BytesIO()
        handler.close_connection = False
        handler.request = object()
        handler.client_address = ("127.0.0.1", 1)
        handler.server = object()
        handler.responses = type(handler).responses
        self.handler = handler

    def run(self) -> tuple[int, dict[str, Any]]:
        """Run the configured handler method and return status plus JSON body."""
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


def _message(task_id: str, text: str = "work") -> dict[str, object]:
    return {
        "taskId": task_id,
        "messageId": f"message-{task_id}",
        "role": "ROLE_USER",
        "parts": [{"text": text}],
    }


def test_direct_task_creation_rejects_unsafe_task_id() -> None:
    bridge = _bridge()

    try:
        bridge.create_working_task(_message("../task"))
    except ValueError as exc:
        assert str(exc) == "message.taskId contains unsupported characters"
    else:
        raise AssertionError("unsafe direct taskId was accepted")


def test_direct_task_creation_rejects_unsafe_context_id() -> None:
    bridge = _bridge()
    message = _message("task-a")
    message["contextId"] = "ctx/../x"

    try:
        bridge.create_working_task(message)
    except ValueError as exc:
        assert str(exc) == "message.contextId contains unsupported characters"
    else:
        raise AssertionError("unsafe direct contextId was accepted")


def test_direct_task_creation_rejects_duplicate_task_id() -> None:
    bridge = _bridge()
    bridge.create_working_task(_message("task-a", "first"))

    try:
        bridge.create_working_task(_message("task-a", "second"))
    except ValueError as exc:
        assert str(exc) == "message.taskId already exists"
    else:
        raise AssertionError("duplicate direct taskId was accepted")


def test_auth_token_leaves_well_known_card_public() -> None:
    bridge = A2ABridge(
        agent=FakeAgent(),
        agent_card={"name": "SYNAPSE CHANNEL"},
        target="WORKER",
        store=A2ATaskStore(),
        auth_token="secret",
    )

    status, body = HandlerHarness(bridge, "GET", "/.well-known/agent-card.json").run()

    assert status == HTTPStatus.OK
    assert body["name"] == "SYNAPSE CHANNEL"


def test_auth_token_protects_a2a_routes() -> None:
    bridge = A2ABridge(
        agent=FakeAgent(),
        agent_card={"name": "SYNAPSE CHANNEL"},
        target="WORKER",
        store=A2ATaskStore(),
        auth_token="secret",
    )
    routes = [
        ("GET", "/extendedAgentCard", None),
        ("GET", "/tasks", None),
        (
            "POST",
            "/message:send",
            {"message": {"messageId": "m1", "role": "ROLE_USER", "parts": [{"text": "x"}]}},
        ),
        ("POST", "/rpc", {"jsonrpc": "2.0", "id": "r1", "method": "message/send"}),
        ("POST", "/tasks/task-a:cancel", None),
    ]

    for method, path, body in routes:
        status, response = HandlerHarness(bridge, method, path, body=body).run()
        assert status == HTTPStatus.UNAUTHORIZED
        assert response["title"] == "Unauthorized"


def test_auth_token_accepts_bearer_for_message_send() -> None:
    bridge = A2ABridge(
        agent=FakeAgent(),
        agent_card={"name": "SYNAPSE CHANNEL"},
        target="WORKER",
        store=A2ATaskStore(),
        auth_token="secret",
    )

    status, body = HandlerHarness(
        bridge,
        "POST",
        "/message:send",
        body={"message": {"messageId": "m1", "role": "ROLE_USER", "parts": [{"text": "x"}]}},
        headers={"Authorization": "Bearer secret"},
    ).run()

    assert status == HTTPStatus.OK
    assert body["task"]["status"]["state"] == "TASK_STATE_WORKING"


def test_a2a_serve_refuses_exposed_bind_without_bearer_token(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fail_fetch_manifest(**_kwargs: object) -> list[dict[str, Any]]:
        raise AssertionError("manifest fetch should not be reached")

    monkeypatch.setattr(cli_a2a, "_fetch_manifest", fail_fetch_manifest)
    args = argparse.Namespace(
        uri="ws://hub",
        name="A2A-BRIDGE",
        token=None,
        host="0.0.0.0",
        port=8877,
        endpoint_url="http://0.0.0.0:8877",
        target="all",
        bridge_name="SYNAPSE CHANNEL",
        description=None,
        documentation_url="https://anulum.github.io/synapse-channel",
        bearer_auth=False,
        a2a_token=None,
        state_file=None,
        task_timeout=300.0,
        subscribe_timeout=0.0,
        insecure_off_loopback=False,
    )

    assert cli_a2a._cmd_a2a_serve(args) == 2
    assert "Refusing to bind" in capsys.readouterr().err


def test_a2a_serve_insecure_override_allows_exposed_manifest_fetch(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fetched: list[bool] = []

    async def fail_to_reach_hub(**_kwargs: object) -> list[dict[str, Any]] | None:
        fetched.append(True)
        return None

    monkeypatch.setattr(cli_a2a, "_fetch_manifest", fail_to_reach_hub)
    args = argparse.Namespace(
        uri="ws://hub",
        name="A2A-BRIDGE",
        token=None,
        host="0.0.0.0",
        port=8877,
        endpoint_url="http://0.0.0.0:8877",
        target="all",
        bridge_name="SYNAPSE CHANNEL",
        description=None,
        documentation_url="https://anulum.github.io/synapse-channel",
        bearer_auth=False,
        a2a_token=None,
        state_file=None,
        task_timeout=300.0,
        subscribe_timeout=0.0,
        insecure_off_loopback=True,
    )

    assert cli_a2a._cmd_a2a_serve(args) == 1
    captured = capsys.readouterr()
    assert fetched == [True]
    assert "WARNING: binding A2A bridge" in captured.err
    assert "Could not reach hub" in captured.err


def test_a2a_serve_allows_exposed_bearer_auth_without_override(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fetched: list[bool] = []

    async def fail_to_reach_hub(**_kwargs: object) -> list[dict[str, Any]] | None:
        fetched.append(True)
        return None

    monkeypatch.setattr(cli_a2a, "_fetch_manifest", fail_to_reach_hub)
    args = argparse.Namespace(
        uri="ws://hub",
        name="A2A-BRIDGE",
        token=None,
        host="0.0.0.0",
        port=8877,
        endpoint_url="http://0.0.0.0:8877",
        target="all",
        bridge_name="SYNAPSE CHANNEL",
        description=None,
        documentation_url="https://anulum.github.io/synapse-channel",
        bearer_auth=True,
        a2a_token="secret",
        state_file=None,
        task_timeout=300.0,
        subscribe_timeout=0.0,
        insecure_off_loopback=False,
    )

    assert cli_a2a._cmd_a2a_serve(args) == 1
    captured = capsys.readouterr()
    assert fetched == [True]
    assert "Refusing to bind" not in captured.err
    assert "WARNING: binding A2A bridge" not in captured.err
    assert "Could not reach hub" in captured.err


def test_default_timeout_boundary_fails_stale_open_task() -> None:
    bridge = _bridge()
    task = bridge.create_working_task(_message("task-timeout"))
    task["metadata"]["updatedAt"] = 100.0
    bridge.store.put(task)

    early = bridge.expire_timed_out_tasks(now=399.9)
    before_deadline = bridge.store.get("task-timeout")
    assert before_deadline is not None
    before_deadline_state = before_deadline["status"]["state"]
    expired = bridge.expire_timed_out_tasks(now=400.0)
    after_deadline = bridge.store.get("task-timeout")

    assert early == []
    assert before_deadline_state == "TASK_STATE_WORKING"
    assert len(expired) == 1
    assert after_deadline is not None
    assert after_deadline["status"]["state"] == "TASK_STATE_FAILED"
