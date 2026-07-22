# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the reverse-DNS-free threading HTTP server bind
"""Tests for the reverse-DNS-free threading HTTP server bind."""

from __future__ import annotations

import socket
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler

import pytest

from synapse_channel.prompt_bind_http import PromptBindThreadingHTTPServer


class _OkHandler(BaseHTTPRequestHandler):
    """Minimal handler that answers 200 and logs nothing."""

    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, _format: str, *_args: object) -> None:
        return None


def test_bind_records_host_and_never_reverse_resolves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """server_bind sets ``server_name`` to the host without calling getfqdn.

    ``socket.getfqdn`` is patched to fail, so a bind that still reaches the stock
    ``HTTPServer.server_bind`` reverse-DNS path would raise; the prompt bind must
    complete and record the loopback host verbatim.
    """

    def _forbidden(_host: str = "") -> str:
        raise AssertionError("server_bind must not call socket.getfqdn")

    monkeypatch.setattr(socket, "getfqdn", _forbidden)

    server = PromptBindThreadingHTTPServer(("127.0.0.1", 0), _OkHandler)
    try:
        assert server.server_name == "127.0.0.1"
        assert server.server_port == server.server_address[1]
        assert server.server_port > 0
    finally:
        server.server_close()


def test_prompt_bound_server_serves_requests() -> None:
    """A prompt-bound server still accepts connections and answers 200."""
    server = PromptBindThreadingHTTPServer(("127.0.0.1", 0), _OkHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        with urllib.request.urlopen(  # nosec B310 - fixed loopback test URL
            f"http://127.0.0.1:{port}/", timeout=2.0
        ) as response:
            assert response.status == 200
            assert response.read() == b"ok"
    finally:
        server.shutdown()
        thread.join(timeout=5.0)
        server.server_close()
