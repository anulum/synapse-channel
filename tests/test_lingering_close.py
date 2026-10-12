# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for ending a refused HTTP connection without a reset
"""Real connections: the client keeps its answer and sees an orderly end."""

from __future__ import annotations

import socket
import struct
import threading
import time
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler

import pytest

from synapse_channel.lingering_close import (
    LINGER_BYTES,
    LINGER_CONNECTIONS,
    LINGER_SECONDS,
    LingeringCloser,
)
from synapse_channel.prompt_bind_http import PromptBindThreadingHTTPServer


def _pair() -> tuple[socket.socket, socket.socket]:
    """Return a connected pair of real TCP sockets on the loopback: (server side, client side)."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    client = socket.create_connection(listener.getsockname(), timeout=5.0)
    served, _ = listener.accept()
    listener.close()
    return served, client


def _until(condition: Callable[[], bool], seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def _pending_error(sock: socket.socket) -> int:
    return sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)


@pytest.fixture
def closer() -> Iterator[LingeringCloser]:
    made = LingeringCloser(seconds=5.0)
    try:
        yield made
    finally:
        made.close()


def test_the_bounds_have_their_documented_defaults() -> None:
    made = LingeringCloser()

    assert (made.seconds, made.max_bytes, made.max_connections) == (
        LINGER_SECONDS,
        LINGER_BYTES,
        LINGER_CONNECTIONS,
    )
    assert (LINGER_SECONDS, LINGER_BYTES, LINGER_CONNECTIONS) == (2.0, 2 * 1024 * 1024, 64)
    assert made.pending == 0
    made.close()
    made.close()


def test_the_client_sees_the_end_of_the_answer_at_once_and_no_reset_after_late_bytes(
    closer: LingeringCloser,
) -> None:
    served, client = _pair()
    served.sendall(b"answer")

    assert closer.take(served) is True
    other, other_client = _pair()
    assert closer.take(other) is True and closer.pending == 2
    other_client.close()
    assert _until(lambda: closer.pending == 1)

    # The answer and its end reach the client although it has not sent everything yet.
    assert client.recv(64) == b"answer" and client.recv(64) == b""
    assert closer.pending == 1
    client.sendall(b"the rest of a request that nobody will read" * 100)
    time.sleep(0.2)
    assert _pending_error(client) == 0 and closer.pending == 1
    client.close()
    assert _until(lambda: closer.pending == 0)
    assert served.fileno() == -1


def test_a_connection_is_closed_at_its_deadline_when_the_client_never_closes() -> None:
    closer = LingeringCloser(seconds=0.3)
    served, client = _pair()
    try:
        started = time.monotonic()
        assert closer.take(served)
        assert closer.pending == 1

        assert _until(lambda: closer.pending == 0)

        assert 0.25 <= time.monotonic() - started < 3.0
        assert served.fileno() == -1
    finally:
        client.close()
        closer.close()


def test_a_connection_is_closed_once_it_sends_more_than_the_byte_bound() -> None:
    closer = LingeringCloser(seconds=30.0, max_bytes=10_000)
    served, client = _pair()
    try:
        assert closer.take(served)
        client.sendall(b"x" * 9_000)
        time.sleep(0.2)
        assert closer.pending == 1

        client.sendall(b"x" * 9_000)

        assert _until(lambda: closer.pending == 0)
        assert served.fileno() == -1
    finally:
        client.close()
        closer.close()


def test_a_connection_that_the_client_resets_is_closed_and_forgotten(
    closer: LingeringCloser,
) -> None:
    served, client = _pair()
    assert closer.take(served)

    # Closing with a zero linger time makes the client's own close a reset.
    client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    client.close()

    assert _until(lambda: closer.pending == 0)
    assert served.fileno() == -1


def test_a_connection_over_the_count_bound_is_left_to_the_caller() -> None:
    closer = LingeringCloser(seconds=30.0, max_connections=1)
    first, first_client = _pair()
    second, second_client = _pair()
    try:
        assert closer.take(first) is True

        assert closer.take(second) is False

        # The declined socket was not touched: it can still send.
        second.sendall(b"still open")
        assert second_client.recv(64) == b"still open"
        assert closer.pending == 1
    finally:
        for sock in (second, first_client, second_client):
            sock.close()
        closer.close()


def test_closing_the_closer_closes_what_waits_and_declines_what_comes_after() -> None:
    closer = LingeringCloser(seconds=30.0)
    served, client = _pair()
    late, late_client = _pair()
    gone, gone_client = _pair()
    try:
        assert closer.take(served) and closer.pending == 1
        gone.close()
        assert closer.take(gone) is False

        closer.close()

        assert closer.pending == 0 and served.fileno() == -1
        assert closer.take(late) is False and late.fileno() != -1
    finally:
        for sock in (client, late, late_client, gone_client):
            sock.close()


class _RefuseUnread(BaseHTTPRequestHandler):
    """Answer every POST with a refusal before reading its body."""

    def do_POST(self) -> None:
        body = b"refused before the body was read"
        self.send_response(403)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return None


def _read_answer(sock: socket.socket) -> bytes:
    data = b""
    while True:
        part = sock.recv(65536)
        if not part:
            return data
        data += part


@pytest.mark.parametrize("body_bytes", [200, 300_000])
def test_a_real_server_ends_a_refused_request_in_order_whenever_its_body_arrives(
    body_bytes: int,
) -> None:
    server = PromptBindThreadingHTTPServer(("127.0.0.1", 0), _RefuseUnread)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        head = (
            f"POST /x HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Length: {body_bytes}\r\n\r\n"
        ).encode("ascii")
        client = socket.create_connection(("127.0.0.1", port), timeout=5.0)
        try:
            client.sendall(head)
            answer = _read_answer(client)
            # The body goes out only now, after the refusal and its end were read.
            client.sendall(b"b" * body_bytes)
            time.sleep(0.3)

            assert answer.startswith(b"HTTP/1.0 403") and answer.endswith(
                b"refused before the body was read"
            )
            assert _pending_error(client) == 0
        finally:
            client.close()
        assert _until(lambda: server.lingering_closer.pending == 0)
    finally:
        server.shutdown()
        thread.join(timeout=5.0)
        server.server_close()
    # A closed server's closer declines even a healthy connection.
    served, late_client = _pair()
    try:
        assert server.lingering_closer.take(served) is False
    finally:
        served.close()
        late_client.close()


def test_closing_the_server_closes_a_connection_that_is_still_kept_open() -> None:
    """A client that never closes does not outlive the server by the linger time."""
    server = PromptBindThreadingHTTPServer(("127.0.0.1", 0), _RefuseUnread)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    client = socket.create_connection(("127.0.0.1", port), timeout=5.0)
    try:
        client.sendall(
            f"POST /x HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Length: 64\r\n\r\n".encode(
                "ascii"
            )
        )
        answer = _read_answer(client)
        kept = _until(lambda: server.lingering_closer.pending == 1)

        server.shutdown()
        thread.join(timeout=5.0)
        server.server_close()
        still_waiting = server.lingering_closer.pending
    finally:
        client.close()

    assert answer.startswith(b"HTTP/1.0 403")
    assert kept
    assert still_waiting == 0


def test_a_server_whose_closer_declines_closes_the_connection_at_once() -> None:
    server = PromptBindThreadingHTTPServer(("127.0.0.1", 0), _RefuseUnread)
    server.lingering_closer = LingeringCloser(max_connections=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        client = socket.create_connection(("127.0.0.1", port), timeout=5.0)
        try:
            client.sendall(
                f"POST /x HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Length: 0\r\n\r\n".encode(
                    "ascii"
                )
            )
            answer = _read_answer(client)
        finally:
            client.close()

        assert answer.startswith(b"HTTP/1.0 403") and server.lingering_closer.pending == 0
    finally:
        server.shutdown()
        thread.join(timeout=5.0)
        server.server_close()
