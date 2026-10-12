# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real-socket bounds for A2A HTTP concurrent admission and body reads

"""Real-socket coverage for A2A HTTP concurrent admission and read deadlines."""

from __future__ import annotations

import contextlib
import json
import select
import socket
import struct
import threading
import time
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from typing import Any

import pytest

from a2a_server_helpers import RecordingAgent
from synapse_channel.a2a_http import (
    DEFAULT_A2A_REQUEST_READ_TIMEOUT_SECONDS,
    DEFAULT_MAX_CONCURRENT_A2A_REQUESTS,
    A2AHTTPServer,
    make_a2a_http_server,
)
from synapse_channel.a2a_server import A2ABridge
from synapse_channel.a2a_store import A2ATaskStore


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


def _agent_card() -> dict[str, Any]:
    return {
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
    }


def _bridge(agent: Any | None = None) -> A2ABridge:
    return A2ABridge(
        agent=agent or RecordingAgent(),
        agent_card=_agent_card(),
        target="WORKER",
        store=A2ATaskStore(),
    )


@contextlib.contextmanager
def _running_server(
    bridge: A2ABridge,
    *,
    max_concurrent_requests: int = DEFAULT_MAX_CONCURRENT_A2A_REQUESTS,
    request_read_timeout_seconds: float = DEFAULT_A2A_REQUEST_READ_TIMEOUT_SECONDS,
) -> Any:
    port = _free_port()
    server = make_a2a_http_server(
        bridge=bridge,
        host="127.0.0.1",
        port=port,
        max_concurrent_requests=max_concurrent_requests,
        request_read_timeout_seconds=request_read_timeout_seconds,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, port
    finally:
        server.shutdown()
        server.server_close()
        with contextlib.suppress(RuntimeError):
            thread.join(timeout=2.0)


def _get_agent_card(port: int, *, timeout: float = 2.0) -> tuple[int, dict[str, Any]]:
    sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    try:
        sock.sendall(
            b"GET /.well-known/agent-card.json HTTP/1.1\r\n"
            b"Host: example.test\r\n"
            b"Connection: close\r\n"
            b"\r\n"
        )
        raw = _recv_http(sock, timeout=timeout)
    finally:
        sock.close()
    return _parse_http_response(raw)


def _recv_http(sock: socket.socket, *, timeout: float) -> bytes:
    sock.settimeout(timeout)
    chunks: list[bytes] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            part = sock.recv(65536)
        except TimeoutError:
            break
        if not part:
            break
        chunks.append(part)
        if b"\r\n\r\n" in b"".join(chunks):
            # Prefer full body when Content-Length is known; otherwise stop at headers.
            joined = b"".join(chunks)
            header, _, body = joined.partition(b"\r\n\r\n")
            length = None
            for line in header.split(b"\r\n")[1:]:
                if line.lower().startswith(b"content-length:"):
                    try:
                        length = int(line.split(b":", 1)[1].strip())
                    except ValueError:
                        length = None
                    break
            if length is None or len(body) >= length:
                break
    return b"".join(chunks)


def _parse_http_response(raw: bytes) -> tuple[int, dict[str, Any]]:
    header, _, body = raw.partition(b"\r\n\r\n")
    status_line = header.split(b"\r\n", 1)[0].decode("ascii", errors="replace")
    status = int(status_line.split(" ", 2)[1])
    if not body:
        return status, {}
    return status, json.loads(body.decode("utf-8"))


def _await_capacity_free(
    port: int,
    *,
    timeout: float = 5.0,
    probe: Callable[[int], tuple[int, dict[str, Any]]] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Poll the agent card until the admission slot is released, or ``timeout``.

    The server releases a slot in ``process_request_thread``'s ``finally`` — AFTER
    the handler has written the client's response — so the slot can still read as
    occupied for a brief window after the holding request's client has already seen
    its ``200``. On a loaded CI runner (notably macOS) that release lag can exceed
    the gap before the next probe, so a single immediate probe races and reads a
    transient ``503``. Polling distinguishes a merely lagging release (returns
    ``200`` and passes) from a genuinely leaked slot (never frees, times out red).
    """
    deadline = time.monotonic() + timeout
    while True:
        if probe is None:
            status, body = _get_agent_card(port, timeout=2.0)
        else:
            status, body = probe(port)
        if status == int(HTTPStatus.OK) or time.monotonic() >= deadline:
            return status, body
        time.sleep(0.02)


class _HoldOpenAgent(RecordingAgent):
    """Recording agent that blocks chat until an event is signalled."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    async def chat(
        self,
        payload: str,
        *,
        target: str = "all",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.entered.set()
        assert self.release.wait(timeout=5.0), "hold-open agent was not released"
        await super().chat(payload, target=target, metadata=metadata)


def _post_message_send(port: int, *, timeout: float = 2.0) -> tuple[int, dict[str, Any]]:
    body = json.dumps(
        {
            "message": {
                "messageId": "msg-bounds-1",
                "role": "ROLE_USER",
                "parts": [{"text": "hello-bounds"}],
            }
        }
    ).encode("utf-8")
    request = (
        b"POST /message:send HTTP/1.1\r\n"
        b"Host: example.test\r\n"
        b"Content-Type: application/json\r\n"
        + f"Content-Length: {len(body)}\r\n".encode("ascii")
        + b"Connection: close\r\n"
        + b"\r\n"
        + body
    )
    sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    try:
        sock.sendall(request)
        raw = _recv_http(sock, timeout=timeout)
    finally:
        sock.close()
    return _parse_http_response(raw)


def test_make_a2a_http_server_rejects_invalid_bounds() -> None:
    bridge = _bridge()
    with pytest.raises(ValueError, match="max_concurrent_requests"):
        make_a2a_http_server(bridge=bridge, host="127.0.0.1", port=0, max_concurrent_requests=0)
    with pytest.raises(ValueError, match="request_read_timeout_seconds"):
        make_a2a_http_server(
            bridge=bridge,
            host="127.0.0.1",
            port=0,
            request_read_timeout_seconds=0.0,
        )


def test_concurrent_capacity_refuses_with_503_and_releases() -> None:
    """A second connection is refused while the first occupies the only slot."""
    agent = _HoldOpenAgent()
    bridge = _bridge(agent)
    with _running_server(
        bridge,
        max_concurrent_requests=1,
        request_read_timeout_seconds=5.0,
    ) as (_server, port):
        holder_status: dict[str, Any] = {}
        holder_error: list[BaseException] = []

        def hold_request() -> None:
            try:
                status, body = _post_message_send(port, timeout=5.0)
                holder_status["status"] = status
                holder_status["body"] = body
            except BaseException as exc:  # pragma: no cover - diagnostic
                holder_error.append(exc)

        holder = threading.Thread(target=hold_request, daemon=True)
        holder.start()
        assert agent.entered.wait(timeout=2.0), "first request never entered the agent"

        refused_status, refused_body = _get_agent_card(port, timeout=2.0)
        assert refused_status == int(HTTPStatus.SERVICE_UNAVAILABLE)
        assert refused_body.get("title") == "Service Unavailable"
        error = refused_body.get("error") or {}
        details = error.get("details") or []
        reasons = [item.get("reason") for item in details if isinstance(item, dict)]
        assert "A2A_HTTP_CAPACITY_EXHAUSTED" in reasons

        agent.release.set()
        holder.join(timeout=5.0)
        assert not holder_error
        assert holder_status.get("status") == int(HTTPStatus.OK)

        # Capacity must be free after the holder finishes. The slot release runs
        # in the server thread's ``finally``, after the holder's client already saw
        # its 200, so allow a bounded release lag instead of racing a single probe
        # (the race surfaced as a transient 503 on loaded macOS CI runners).
        free_status, free_body = _await_capacity_free(port, timeout=5.0)
        assert free_status == int(HTTPStatus.OK)
        assert free_body.get("name") == "SYNAPSE CHANNEL"


_LATE_BODY_BYTES = 4096


def _refused_with_a_late_body(port: int, host: str) -> tuple[bytes, int]:
    """Send a request head, read the answer to its end, then send the body.

    Returns the raw answer and the error pending on the client's socket after
    the late body went out. A server that closed while request bytes were
    unread leaves a reset there; an orderly end leaves zero.
    """
    head = (
        "POST /message:send HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {_LATE_BODY_BYTES}\r\n"
        "\r\n"
    ).encode("ascii")
    sock = socket.create_connection(("127.0.0.1", port), timeout=5.0)
    try:
        sock.sendall(head)
        answer = b""
        while part := sock.recv(65536):
            answer += part
        sock.sendall(b" " * _LATE_BODY_BYTES)
        time.sleep(0.3)
        return answer, sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
    finally:
        sock.close()


def _framed(raw: bytes) -> tuple[int, dict[str, Any]]:
    """Parse an answer and require that its body is as long as it declares."""
    header, _, body = raw.partition(b"\r\n\r\n")
    declared = [
        int(line.split(b":", 1)[1])
        for line in header.split(b"\r\n")[1:]
        if line.lower().startswith(b"content-length:")
    ]
    assert declared == [len(body)]
    return _parse_http_response(raw)


def _reasons(body: dict[str, Any]) -> list[Any]:
    details = (body.get("error") or {}).get("details") or []
    return [item.get("reason") for item in details if isinstance(item, dict)]


def test_a_capacity_refusal_reaches_the_client_whole_and_ends_without_a_reset() -> None:
    """The refusal is complete and the connection ends in order, body unread."""
    agent = _HoldOpenAgent()
    with _running_server(
        _bridge(agent), max_concurrent_requests=1, request_read_timeout_seconds=5.0
    ) as (_server, port):
        outcome: list[tuple[int, dict[str, Any]]] = []
        holder = threading.Thread(
            target=lambda: outcome.append(_post_message_send(port, timeout=5.0)), daemon=True
        )
        holder.start()
        assert agent.entered.wait(timeout=2.0), "first request never entered the agent"

        answer, pending_error = _refused_with_a_late_body(port, "example.test")

        agent.release.set()
        holder.join(timeout=5.0)
        status, body = _framed(answer)
        assert status == int(HTTPStatus.SERVICE_UNAVAILABLE)
        assert "A2A_HTTP_CAPACITY_EXHAUSTED" in _reasons(body)
        assert pending_error == 0
        assert [item[0] for item in outcome] == [int(HTTPStatus.OK)]
        assert len(agent.messages) == 1


def test_a_host_refusal_reaches_the_client_whole_and_ends_without_a_reset() -> None:
    """A request refused before its body is answered whole; the body is never dispatched."""
    agent = RecordingAgent()
    bridge = _bridge(agent)
    bridge.allowed_authorities = ("example.test",)
    with _running_server(bridge) as (_server, port):
        answer, pending_error = _refused_with_a_late_body(port, "attacker.test")

        status, body = _framed(answer)
        assert status == int(HTTPStatus.FORBIDDEN)
        assert "ORIGIN_OR_HOST_NOT_ALLOWED" in _reasons(body)
        assert pending_error == 0
        assert agent.messages == []
        assert bridge.store.list_tasks() == []


class _FailsOnRequest(BaseHTTPRequestHandler):
    """Raise for ``/fail`` and answer every other path."""

    def do_GET(self) -> None:
        if self.path == "/fail":
            raise RuntimeError("handler failed on purpose")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, _format: str, *_args: object) -> None:
        return None


def _plain_get(port: int, path: str) -> bytes:
    sock = socket.create_connection(("127.0.0.1", port), timeout=5.0)
    try:
        sock.sendall(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n".encode("ascii"))
        answer = b""
        while part := sock.recv(65536):
            answer += part
        return answer
    finally:
        sock.close()


def test_a_failing_handler_is_reported_and_gives_its_slot_back(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A handler that raises ends its connection, is reported, and frees the only slot."""
    server = A2AHTTPServer(("127.0.0.1", 0), _FailsOnRequest, max_concurrent_requests=1)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]

        failed = _plain_get(port, "/fail")
        deadline = time.monotonic() + 5.0
        served = _plain_get(port, "/served")
        while not served.startswith(b"HTTP/1.0 200") and time.monotonic() < deadline:
            time.sleep(0.02)
            served = _plain_get(port, "/served")
    finally:
        server.shutdown()
        thread.join(timeout=5.0)
        server.server_close()

    assert failed == b""
    assert served.endswith(b"\r\n\r\nok")
    assert "handler failed on purpose" in capsys.readouterr().err


def test_a_capacity_refusal_to_a_client_that_already_reset_closes_the_connection() -> None:
    """The refusal cannot be written; the connection is closed and nothing is kept waiting."""
    agent = _HoldOpenAgent()
    with _running_server(
        _bridge(agent), max_concurrent_requests=1, request_read_timeout_seconds=5.0
    ) as (server, port):
        outcome: list[tuple[int, dict[str, Any]]] = []
        holder = threading.Thread(
            target=lambda: outcome.append(_post_message_send(port, timeout=5.0)), daemon=True
        )
        holder.start()
        assert agent.entered.wait(timeout=2.0), "first request never entered the agent"
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        client = socket.create_connection(listener.getsockname(), timeout=5.0)
        accepted, address = listener.accept()
        listener.close()
        # A zero linger time makes the client's close a reset, which the accepted side then holds.
        client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        client.close()
        # The reset makes the accepted side readable. The wait is shorter than the
        # holder's patience.
        select.select([accepted], [], [accepted], 2.0)

        server.process_request(accepted, address)

        # Where the reset already shows on shutdown the close is immediate; elsewhere the
        # closer meets it on its next pass.
        deadline = time.monotonic() + 3.0
        while (
            accepted.fileno() != -1 or server.lingering_closer.pending
        ) and time.monotonic() < deadline:
            time.sleep(0.01)
        closed = accepted.fileno() == -1
        waiting = server.lingering_closer.pending
        agent.release.set()
        holder.join(timeout=5.0)
        free_status, _ = _await_capacity_free(port, timeout=5.0)

    assert closed
    assert waiting == 0
    assert [item[0] for item in outcome] == [int(HTTPStatus.OK)]
    assert free_status == int(HTTPStatus.OK)


def test_await_capacity_free_tolerates_a_lagging_slot_release() -> None:
    """A slot that reads occupied then frees is polled through to the 200."""
    responses = iter(
        [
            (int(HTTPStatus.SERVICE_UNAVAILABLE), {"title": "Service Unavailable"}),
            (int(HTTPStatus.SERVICE_UNAVAILABLE), {"title": "Service Unavailable"}),
            (int(HTTPStatus.OK), {"name": "SYNAPSE CHANNEL"}),
        ]
    )

    def probe(_port: int) -> tuple[int, dict[str, Any]]:
        return next(responses)

    status, body = _await_capacity_free(0, timeout=5.0, probe=probe)

    assert status == int(HTTPStatus.OK)
    assert body.get("name") == "SYNAPSE CHANNEL"


def test_await_capacity_free_gives_up_on_a_leaked_slot() -> None:
    """A slot that never frees times out red rather than hanging or masking."""

    def probe(_port: int) -> tuple[int, dict[str, Any]]:
        return int(HTTPStatus.SERVICE_UNAVAILABLE), {"title": "Service Unavailable"}

    status, body = _await_capacity_free(0, timeout=0.05, probe=probe)

    assert status == int(HTTPStatus.SERVICE_UNAVAILABLE)
    assert body.get("title") == "Service Unavailable"


def test_incomplete_slow_body_times_out_without_long_sleep() -> None:
    """A declared Content-Length that never finishes is refused with 408 promptly."""
    bridge = _bridge()
    with _running_server(
        bridge,
        max_concurrent_requests=4,
        request_read_timeout_seconds=0.35,
    ) as (_server, port):
        sock = socket.create_connection(("127.0.0.1", port), timeout=2.0)
        try:
            # Announce 64 body bytes but send only a tiny prefix, then stall.
            headers = (
                b"POST /message:send HTTP/1.1\r\n"
                b"Host: example.test\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: 64\r\n"
                b"Connection: close\r\n"
                b"\r\n"
                b'{"'
            )
            started = time.monotonic()
            sock.sendall(headers)
            raw = _recv_http(sock, timeout=2.0)
            elapsed = time.monotonic() - started
        finally:
            sock.close()

        status, body = _parse_http_response(raw)
        assert status == int(HTTPStatus.REQUEST_TIMEOUT)
        assert body.get("title") == "Request Timeout"
        error = body.get("error") or {}
        details = error.get("details") or []
        reasons = [item.get("reason") for item in details if isinstance(item, dict)]
        assert "A2A_HTTP_READ_TIMEOUT" in reasons
        # Bound must fire near the configured deadline, not a multi-second hang.
        assert elapsed < 1.5


def test_a2a_http_server_exposes_configured_limits() -> None:
    bridge = _bridge()
    server = make_a2a_http_server(
        bridge=bridge,
        host="127.0.0.1",
        port=0,
        max_concurrent_requests=7,
        request_read_timeout_seconds=1.25,
    )
    try:
        assert isinstance(server, A2AHTTPServer)
        assert server.max_concurrent_requests == 7
        assert server.request_read_timeout_seconds == pytest.approx(1.25)
    finally:
        server.server_close()
