# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real gRPC effective-policy profile tests
"""Exercise gRPC TLS, mTLS, bounds, deadlines, admission, and error custody."""

from __future__ import annotations

import asyncio
import importlib
import json
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from a2a_server_helpers import RecordingAgent, _free_port
from synapse_channel.a2a_grpc import (
    METHOD_SEND,
    A2AGrpcClient,
    A2AGrpcPolicy,
    _get_handler,
    _json_deserializer,
    _request_allowed,
    _send_handler,
    build_a2a_grpc_server,
    build_grpc_server_credentials,
    grpc_available,
    start_grpc_in_background,
)
from synapse_channel.a2a_server import A2ABridge, SynapseAgentRuntime
from synapse_channel.a2a_store import A2ATaskStore
from synapse_channel.client.agent import SynapseAgent
from test_a2a_mtls_client_ca import _write_ca_and_certs

pytestmark = pytest.mark.skipif(not grpc_available(), reason="grpcio not installed")


def _grpc() -> Any:
    """Return the optional gRPC module after the module-level availability gate."""
    return importlib.import_module("grpc")


def _send_payload(message_id: str, text: str = "hello") -> dict[str, Any]:
    """Return one valid production SendMessage payload."""
    return {
        "message": {
            "messageId": message_id,
            "role": "ROLE_USER",
            "parts": [{"text": text, "mediaType": "text/plain"}],
        }
    }


def _rpc_error(call: Any) -> Any:
    """Return the transport exception raised by ``call``."""
    try:
        call()
    except Exception as exc:
        return exc
    raise AssertionError("gRPC call unexpectedly succeeded")


def _send_with_client(
    target: str,
    payload: dict[str, Any],
    **client_kwargs: Any,
) -> dict[str, Any]:
    """Send through a client whose channel is closed on every outcome."""
    with A2AGrpcClient(target, **client_kwargs) as client:
        return client.send_message(payload)


class _RecordingContext:
    """Small handler context that records the stable status surface."""

    def __init__(
        self,
        *,
        remaining: float | None = 1.0,
        metadata: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self.remaining = remaining
        self.metadata = metadata
        self.code: Any = None
        self.details = ""

    def invocation_metadata(self) -> tuple[tuple[str, str], ...]:
        return self.metadata

    def time_remaining(self) -> float | None:
        return self.remaining

    def set_code(self, code: Any) -> None:
        self.code = code

    def set_details(self, details: str) -> None:
        self.details = details


class _ExceptionBridge:
    """Bridge adapter that raises the selected exception from either method."""

    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def send_message(
        self,
        payload: dict[str, Any],
        *,
        protocol_version: str | None = None,
        operation_timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        raise self.exc

    def get_task(
        self,
        task_id: str,
        *,
        history_length: int | None = None,
    ) -> dict[str, Any] | None:
        raise self.exc


def test_grpc_handler_policy_branches_return_stable_statuses() -> None:
    grpc = _grpc()
    policy = A2AGrpcPolicy(max_rpc_seconds=1.0)

    too_long = _RecordingContext(remaining=2.0)
    assert _request_allowed(too_long, policy) is False
    assert too_long.code is grpc.StatusCode.INVALID_ARGUMENT
    assert too_long.details == "finite call deadline required"

    expired = _RecordingContext(remaining=0.0)
    assert _request_allowed(expired, policy) is False
    assert expired.code is grpc.StatusCode.DEADLINE_EXCEEDED
    assert expired.details == "call deadline exceeded"

    assert _json_deserializer(2)(b"{}") == {}
    with pytest.raises(ValueError, match="too large"):
        _json_deserializer(1)(b"{}")
    with pytest.raises(ValueError, match="JSON object"):
        _json_deserializer(8)(b"[]")

    for exc, expected_code, expected_details in (
        (TimeoutError("private"), grpc.StatusCode.DEADLINE_EXCEEDED, "call deadline exceeded"),
        (ValueError("private"), grpc.StatusCode.INVALID_ARGUMENT, "request was invalid"),
        (RuntimeError("private"), grpc.StatusCode.INTERNAL, "request failed"),
    ):
        send_context = _RecordingContext()
        assert _send_handler(_ExceptionBridge(exc), policy)({}, send_context) == {}
        assert send_context.code is expected_code
        assert send_context.details == expected_details

        get_context = _RecordingContext()
        assert _get_handler(_ExceptionBridge(exc), policy)({"id": "task"}, get_context) == {}
        assert get_context.code is expected_code
        assert get_context.details == expected_details

    missing_id = _RecordingContext()
    assert _get_handler(_ExceptionBridge(RuntimeError()), policy)({}, missing_id) == {}
    assert missing_id.code is grpc.StatusCode.INVALID_ARGUMENT
    assert missing_id.details == "task id is required"

    bad_history = _RecordingContext()
    assert (
        _get_handler(_ExceptionBridge(AssertionError()), policy)(
            {"id": "task", "historyLength": "not-an-integer"},
            bad_history,
        )
        == {}
    )
    assert bad_history.code is grpc.StatusCode.INVALID_ARGUMENT
    assert bad_history.details == "request was invalid"


def test_grpc_tls_and_mtls_profiles_fail_closed_and_accept_trusted_clients(
    tmp_path: Path,
) -> None:
    grpc = _grpc()
    ca, server_cert, server_key, client_cert, client_key = _write_ca_and_certs(tmp_path)
    bridge = A2ABridge(
        agent=RecordingAgent(),
        agent_card={"name": "grpc-bridge"},
        target="WORKER",
        store=A2ATaskStore(),
    )
    policy = A2AGrpcPolicy(bearer_token="profile-token")

    tls_port = _free_port()
    tls_credentials = build_grpc_server_credentials(
        certfile=server_cert,
        keyfile=server_key,
    )
    tls_server, _thread = start_grpc_in_background(
        bridge,
        host="127.0.0.1",
        port=tls_port,
        server_credentials=tls_credentials,
        policy=policy,
    )
    time.sleep(0.05)
    try:
        plain_error = _rpc_error(
            lambda: _send_with_client(
                f"127.0.0.1:{tls_port}",
                _send_payload("plain-refused"),
                bearer_token="profile-token",
                timeout_seconds=1.0,
            )
        )
        assert plain_error.code().name == "UNAVAILABLE"

        roots = ca.read_bytes()
        trusted_tls = grpc.ssl_channel_credentials(root_certificates=roots)
        with A2AGrpcClient(
            f"127.0.0.1:{tls_port}",
            channel_credentials=trusted_tls,
            bearer_token="profile-token",
        ) as client:
            assert client.send_message(_send_payload("tls-ok"))["task"]["id"]
    finally:
        tls_server.stop(grace=None)

    mtls_port = _free_port()
    mtls_credentials = build_grpc_server_credentials(
        certfile=server_cert,
        keyfile=server_key,
        client_ca_file=ca,
    )
    mtls_server, _thread = start_grpc_in_background(
        bridge,
        host="127.0.0.1",
        port=mtls_port,
        server_credentials=mtls_credentials,
        policy=policy,
    )
    time.sleep(0.05)
    try:
        no_client_cert = grpc.ssl_channel_credentials(root_certificates=roots)
        missing_error = _rpc_error(
            lambda: _send_with_client(
                f"127.0.0.1:{mtls_port}",
                _send_payload("mtls-missing"),
                channel_credentials=no_client_cert,
                bearer_token="profile-token",
                timeout_seconds=1.0,
            )
        )
        assert missing_error.code().name == "UNAVAILABLE"

        untrusted_dir = tmp_path / "untrusted"
        untrusted_dir.mkdir()
        _, _, _, untrusted_cert, untrusted_key = _write_ca_and_certs(untrusted_dir)
        untrusted_mtls = grpc.ssl_channel_credentials(
            root_certificates=roots,
            private_key=untrusted_key.read_bytes(),
            certificate_chain=untrusted_cert.read_bytes(),
        )
        untrusted_error = _rpc_error(
            lambda: _send_with_client(
                f"127.0.0.1:{mtls_port}",
                _send_payload("mtls-untrusted"),
                channel_credentials=untrusted_mtls,
                bearer_token="profile-token",
                timeout_seconds=1.0,
            )
        )
        assert untrusted_error.code().name == "UNAVAILABLE"

        trusted_mtls = grpc.ssl_channel_credentials(
            root_certificates=roots,
            private_key=client_key.read_bytes(),
            certificate_chain=client_cert.read_bytes(),
        )
        with A2AGrpcClient(
            f"127.0.0.1:{mtls_port}",
            channel_credentials=trusted_mtls,
            bearer_token="profile-token",
        ) as client:
            assert client.send_message(_send_payload("mtls-ok"))["task"]["id"]
    finally:
        mtls_server.stop(grace=None)


def test_grpc_bind_failure_does_not_leave_a_listener() -> None:
    bridge = A2ABridge(
        agent=RecordingAgent(),
        agent_card={"name": "grpc-bridge"},
        target="WORKER",
        store=A2ATaskStore(),
    )
    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    occupied.bind(("127.0.0.1", 0))
    port = int(occupied.getsockname()[1])
    occupied.listen()
    try:
        with pytest.raises(RuntimeError):
            build_a2a_grpc_server(bridge, host="127.0.0.1", port=port)
    finally:
        occupied.close()


def test_grpc_start_failure_stops_constructed_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Server:
        def __init__(self) -> None:
            self.stop_calls: list[object] = []

        def add_generic_rpc_handlers(self, _handlers: object) -> None:
            return None

        def add_insecure_port(self, _bind: str) -> int:
            return 50051

        def start(self) -> None:
            raise RuntimeError("injected start failure")

        def stop(self, grace: object) -> None:
            self.stop_calls.append(grace)

    server = Server()
    fake_grpc = SimpleNamespace(
        server=lambda *_args, **_kwargs: server,
        unary_unary_rpc_method_handler=lambda *_args, **_kwargs: object(),
        method_handlers_generic_handler=lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr("synapse_channel.a2a_grpc._require_grpc", lambda: fake_grpc)

    with pytest.raises(RuntimeError, match="injected start failure"):
        build_a2a_grpc_server(cast(Any, object()))

    assert server.stop_calls == [None]


def test_grpc_requires_bounded_deadline_and_rejects_oversized_input() -> None:
    grpc = _grpc()
    bridge = A2ABridge(
        agent=RecordingAgent(),
        agent_card={"name": "grpc-bridge"},
        target="WORKER",
        store=A2ATaskStore(),
    )
    port = _free_port()
    policy = A2AGrpcPolicy(
        max_receive_message_bytes=256,
        max_rpc_seconds=1.0,
    )
    server, _thread = start_grpc_in_background(
        bridge,
        host="127.0.0.1",
        port=port,
        policy=policy,
    )
    time.sleep(0.05)
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    raw_send = channel.unary_unary(
        METHOD_SEND,
        request_serializer=lambda value: json.dumps(value).encode(),
        response_deserializer=lambda raw: json.loads(raw),
    )
    try:
        no_deadline = _rpc_error(lambda: raw_send(_send_payload("no-deadline")))
        assert no_deadline.code().name == "INVALID_ARGUMENT"
        assert no_deadline.details() == "finite call deadline required"

        too_large = _rpc_error(
            lambda: raw_send(
                _send_payload("too-large", "x" * 512),
                timeout=1.0,
            )
        )
        assert too_large.code().name == "RESOURCE_EXHAUSTED"

        nested = b'{"message":' + (b"[" * 65) + b"0" + (b"]" * 65) + b"}"
        deep_send = channel.unary_unary(
            METHOD_SEND,
            request_serializer=lambda _value: nested,
            response_deserializer=lambda raw: json.loads(raw),
        )
        too_deep = _rpc_error(lambda: deep_send({}, timeout=1.0))
        assert too_deep.code().name == "INTERNAL"
        assert "deserializing request" in too_deep.details()
        assert "nested" not in too_deep.details()
        assert bridge.store.list_tasks() == []
    finally:
        channel.close()
        server.stop(grace=None)


def test_grpc_response_limit_and_duplicate_bearer_fail_before_disclosure() -> None:
    grpc = _grpc()

    class LargeBridge:
        def __init__(self) -> None:
            self.send_calls = 0

        def send_message(
            self,
            payload: dict[str, Any],
            *,
            protocol_version: str | None = None,
            operation_timeout_seconds: float | None = None,
        ) -> dict[str, Any]:
            self.send_calls += 1
            return {"task": {"id": "large", "secret": "s" * 512}}

        def get_task(
            self,
            task_id: str,
            *,
            history_length: int | None = None,
        ) -> dict[str, Any] | None:
            return {"id": task_id, "secret": "s" * 512}

    bridge = LargeBridge()
    port = _free_port()
    server, _thread = start_grpc_in_background(
        bridge,
        host="127.0.0.1",
        port=port,
        policy=A2AGrpcPolicy(
            bearer_token="one",
            max_send_message_bytes=128,
        ),
    )
    time.sleep(0.05)
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    send = channel.unary_unary(
        METHOD_SEND,
        request_serializer=lambda value: json.dumps(value).encode(),
        response_deserializer=lambda raw: json.loads(raw),
    )
    try:
        duplicate = _rpc_error(
            lambda: send(
                _send_payload("duplicate"),
                timeout=1.0,
                metadata=(
                    ("authorization", "Bearer one"),
                    ("authorization", "Bearer one"),
                ),
            )
        )
        assert duplicate.code().name == "UNAUTHENTICATED"
        assert duplicate.details() == "authentication required"
        assert bridge.send_calls == 0

        oversized = _rpc_error(
            lambda: send(
                _send_payload("large"),
                timeout=1.0,
                metadata=(("authorization", "Bearer one"),),
            )
        )
        assert oversized.code().name == "RESOURCE_EXHAUSTED"
        assert oversized.details() == "response exceeds configured limit"
        assert "secret" not in oversized.details()
        assert bridge.send_calls == 1
    finally:
        channel.close()
        server.stop(grace=None)


def test_grpc_admission_recovers_and_internal_errors_are_value_free() -> None:
    class BlockingBridge:
        def __init__(self) -> None:
            self.entered = threading.Event()
            self.release = threading.Event()

        def send_message(
            self,
            payload: dict[str, Any],
            *,
            protocol_version: str | None = None,
            operation_timeout_seconds: float | None = None,
        ) -> dict[str, Any]:
            self.entered.set()
            self.release.wait(timeout=2.0)
            return {"task": {"id": payload["message"]["messageId"]}}

        def get_task(
            self,
            task_id: str,
            *,
            history_length: int | None = None,
        ) -> dict[str, Any] | None:
            raise RuntimeError("/private/path bearer-value")

    bridge = BlockingBridge()
    port = _free_port()
    server, _thread = start_grpc_in_background(
        bridge,
        host="127.0.0.1",
        port=port,
        policy=A2AGrpcPolicy(max_concurrent_rpcs=1),
    )
    time.sleep(0.05)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            first = executor.submit(
                lambda: _send_with_client(
                    f"127.0.0.1:{port}",
                    _send_payload("first"),
                )
            )
            assert bridge.entered.wait(timeout=1.0)
            exhausted = _rpc_error(
                lambda: _send_with_client(
                    f"127.0.0.1:{port}",
                    _send_payload("second"),
                    timeout_seconds=1.0,
                )
            )
            assert exhausted.code().name == "RESOURCE_EXHAUSTED"
            bridge.release.set()
            assert first.result(timeout=1.0)["task"]["id"] == "first"

        with A2AGrpcClient(f"127.0.0.1:{port}") as client:
            recovered = client.send_message(_send_payload("recovered"))
            internal = _rpc_error(lambda: client.get_task("known"))
        assert recovered["task"]["id"] == "recovered"
        assert internal.code().name == "INTERNAL"
        assert internal.details() == "request failed"
        assert "/private/path" not in internal.details()
        assert "bearer-value" not in internal.details()
    finally:
        bridge.release.set()
        server.stop(grace=None)


def test_grpc_admission_recovers_after_client_deadline_expiry() -> None:
    class HangingAgent(RecordingAgent):
        def __init__(self) -> None:
            super().__init__()
            self.entered = threading.Event()
            self.cancelled = threading.Event()
            self.hang = True

        async def chat(
            self,
            content: str,
            target: str = "BROADCAST",
            *,
            metadata: dict[str, Any] | None = None,
        ) -> None:
            if not self.hang:
                return
            self.entered.set()
            try:
                await asyncio.Future()
            finally:
                self.cancelled.set()

    agent = HangingAgent()
    runtime = SynapseAgentRuntime(
        cast(SynapseAgent, agent),
        operation_timeout_seconds=1.0,
    )
    bridge = A2ABridge(
        agent=agent,
        agent_card={"name": "grpc-bridge"},
        target="WORKER",
        store=A2ATaskStore(),
        submit=runtime.run,
    )
    port = _free_port()
    try:
        runtime._thread.start()
        server, _thread = start_grpc_in_background(
            bridge,
            host="127.0.0.1",
            port=port,
            policy=A2AGrpcPolicy(max_concurrent_rpcs=1, max_rpc_seconds=1.0),
        )
        time.sleep(0.05)
        with ThreadPoolExecutor(max_workers=1) as executor:
            expired = executor.submit(
                lambda: _send_with_client(
                    f"127.0.0.1:{port}",
                    _send_payload("expires"),
                    timeout_seconds=0.05,
                )
            )
            assert agent.entered.wait(timeout=1.0)
            expired_error = _rpc_error(lambda: expired.result(timeout=1.0))
            assert expired_error.code().name == "DEADLINE_EXCEEDED"
            assert agent.cancelled.wait(timeout=1.0)

            agent.hang = False
            recovered = _send_with_client(
                f"127.0.0.1:{port}",
                _send_payload("after-expiry"),
                timeout_seconds=0.5,
            )
            assert recovered["task"]["id"]
    finally:
        if "server" in locals():
            server.stop(grace=None)
        runtime.stop()
