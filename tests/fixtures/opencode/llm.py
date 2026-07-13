# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — deterministic OpenCode acceptance provider
"""Serve queued OpenAI-compatible SSE responses to real OpenCode processes."""

from __future__ import annotations

import json
import queue
import threading
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

_MAX_REQUEST_BYTES = 1_048_576
_TITLE_SYSTEM_PREFIX = "You are a title generator."


def _chunk(
    *,
    delta: Mapping[str, Any] | None = None,
    finish: str = "",
    usage: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    choice: dict[str, Any] = {"delta": dict(delta or {})}
    if finish:
        choice["finish_reason"] = finish
    result: dict[str, Any] = {
        "id": "chatcmpl-synapse-opencode",
        "object": "chat.completion.chunk",
        "choices": [choice],
    }
    if usage is not None:
        result["usage"] = {
            "prompt_tokens": usage["input"],
            "completion_tokens": usage["output"],
            "total_tokens": usage["input"] + usage["output"],
        }
    return result


def _text_script(text: str) -> tuple[dict[str, Any], ...]:
    return (
        _chunk(delta={"role": "assistant"}),
        _chunk(delta={"content": text}),
        _chunk(finish="stop", usage={"input": 2, "output": 1}),
    )


def _tool_script(name: str, arguments: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    return (
        _chunk(
            delta={
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_synapse_guard",
                        "type": "function",
                        "function": {"name": name, "arguments": ""},
                    }
                ],
            }
        ),
        _chunk(
            delta={
                "tool_calls": [
                    {
                        "index": 0,
                        "function": {"arguments": json.dumps(arguments, separators=(",", ":"))},
                    }
                ]
            }
        ),
        _chunk(finish="tool_calls", usage={"input": 2, "output": 1}),
    )


def _is_title_request(request: Mapping[str, Any]) -> bool:
    """Return whether OpenCode is asking its hidden agent to title a session."""
    messages = request.get("messages")
    if not isinstance(messages, list) or not messages:
        return False
    first = messages[0]
    if not isinstance(first, dict):
        return False
    content = first.get("content")
    return isinstance(content, str) and content.startswith(_TITLE_SYSTEM_PREFIX)


class ScriptedLlmServer:
    """A real HTTP/SSE provider endpoint with an explicit response queue."""

    def __init__(self) -> None:
        self._scripts: queue.Queue[tuple[dict[str, Any], ...]] = queue.Queue()
        self._requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                if self.path != "/v1/chat/completions":
                    self.send_error(404)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self.send_error(400)
                    return
                if length <= 0 or length > _MAX_REQUEST_BYTES:
                    self.send_error(413)
                    return
                try:
                    decoded = json.loads(self.rfile.read(length))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    self.send_error(400)
                    return
                if not isinstance(decoded, dict):
                    self.send_error(400)
                    return
                with owner._lock:
                    owner._requests.append(decoded)
                if _is_title_request(decoded):
                    script = _text_script("Synapse OpenCode acceptance")
                else:
                    try:
                        script = owner._scripts.get(timeout=5)
                    except queue.Empty:
                        self.send_error(503)
                        return
                payload = (
                    b"".join(
                        f"data: {json.dumps(chunk, separators=(',', ':'))}\n\n".encode()
                        for chunk in script
                    )
                    + b"data: [DONE]\n\n"
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args: object) -> None:
                del format, args

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        """Return the OpenAI-compatible base URL."""
        address = self._server.server_address
        host, port = str(address[0]), int(address[1])
        return f"http://{host}:{port}/v1"

    @property
    def requests(self) -> tuple[dict[str, Any], ...]:
        """Return a stable snapshot of provider request bodies."""
        with self._lock:
            return tuple(self._requests)

    @property
    def prompt_requests(self) -> tuple[dict[str, Any], ...]:
        """Return build-agent requests, excluding automatic title generation."""
        return tuple(request for request in self.requests if not _is_title_request(request))

    def enqueue_text(self, text: str) -> None:
        """Queue one assistant text completion."""
        self._scripts.put(_text_script(text))

    def enqueue_tool(self, name: str, arguments: Mapping[str, Any]) -> None:
        """Queue one assistant tool call completion."""
        self._scripts.put(_tool_script(name, arguments))

    def __enter__(self) -> ScriptedLlmServer:
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        del exc_info
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def provider_config(llm_url: str) -> dict[str, Any]:
    """Return the source-verified OpenCode test-provider configuration."""
    return {
        "formatter": False,
        "lsp": False,
        "provider": {
            "test": {
                "name": "Test",
                "id": "test",
                "env": [],
                "npm": "@ai-sdk/openai-compatible",
                "models": {
                    "test-model": {
                        "id": "test-model",
                        "name": "Test Model",
                        "attachment": False,
                        "reasoning": False,
                        "temperature": False,
                        "tool_call": True,
                        "release_date": "2025-01-01",
                        "limit": {"context": 100_000, "output": 10_000},
                        "cost": {"input": 0, "output": 0},
                        "options": {},
                    }
                },
                "options": {"apiKey": "test-key", "baseURL": llm_url},
            }
        },
    }
