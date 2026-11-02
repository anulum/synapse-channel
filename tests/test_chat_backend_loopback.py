# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — OpenAI-compatible chat backend against a real loopback server
"""A body that is not JSON, or whose content is not text, is a parse error, never a reply."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from synapse_channel.client.chat_backends import OpenAIChatClient


@contextmanager
def _server(body: bytes) -> Iterator[tuple[str, list[dict[str, object]]]]:
    """Answer every completion request with ``body`` and record the request JSON."""
    requests: list[dict[str, object]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize(
    ("body", "cause"),
    [
        (b"not json {", json.JSONDecodeError),
        (json.dumps({"choices": [{"message": {"content": 7}}]}).encode(), type(None)),
        (json.dumps({"choices": [{"message": {"content": None}}]}).encode(), type(None)),
    ],
    ids=["not-json", "numeric-content", "null-content"],
)
def test_unparsable_or_non_text_completion_is_a_parse_error(body: bytes, cause: type) -> None:
    """The client raises one stable error and never returns the peer's non-text value."""
    with _server(body) as (url, requests):
        client = OpenAIChatClient(api_key="local", model="m", base_url=url, timeout_seconds=3)
        with pytest.raises(RuntimeError, match="^chat backend response parse error$") as caught:
            client.generate(system_prompt="system", user_prompt="question")
    assert type(caught.value.__cause__) is cause
    assert len(requests) == 1
    assert requests[0]["model"] == "m"


def test_text_completion_is_returned_sanitised() -> None:
    """The control: a text completion from the same server is returned."""
    body = json.dumps({"choices": [{"message": {"content": "  an   answer \n"}}]}).encode()
    with _server(body) as (url, _requests):
        client = OpenAIChatClient(api_key="local", model="m", base_url=url, timeout_seconds=3)
        assert client.generate(system_prompt="system", user_prompt="question") == "an answer"
