# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — security tests for the A2A bridge

from __future__ import annotations

import argparse
import hmac
from http import HTTPStatus
from typing import Any, cast

import pytest

from a2a_server_helpers import HandlerHarness, RecordingAgent
from synapse_channel import cli_a2a
from synapse_channel.a2a_http import build_a2a_handler
from synapse_channel.a2a_http_protocol import (
    describe_a2a_origin_policy,
    endpoint_authorities,
    normalise_authority,
    normalise_origin,
    origin_allowed,
)
from synapse_channel.a2a_server import A2ABridge
from synapse_channel.a2a_store import A2ATaskStore
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.protocol import MAX_JSON_DEPTH


def _bridge(
    *,
    allowed_origins: tuple[str, ...] = (),
    allowed_authorities: tuple[str, ...] = ("bridge.test",),
) -> A2ABridge:
    return A2ABridge(
        agent=RecordingAgent(),
        agent_card={"name": "SYNAPSE CHANNEL"},
        target="WORKER",
        store=A2ATaskStore(),
        allowed_origins=allowed_origins,
        allowed_authorities=allowed_authorities,
    )


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
        agent=RecordingAgent(),
        agent_card={"name": "SYNAPSE CHANNEL"},
        target="WORKER",
        store=A2ATaskStore(),
        auth_token="secret",
        allowed_authorities=("bridge.test",),
    )
    harness = HandlerHarness("GET", "/.well-known/agent-card.json")
    harness.handler.bridge = bridge

    status, body = harness.run()

    assert status == HTTPStatus.OK
    assert body["name"] == "SYNAPSE CHANNEL"


def test_auth_token_protects_a2a_routes() -> None:
    bridge = A2ABridge(
        agent=RecordingAgent(),
        agent_card={"name": "SYNAPSE CHANNEL"},
        target="WORKER",
        store=A2ATaskStore(),
        auth_token="secret",
        allowed_authorities=("bridge.test",),
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
        harness = HandlerHarness(method, path, body=body)
        harness.handler.bridge = bridge
        status, response = harness.run()
        assert status == HTTPStatus.UNAUTHORIZED
        assert response["title"] == "Unauthorized"


def test_auth_token_accepts_bearer_for_message_send() -> None:
    bridge = A2ABridge(
        agent=RecordingAgent(),
        agent_card={"name": "SYNAPSE CHANNEL"},
        target="WORKER",
        store=A2ATaskStore(),
        auth_token="secret",
        allowed_authorities=("bridge.test",),
    )

    harness = HandlerHarness(
        "POST",
        "/message:send",
        body={"message": {"messageId": "m1", "role": "ROLE_USER", "parts": [{"text": "x"}]}},
        headers={"Authorization": "Bearer secret"},
    )
    harness.handler.bridge = bridge
    status, body = harness.run()

    assert status == HTTPStatus.OK
    assert body["task"]["status"]["state"] == "TASK_STATE_WORKING"


@pytest.mark.parametrize(
    ("headers", "expected_status"),
    [
        ({}, HTTPStatus.UNAUTHORIZED),
        ({"Authorization": "Bearer wrong"}, HTTPStatus.UNAUTHORIZED),
        ({"Authorization": "Bearer secret"}, HTTPStatus.OK),
    ],
)
def test_auth_token_uses_constant_time_bearer_comparison(
    monkeypatch: pytest.MonkeyPatch,
    headers: dict[str, str],
    expected_status: HTTPStatus,
) -> None:
    calls: list[tuple[str, str]] = []
    original_compare_digest = hmac.compare_digest

    def spy_compare_digest(left: str, right: str) -> bool:
        calls.append((left, right))
        return original_compare_digest(left, right)

    monkeypatch.setattr(hmac, "compare_digest", spy_compare_digest)
    bridge = A2ABridge(
        agent=RecordingAgent(),
        agent_card={"name": "SYNAPSE CHANNEL"},
        target="WORKER",
        store=A2ATaskStore(),
        auth_token="secret",
        allowed_authorities=("bridge.test",),
    )
    harness = HandlerHarness("GET", "/tasks", headers=headers)
    harness.handler.bridge = bridge

    status, _body = harness.run()

    assert status == expected_status
    assert calls == [(headers.get("Authorization", ""), "Bearer secret")]


def test_message_send_rejects_json_nested_past_wire_depth_limit() -> None:
    raw = b'{"message":' + (b"[" * MAX_JSON_DEPTH) + b"0" + (b"]" * MAX_JSON_DEPTH) + b"}"
    harness = HandlerHarness("POST", "/message:send", body=raw)
    harness.handler.bridge = _bridge()

    status, body = harness.run()

    assert status == HTTPStatus.BAD_REQUEST
    assert body["title"] == "Invalid JSON"


def test_a2a_serve_refuses_exposed_bind_without_bearer_token(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fail_fetch_manifest(**_kwargs: object) -> list[dict[str, Any]]:
        raise AssertionError("manifest fetch should not be reached")

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

    assert cli_a2a._cmd_a2a_serve(args, manifest_fetcher=fail_fetch_manifest) == 2
    assert "Refusing to bind" in capsys.readouterr().err


def test_a2a_serve_insecure_override_allows_exposed_manifest_fetch(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fetched: list[bool] = []

    async def fail_to_reach_hub(**_kwargs: object) -> list[dict[str, Any]] | None:
        fetched.append(True)
        return None

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

    assert cli_a2a._cmd_a2a_serve(args, manifest_fetcher=fail_to_reach_hub) == 1
    captured = capsys.readouterr()
    assert fetched == [True]
    assert "WARNING: bound to non-loopback host" in captured.err
    assert "Could not reach hub" in captured.err


def test_a2a_serve_allows_exposed_bearer_auth_without_override(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Plaintext bearer on a non-loopback bind is refused without the override.

    R4 parity: a bearer token on cleartext HTTP off-loopback is network-readable,
    so a2a-serve fails closed unless --insecure-off-loopback or native TLS is set.
    """
    fetched: list[bool] = []

    async def fail_to_reach_hub(**_kwargs: object) -> list[dict[str, Any]] | None:
        fetched.append(True)
        return None

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

    assert cli_a2a._cmd_a2a_serve(args, manifest_fetcher=fail_to_reach_hub) == 2
    captured = capsys.readouterr()
    assert fetched == []
    assert "Refusing to bind" in captured.err
    assert "plaintext HTTP" in captured.err or "cleartext bearer" in captured.err


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


# --- Origin allow-list (optional browser-origin hardening, TODO 4166) ----------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://ide.example", "https://ide.example"),
        ("  HTTPS://IDE.Example/  ", "https://ide.example"),
        ("https://host:8443/", "https://host:8443"),
        ("http://[::1]:8877", "http://[::1]:8877"),
    ],
)
def test_normalise_origin_lowercases_and_trims(value: str, expected: str) -> None:
    assert normalise_origin(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "null",
        "file://host",
        "https://user@host",
        "https://host/path",
        "https://host:",
        "https://bad host",
        "https://host\\@attacker.example",
        "https://host\x7f",
    ],
)
def test_normalise_origin_refuses_non_exact_principals(value: str) -> None:
    with pytest.raises(ValueError, match="exact|opaque"):
        normalise_origin(value)


def test_describe_a2a_origin_policy_reports_defaults_and_enabled_list() -> None:
    off = describe_a2a_origin_policy()
    assert off["opaque_null_rejected"] is True
    assert off["allow_list_enabled"] is False
    assert off["default_allow_list"] == "off"
    assert off["host_authority_binding"] == "always"
    assert off["present_origin_default"] == "deny"
    on = describe_a2a_origin_policy(allow_origins=("https://ide.example",))
    assert on["allow_list_enabled"] is True
    assert on["allow_origins"] == ["https://ide.example"]


def test_endpoint_authorities_cover_only_the_advertised_default_port() -> None:
    assert endpoint_authorities("https://IDE.example./a2a/v1") == (
        "ide.example",
        "ide.example:443",
    )
    assert endpoint_authorities("http://Bridge.example/a2a/v1") == (
        "bridge.example",
        "bridge.example:80",
    )
    assert endpoint_authorities("http://127.0.0.1:8877") == ("127.0.0.1:8877",)
    assert endpoint_authorities("http://[::1]/a2a/v1") == ("[::1]", "[::1]:80")
    assert normalise_authority(" IDE.EXAMPLE:443 ") == "ide.example:443"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "user@bridge.test",
        "bridge.test:",
        "bridge.test:invalid",
        "bridge.test,attacker.test",
        "bad host",
    ],
)
def test_normalise_authority_refuses_ambiguous_host_values(value: str) -> None:
    with pytest.raises(ValueError, match="exact"):
        normalise_authority(value)


def test_endpoint_authorities_refuse_credential_or_delimiter_ambiguity() -> None:
    with pytest.raises(ValueError, match="authority"):
        endpoint_authorities("https://user@bridge.test/a2a")
    with pytest.raises(ValueError, match="authority"):
        endpoint_authorities("https://bridge.test\\@attacker.example/a2a")
    with pytest.raises(ValueError, match="authority"):
        endpoint_authorities("https://bridge.test:/a2a")


@pytest.mark.parametrize(
    ("origin_header", "host_header", "allowed", "authorities", "expected"),
    [
        (None, None, (), ("bridge.test",), False),
        (None, "bridge.test", (), ("bridge.test",), True),
        ("https://x", "bridge.test", (), ("bridge.test",), False),
        (None, "bridge.test", ("https://x",), ("bridge.test",), True),
        (None, "evil.test", ("https://x",), ("bridge.test",), False),
        ("https://x", "bridge.test", ("https://x",), ("bridge.test",), True),
        ("https://X/", "bridge.test", ("https://x",), ("bridge.test",), True),
        ("https://evil", "bridge.test", ("https://x",), ("bridge.test",), False),
        ("https://x", "bridge.test", ("https://y", "https://x"), ("bridge.test",), True),
        ("null", "bridge.test", ("https://x",), ("bridge.test",), False),
        ("https://x:443", "bridge.test", ("https://x",), ("bridge.test",), False),
    ],
)
def test_origin_allowed_decisions(
    origin_header: str | None,
    host_header: str | None,
    allowed: tuple[str, ...],
    authorities: tuple[str, ...],
    expected: bool,
) -> None:
    assert origin_allowed(origin_header, host_header, allowed, authorities) is expected


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
def test_disallowed_origin_is_refused_on_every_method(method: str) -> None:
    path = "/tasks" if method == "GET" else "/message:send"
    if method == "DELETE":
        path = "/tasks/t/pushNotificationConfigs/c"
    harness = HandlerHarness(
        method,
        path,
        headers={"Host": "bridge.test", "Origin": "https://evil.example"},
    )
    harness.handler.bridge = _bridge(allowed_origins=("https://ide.example",))

    status, body = harness.run()

    assert status == HTTPStatus.FORBIDDEN
    assert body["title"] == "Forbidden"
    assert body["detail"] == "Origin or Host not allowed"


def test_allow_list_gates_even_the_public_agent_card() -> None:
    """A hostile page must not read the discovery card through a victim's browser."""
    harness = HandlerHarness(
        "GET",
        "/.well-known/agent-card.json",
        headers={"Host": "bridge.test", "Origin": "https://evil.example"},
    )
    harness.handler.bridge = _bridge(allowed_origins=("https://ide.example",))

    status, body = harness.run()

    assert status == HTTPStatus.FORBIDDEN
    assert body["title"] == "Forbidden"


def test_allowed_origin_passes_through_to_the_route() -> None:
    harness = HandlerHarness(
        "GET",
        "/.well-known/agent-card.json",
        headers={"Host": "bridge.test", "Origin": "https://IDE.example/"},
    )
    harness.handler.bridge = _bridge(allowed_origins=("https://ide.example",))

    status, body = harness.run()

    assert status == HTTPStatus.OK
    assert body["name"] == "SYNAPSE CHANNEL"


def test_missing_origin_requires_the_advertised_host_authority() -> None:
    harness = HandlerHarness(
        "GET",
        "/.well-known/agent-card.json",
        headers={"Host": "bridge.test"},
    )
    harness.handler.bridge = _bridge(allowed_origins=("https://ide.example",))

    status, _body = harness.run()

    assert status == HTTPStatus.OK

    hostile = HandlerHarness(
        "GET",
        "/.well-known/agent-card.json",
        headers={"Host": "attacker.example"},
    )
    hostile.handler.bridge = _bridge(allowed_origins=("https://ide.example",))
    denied, body = hostile.run()
    assert denied == HTTPStatus.FORBIDDEN
    assert body["detail"] == "Origin or Host not allowed"


def test_opaque_origin_is_always_refused() -> None:
    harness = HandlerHarness(
        "GET",
        "/.well-known/agent-card.json",
        headers={"Host": "bridge.test", "Origin": "null"},
    )
    harness.handler.bridge = _bridge(allowed_origins=("https://ide.example",))

    status, _body = harness.run()

    assert status == HTTPStatus.FORBIDDEN


def test_no_allow_list_refuses_every_present_browser_origin() -> None:
    harness = HandlerHarness(
        "GET",
        "/.well-known/agent-card.json",
        headers={"Host": "bridge.test", "Origin": "https://anything.example"},
    )
    harness.handler.bridge = _bridge()

    status, body = harness.run()

    assert status == HTTPStatus.FORBIDDEN
    assert body["detail"] == "Origin or Host not allowed"


@pytest.mark.parametrize(
    ("allow_origin", "expected_origins"),
    [
        (["https://IDE.example/"], ("https://ide.example",)),
        (None, ()),
    ],
)
def test_a2a_serve_always_threads_endpoint_authorities_into_the_bridge(
    allow_origin: list[str] | None,
    expected_origins: tuple[str, ...],
) -> None:
    """The CLI always derives Host authority; Origin remains independently optional."""
    captured: dict[str, Any] = {}

    class _Runtime:
        def __init__(self, _agent: Any) -> None:
            pass

        def start(self, **_kwargs: Any) -> bool:
            return True

        def run(self, _coro: Any) -> Any:
            return None

        def stop(self) -> None:
            return None

    def capturing_bridge(**kwargs: Any) -> A2ABridge:
        captured.update(kwargs)
        return A2ABridge(
            agent=RecordingAgent(),
            agent_card={},
            target=kwargs["target"],
            allowed_origins=kwargs["allowed_origins"],
            allowed_authorities=kwargs["allowed_authorities"],
        )

    async def manifest(**_kwargs: object) -> list[dict[str, Any]]:
        return [{"name": "worker", "capabilities": ["chat"]}]

    args = argparse.Namespace(
        uri="ws://hub",
        name="A2A-BRIDGE",
        token=None,
        host="127.0.0.1",
        port=8877,
        endpoint_url="https://bridge.example/a2a",
        target="all",
        bridge_name="SYNAPSE CHANNEL",
        description=None,
        documentation_url="https://anulum.github.io/synapse-channel",
        bearer_auth=False,
        a2a_token=None,
        allow_origin=allow_origin,
        state_file=None,
        task_timeout=300.0,
        subscribe_timeout=0.0,
        insecure_off_loopback=False,
    )

    assert (
        cli_a2a._cmd_a2a_serve(
            args,
            manifest_fetcher=manifest,
            agent_factory=lambda *a, **k: cast(SynapseAgent, object()),
            runtime_factory=_Runtime,
            bridge_factory=capturing_bridge,
            server_runner=lambda **_kwargs: None,
        )
        == 0
    )
    assert captured["allowed_origins"] == expected_origins
    assert captured["allowed_authorities"] == ("bridge.example", "bridge.example:443")


def test_direct_http_handler_refuses_bridge_without_endpoint_authority() -> None:
    bridge = A2ABridge(
        agent=RecordingAgent(),
        agent_card={},
        target="WORKER",
        store=A2ATaskStore(),
    )

    with pytest.raises(ValueError, match="trusted endpoint authority"):
        build_a2a_handler(bridge)


@pytest.mark.parametrize(
    "agent_card",
    [
        {
            "supportedInterfaces": [
                {
                    "url": "https://Bridge.Example/a2a",
                    "protocolBinding": "HTTP+JSON",
                }
            ]
        },
        {"url": "https://Bridge.Example/a2a"},
    ],
)
def test_direct_bridge_derives_authorities_from_advertised_http_url(
    agent_card: dict[str, object],
) -> None:
    bridge = A2ABridge(
        agent=RecordingAgent(),
        agent_card=agent_card,
        target="WORKER",
        store=A2ATaskStore(),
    )

    assert bridge.allowed_authorities == ("bridge.example", "bridge.example:443")
    handler = cast(Any, build_a2a_handler(bridge))
    assert handler.bridge is bridge


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/.well-known/agent-card.json", None),
        ("GET", "/extendedAgentCard", None),
        ("GET", "/tasks", None),
        ("GET", "/tasks/missing?historyLength=1", None),
        ("GET", "/tasks/missing/pushNotificationConfigs", None),
        ("GET", "/tasks/missing/pushNotificationConfigs/config", None),
        (
            "POST",
            "/message:send",
            {
                "message": {
                    "messageId": "host-boundary-send",
                    "role": "ROLE_USER",
                    "parts": [{"text": "work"}],
                }
            },
        ),
        ("POST", "/rpc", {"jsonrpc": "2.0", "id": "host-boundary-rpc", "method": "tasks/list"}),
        ("POST", "/tasks/missing:cancel", None),
        ("POST", "/tasks/missing:subscribe", None),
        ("POST", "/tasks/missing/pushNotificationConfigs", {"url": "https://receiver.test"}),
        ("DELETE", "/tasks/missing/pushNotificationConfigs/config", None),
    ],
)
def test_exact_host_without_origin_reaches_every_route_boundary(
    method: str,
    path: str,
    body: dict[str, object] | None,
) -> None:
    status, _response = HandlerHarness(
        method,
        path,
        body=body,
        headers={"Host": "example.test"},
    ).run()

    assert status != HTTPStatus.FORBIDDEN


@pytest.mark.parametrize(
    ("host", "skip_host"),
    [
        ("attacker.test", False),
        ("user@example.test", False),
        ("example.test,attacker.test", False),
        ("", True),
    ],
)
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/.well-known/agent-card.json", None),
        ("GET", "/extendedAgentCard", None),
        ("GET", "/tasks", None),
        ("GET", "/tasks/missing?historyLength=1", None),
        ("GET", "/tasks/missing/pushNotificationConfigs", None),
        ("GET", "/tasks/missing/pushNotificationConfigs/config", None),
        (
            "POST",
            "/message:send",
            {
                "message": {
                    "messageId": "host-denied-send",
                    "role": "ROLE_USER",
                    "parts": [{"text": "must not dispatch"}],
                }
            },
        ),
        (
            "POST",
            "/message:stream",
            {
                "message": {
                    "messageId": "host-denied-stream",
                    "role": "ROLE_USER",
                    "parts": [{"text": "must not dispatch"}],
                }
            },
        ),
        ("POST", "/rpc", {"jsonrpc": "2.0", "id": "host-denied-rpc", "method": "tasks/list"}),
        ("POST", "/tasks/missing:cancel", None),
        ("POST", "/tasks/missing:subscribe", None),
        ("POST", "/tasks/missing/pushNotificationConfigs", {"url": "https://receiver.test"}),
        ("DELETE", "/tasks/missing/pushNotificationConfigs/config", None),
    ],
)
def test_untrusted_or_missing_host_is_refused_before_every_route(
    host: str,
    skip_host: bool,
    method: str,
    path: str,
    body: dict[str, object] | None,
) -> None:
    headers = {} if skip_host else {"Host": host}
    harness = HandlerHarness(
        method,
        path,
        body=body,
        headers=headers,
        skip_host=skip_host,
    )

    status, response = harness.run()

    assert status == HTTPStatus.FORBIDDEN
    assert response["detail"] == "Origin or Host not allowed"
    assert response["error"]["details"][0]["reason"] == "ORIGIN_OR_HOST_NOT_ALLOWED"
    assert harness.handler.bridge.store.list_tasks() == []
    assert harness.handler.bridge.agent.messages == []


@pytest.mark.parametrize(
    ("headers", "extra_header_pairs", "bridge"),
    [
        (
            {"Host": "bridge.test"},
            [("Host", "attacker.test")],
            _bridge(),
        ),
        (
            {"Host": "bridge.test", "Origin": "https://ide.example"},
            [("Origin", "https://attacker.example")],
            _bridge(allowed_origins=("https://ide.example",)),
        ),
    ],
)
def test_duplicate_host_or_origin_headers_fail_closed(
    headers: dict[str, str],
    extra_header_pairs: list[tuple[str, str]],
    bridge: A2ABridge,
) -> None:
    status, response = HandlerHarness(
        "GET",
        "/.well-known/agent-card.json",
        headers=headers,
        bridge=bridge,
        extra_header_pairs=extra_header_pairs,
    ).run()

    assert status == HTTPStatus.FORBIDDEN
    assert response["error"]["details"][0]["reason"] == "ORIGIN_OR_HOST_NOT_ALLOWED"


def test_a2a_serve_refuses_opaque_origin_before_hub_access(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fail_fetch_manifest(**_kwargs: object) -> list[dict[str, Any]]:
        raise AssertionError("manifest fetch should not be reached")

    args = argparse.Namespace(
        uri="ws://hub",
        name="A2A-BRIDGE",
        token=None,
        host="127.0.0.1",
        port=8877,
        endpoint_url="http://127.0.0.1:8877",
        target="all",
        bridge_name="SYNAPSE CHANNEL",
        description=None,
        documentation_url="https://anulum.github.io/synapse-channel",
        bearer_auth=False,
        a2a_token=None,
        allow_origin=["null"],
        state_file=None,
        task_timeout=300.0,
        subscribe_timeout=0.0,
        insecure_off_loopback=False,
    )

    assert cli_a2a._cmd_a2a_serve(args, manifest_fetcher=fail_fetch_manifest) == 2
    assert "opaque 'null' origins cannot be allow-listed" in capsys.readouterr().err
