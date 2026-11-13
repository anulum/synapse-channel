# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — an ordinary dashboard response whose reader has left
"""A page that leaves while the dashboard writes to it is not an error of the server."""

from __future__ import annotations

import socket
import struct
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from dashboard_helpers import _feeds_server, _http_get

_BODY_BYTES = 32 * 1024 * 1024
"""Larger than any loopback socket buffer, so the write is still running when the reader resets."""


def _request_threads() -> list[threading.Thread]:
    """Return the live threads that serve one dashboard request each."""
    return [thread for thread in threading.enumerate() if "process_request_thread" in thread.name]


def _await_request_threads(deadline_seconds: float = 10.0) -> None:
    """Wait until every request thread has ended, so what they printed is complete."""
    deadline = time.monotonic() + deadline_seconds
    while _request_threads() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert _request_threads() == []


def test_a_reader_that_left_mid_body_ends_the_response_quietly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<title>cockpit</title>", encoding="utf-8")
    (dist / "app.js").write_bytes(b"/*" + b"x" * _BODY_BYTES + b"*/")
    server = _feeds_server(cockpit_dist=dist)
    try:
        address = urlsplit(server.url("/"))
        assert address.hostname is not None and address.port is not None
        with socket.create_connection((address.hostname, address.port), timeout=5.0) as reader:
            reader.sendall(
                f"GET /cockpit/app.js HTTP/1.1\r\nHost: {address.netloc}\r\n\r\n".encode()
            )
            # The response has begun; the page leaves with a reset, as a closed tab does.
            assert reader.recv(16).startswith(b"HTTP/1.")
            reader.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        # The same server still serves the next reader.
        status, _, body = _http_get(server.url("/cockpit/"))
        _await_request_threads()
    finally:
        server.close()

    assert (status, body) == (200, "<title>cockpit</title>")
    assert capsys.readouterr().err == ""
