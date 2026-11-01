# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a2a-client exit codes against a real loopback peer
"""A failed discovery and an empty send response both exit 1 from the real CLI."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from synapse_channel import cli


@contextmanager
def _peer(card_status: int) -> Iterator[tuple[str, list[str]]]:
    """Serve an agent card with ``card_status`` and answer every send with ``{}``."""
    seen: list[str] = []

    class Peer(BaseHTTPRequestHandler):
        def _reply(self, status: int, body: dict[str, object]) -> None:
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:
            seen.append(f"GET {self.path}")
            self._reply(card_status, {"name": "Peer", "detail": "private-card-detail"})

        def do_POST(self) -> None:
            self.rfile.read(int(self.headers["Content-Length"]))
            seen.append(f"POST {self.path}")
            self._reply(200, {})

        def log_message(self, _format: str, *_args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Peer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_failed_discovery_exits_1_without_echoing_the_peer_body(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A 502 agent card stops the client before any send; stderr names only the step."""
    with _peer(502) as (url, seen):
        code = cli.main(["a2a-client", "--endpoint-url", url, "--message", "probe"])
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert (
        captured.err == "a2a-client: agent-card discovery failed: HTTP 502 response_kind=object\n"
    )
    assert seen == ["GET /.well-known/agent-card.json"]


def test_send_without_task_or_message_prints_the_receipt_and_exits_1(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A peer that accepts the send but returns neither task nor message is not success."""
    with _peer(200) as (url, seen):
        code = cli.main(["a2a-client", "--endpoint-url", url, "--message", "probe"])
    captured = capsys.readouterr()
    assert code == 1
    assert captured.err == ""
    receipt = json.loads(captured.out)
    assert receipt["agent_card_name"] == "Peer"
    assert receipt["task_id"] is None
    assert receipt["send_response"] == {}
    assert seen == ["GET /.well-known/agent-card.json", "POST /message:send"]
