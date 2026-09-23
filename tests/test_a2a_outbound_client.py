# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — outbound A2A client against a second peer process
"""Dual-process outbound client: discover → send → get against live serve."""

from __future__ import annotations

import json
import stat
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from a2a_server_helpers import RecordingAgent, _default_bridge, _free_port
from synapse_channel import a2a_client as a2a_client_module
from synapse_channel import cli, cli_a2a_client
from synapse_channel.a2a_client import (
    A2AClientError,
    A2AOutboundClient,
    join_endpoint,
    parse_a2a_endpoint,
    resolve_grpc_interface_url,
)
from synapse_channel.a2a_credentials import A2APlaintextBearerError
from synapse_channel.a2a_http import build_a2a_handler
from synapse_channel.a2a_outbound_response import (
    A2A_MAX_RESPONSE_BYTES,
    A2AReceiptWriteError,
)
from synapse_channel.a2a_server import A2ABridge
from synapse_channel.a2a_store import A2ATaskStore


def _serve(bridge: A2ABridge) -> tuple[ThreadingHTTPServer, int]:
    port = _free_port()
    bridge.allowed_authorities = (f"127.0.0.1:{port}",)
    interfaces = bridge.agent_card.get("supportedInterfaces")
    if isinstance(interfaces, list) and interfaces and isinstance(interfaces[0], dict):
        interfaces[0]["url"] = f"http://127.0.0.1:{port}"
    server = ThreadingHTTPServer(("127.0.0.1", port), build_a2a_handler(bridge))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, port


def test_parse_a2a_endpoint_https() -> None:
    scheme, host, port, prefix = parse_a2a_endpoint("https://127.0.0.1:9443/a2a")
    assert scheme == "https"
    assert host == "127.0.0.1"
    assert port == 9443
    assert prefix == "/a2a"


def test_a2a_endpoint_rejects_non_http_scheme_and_joins_peer_route() -> None:
    with pytest.raises(ValueError, match="supports http"):
        parse_a2a_endpoint("file:///private/peer")
    assert join_endpoint("https://peer.example/a2a", "/tasks/t-1") == (
        "https://peer.example/a2a/tasks/t-1"
    )


def test_agent_card_grpc_discovery_selects_usable_interface() -> None:
    card = {
        "supportedInterfaces": [
            None,
            {"protocolBinding": "HTTP+JSON", "url": "https://peer.example/json"},
            {"protocolBinding": "gRPC", "url": ""},
            {"protocolBinding": "HTTP+GRPC", "url": "https://peer.example/grpc"},
            {"protocolBinding": "GRPC+PROTO", "url": "https://peer.example/second"},
        ]
    }
    assert resolve_grpc_interface_url(card) == "https://peer.example/grpc"
    assert resolve_grpc_interface_url({"supportedInterfaces": "invalid"}) is None
    assert (
        resolve_grpc_interface_url({"supportedInterfaces": card["supportedInterfaces"][:3]}) is None
    )


def test_outbound_client_refuses_bearer_to_named_plaintext_peer() -> None:
    with pytest.raises(A2APlaintextBearerError, match="literal loopback"):
        A2AOutboundClient("http://peer.example:8877", token="never echoed")


def test_outbound_client_explicit_plaintext_override_allows_configuration() -> None:
    client = A2AOutboundClient(
        "http://peer.example:8877",
        token="secret",
        allow_insecure_http=True,
    )
    assert client.host == "peer.example"


def test_outbound_request_wraps_streamed_oversize_without_echoing_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = b"peer-secret-response"

    class Response:
        status = 200
        headers: dict[str, str] = {}

        def read(self, amount: int) -> bytes:
            return (secret * ((amount // len(secret)) + 1))[:amount]

    class Connection:
        def request(self, *_args: object, **_kwargs: object) -> None:
            return None

        def getresponse(self) -> Response:
            return Response()

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "synapse_channel.a2a_client.http.client.HTTPConnection",
        lambda *_args, **_kwargs: Connection(),
    )

    with pytest.raises(A2AClientError) as caught:
        a2a_client_module._request(
            scheme="http",
            host="127.0.0.1",
            port=8877,
            method="GET",
            path="/agent-card.json",
        )
    assert str(A2A_MAX_RESPONSE_BYTES) in str(caught.value)
    assert secret.decode() not in str(caught.value)


def test_outbound_status_error_does_not_echo_a_json_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = A2AOutboundClient("http://127.0.0.1:8877")
    monkeypatch.setattr(
        a2a_client_module,
        "_request",
        lambda **_kwargs: (502, {"detail": "peer-secret-detail"}),
    )

    with pytest.raises(A2AClientError) as caught:
        client.get_agent_card()
    assert str(caught.value) == ("agent-card discovery failed: HTTP 502 response_kind=object")
    assert "peer-secret-detail" not in str(caught.value)


def test_outbound_client_discover_send_get_twice() -> None:
    bridge = A2ABridge(
        agent=RecordingAgent(),
        agent_card=_default_bridge().agent_card,
        target="WORKER",
        store=A2ATaskStore(),
    )
    server, port = _serve(bridge)
    endpoint = f"http://127.0.0.1:{port}"
    client = A2AOutboundClient(endpoint, timeout=5.0)
    receipts = []
    try:
        for index in (1, 2):
            text = f"outbound-probe-{index}"
            receipt = client.discover_send_get(text)
            receipts.append(receipt)
            assert receipt["scheme"] == "http"
            assert receipt["task_id"]
            assert receipt["message_text"] == text
            task = receipt["task"]
            assert isinstance(task, dict)
            assert task.get("id") == receipt["task_id"]
            # Payload content present on history or status message
            blob = json.dumps(task)
            assert text in blob or text in json.dumps(receipt["send_response"])
    finally:
        server.shutdown()
        server.server_close()
    assert receipts[0]["task_id"] != receipts[1]["task_id"]
    assert any("outbound-probe-1" in text for _t, text in bridge.agent.messages)
    assert any("outbound-probe-2" in text for _t, text in bridge.agent.messages)


def test_real_peer_direct_message_and_step_failures_do_not_leak_peer_body() -> None:
    """A local HTTP peer may return a direct message or fail at each client step."""
    state: dict[str, object] = {"mode": "direct", "requests": []}

    class Peer(BaseHTTPRequestHandler):
        def _reply(self, status: int, body: dict[str, object]) -> None:
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:
            requests = state["requests"]
            assert isinstance(requests, list)
            requests.append((self.path, self.headers.get("Authorization")))
            route = self.path.removeprefix("/prefix")
            if route == "/.well-known/agent-card.json":
                status = 502 if state["mode"] == "bad-card" else 200
                self._reply(status, {"name": "Peer", "detail": "private-peer-detail"})
            elif route == "/tasks/task-1":
                self._reply(404, {"detail": "private-task-detail"})
            else:
                self._reply(404, {"detail": "unexpected path"})

        def do_POST(self) -> None:
            size = int(self.headers["Content-Length"])
            request = json.loads(self.rfile.read(size))
            requests = state["requests"]
            assert isinstance(requests, list)
            requests.append((self.path, self.headers.get("Authorization"), request))
            route = self.path.removeprefix("/prefix")
            if route != "/message:send":
                self._reply(404, {"detail": "unexpected path"})
            elif state["mode"] == "bad-send":
                self._reply(502, {"detail": "private-send-detail"})
            elif state["mode"] == "bad-get":
                self._reply(200, {"task": {"id": "task-1"}})
            else:
                self._reply(200, {"message": {"messageId": "reply-1"}})

        def log_message(self, _format: str, *_args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Peer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = A2AOutboundClient(f"http://127.0.0.1:{server.server_port}", token="local-test-token")
    try:
        direct = client.discover_send_get("review", message_id="request-1")
        assert direct["agent_card_name"] == "Peer"
        assert direct["task_id"] is None
        assert direct["task"] is None
        assert direct["send_response"]["message"]["messageId"] == "reply-1"
        requests = state["requests"]
        assert isinstance(requests, list)
        assert len(requests) == 2
        assert requests[0] == ("/.well-known/agent-card.json", None)
        assert requests[1][0:2] == ("/message:send", "Bearer local-test-token")
        assert requests[1][2]["message"]["messageId"] == "request-1"

        sent = client.send_message(
            "review-metadata", message_id="request-metadata", metadata={"trace_id": "local-1"}
        )
        assert sent["message"]["messageId"] == "reply-1"
        assert requests[2][2]["message"]["metadata"] == {"trace_id": "local-1"}

        prefixed = A2AOutboundClient(f"{client.endpoint_url}/prefix", token="local-test-token")
        assert prefixed.discover_send_get("prefixed")["task_id"] is None
        assert requests[3][0] == "/prefix/.well-known/agent-card.json"
        assert requests[4][0] == "/prefix/message:send"

        for mode, phrase in (
            ("bad-card", "agent-card discovery failed"),
            ("bad-send", "message:send failed"),
            ("bad-get", "GET task failed"),
        ):
            state["mode"] = mode
            with pytest.raises(A2AClientError) as caught:
                client.discover_send_get("review", message_id="request-2")
            assert phrase in str(caught.value)
            assert "private-" not in str(caught.value)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert not thread.is_alive()


def test_cli_a2a_client_writes_receipt(tmp_path: Path) -> None:
    bridge = A2ABridge(
        agent=RecordingAgent(),
        agent_card=_default_bridge().agent_card,
        target="WORKER",
        store=A2ATaskStore(),
        auth_token="file-bearer",
    )
    server, port = _serve(bridge)
    out = tmp_path / "outbound.json"
    token_file = tmp_path / "a2a.token"
    token_file.write_text("file-bearer\n", encoding="utf-8")
    token_file.chmod(0o600)
    try:
        code = cli.main(
            [
                "a2a-client",
                "--endpoint-url",
                f"http://127.0.0.1:{port}",
                "--a2a-token-file",
                str(token_file),
                "--message",
                "cli-outbound",
                "--output",
                str(out),
            ]
        )
    finally:
        server.shutdown()
        server.server_close()
    assert code == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["task_id"]
    assert data["message_text"] == "cli-outbound"
    if sys.platform != "win32":
        assert stat.S_IMODE(out.stat().st_mode) == 0o600


def test_cli_a2a_client_reports_bounded_receipt_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class Client:
        def discover_send_get(self, _message: str) -> dict[str, object]:
            return {"task_id": "peer-secret-task", "send_response": {}}

    monkeypatch.setattr(
        cli_a2a_client,
        "A2AOutboundClient",
        lambda *_args, **_kwargs: Client(),
    )
    monkeypatch.setattr(
        cli_a2a_client,
        "write_a2a_receipt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            A2AReceiptWriteError("A2A receipt write failed")
        ),
    )

    code = cli.main(
        [
            "a2a-client",
            "--endpoint-url",
            "http://127.0.0.1:8877",
            "--output",
            str(tmp_path / "receipt.json"),
        ]
    )
    assert code == 1
    error = capsys.readouterr().err
    assert error == "a2a-client: A2A receipt write failed\n"
    assert "peer-secret-task" not in error


def test_cli_a2a_client_rejects_non_finite_stdout_receipt(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class Client:
        def discover_send_get(self, _message: str) -> dict[str, object]:
            return {"task_id": "peer-secret-task", "value": float("inf")}

    monkeypatch.setattr(
        cli_a2a_client,
        "A2AOutboundClient",
        lambda *_args, **_kwargs: Client(),
    )

    code = cli.main(["a2a-client", "--endpoint-url", "http://127.0.0.1:8877"])

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert captured.err == "a2a-client: A2A receipt serialization failed\n"
    assert "peer-secret-task" not in captured.err


def test_cli_a2a_client_contains_pre_temp_receipt_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class Client:
        def discover_send_get(self, _message: str) -> dict[str, object]:
            return {"task_id": "peer-secret-task", "send_response": {}}

    monkeypatch.setattr(
        cli_a2a_client,
        "A2AOutboundClient",
        lambda *_args, **_kwargs: Client(),
    )
    occupied = tmp_path / "occupied"
    occupied.write_text("unchanged", encoding="utf-8")

    code = cli.main(
        [
            "a2a-client",
            "--endpoint-url",
            "http://127.0.0.1:8877",
            "--output",
            str(occupied / "receipt.json"),
        ]
    )

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert captured.err == "a2a-client: A2A receipt write failed\n"
    assert "peer-secret-task" not in captured.err
    assert occupied.read_text(encoding="utf-8") == "unchanged"


def test_cli_a2a_client_refuses_remote_plaintext_bearer_before_io(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = cli.main(
        [
            "a2a-client",
            "--endpoint-url",
            "http://peer.example:8877",
            "--a2a-token",
            "never-print-this-bearer",
        ]
    )
    assert code == 2
    error = capsys.readouterr().err
    assert "plaintext HTTP" in error
    assert "never-print-this-bearer" not in error


def test_cli_a2a_client_parser_exposes_safe_file_and_unsafe_override() -> None:
    args = cli.build_parser().parse_args(
        [
            "a2a-client",
            "--endpoint-url",
            "https://peer.example",
            "--a2a-token-file",
            "/run/secrets/a2a",
            "--a2a-allow-insecure-http",
        ]
    )
    assert args.a2a_token_file == "/run/secrets/a2a"
    assert args.a2a_allow_insecure_http is True
