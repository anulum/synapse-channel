# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real-loopback tests for the REMANENTIA memory adapter
"""Pin bounded HTTP, token-file, schema, redaction, and honesty behavior."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast

import pytest

from synapse_channel.participants.remanentia_http import (
    MAX_TOKEN_BYTES,
    RemanentiaHttpRecall,
)


@dataclass
class _Plan:
    body: bytes = b'{"query":"q","results":[]}'
    status: int = 200
    content_type: str = "application/json"
    delay: float = 0.0
    location: str | None = None
    requests: list[dict[str, object]] = field(default_factory=list)


@dataclass(frozen=True)
class _LiveServer:
    url: str
    plan: _Plan


@pytest.fixture
def live_server() -> Iterator[_LiveServer]:
    plan = _Plan()

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            plan.requests.append(
                {
                    "path": self.path,
                    "headers": dict(self.headers.items()),
                    "body": body,
                }
            )
            if plan.delay:
                time.sleep(plan.delay)
            self.send_response(plan.status)
            self.send_header("Content-Type", plan.content_type)
            if plan.location is not None:
                self.send_header("Location", plan.location)
            self.send_header("Content-Length", str(len(plan.body)))
            self.end_headers()
            try:
                self.wfile.write(plan.body)
            except BrokenPipeError:
                pass

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield _LiveServer(f"http://127.0.0.1:{server.server_port}", plan)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


def _response(query: str = "q", results: list[object] | None = None) -> bytes:
    return json.dumps(
        {"query": query, "results": [] if results is None else results},
        ensure_ascii=False,
    ).encode("utf-8")


def _entry(**changes: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "name": "memory.md",
        "type": "semantic",
        "score": 0.75,
        "snippet": "known memory",
        "presentation": "validated",
    }
    entry.update(changes)
    return entry


@pytest.mark.asyncio
async def test_authenticated_recall_uses_fixed_path_and_floors_every_hit(
    live_server: _LiveServer, tmp_path: Path
) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("top-secret\n", encoding="utf-8")
    live_server.plan.body = _response("q", [_entry(), _entry(name="second.md", score=None)])
    recall = RemanentiaHttpRecall(live_server.url, token_file=token_file)

    result = await recall.recall("q", top_k=2)

    assert result.abstained is False
    assert [hit.presentation for hit in result.hits] == ["boundary", "boundary"]
    assert [hit.score for hit in result.hits] == [0.75, None]
    assert "omit honesty axes" in result.note
    request = live_server.plan.requests[0]
    assert request["path"] == "/recall"
    assert json.loads(cast(bytes, request["body"])) == {"query": "q", "top_k": 2}
    headers = cast(dict[str, str], request["headers"])
    assert headers["Authorization"] == "Bearer top-secret"
    assert "top-secret" not in repr(recall)


@pytest.mark.asyncio
async def test_no_token_and_no_hits_are_an_explicit_abstention(live_server: _LiveServer) -> None:
    result = await RemanentiaHttpRecall(live_server.url).recall("q", top_k=1)
    assert result.hits == ()
    assert result.abstained is True
    assert "no admissible hits" in result.note
    headers = cast(dict[str, str], live_server.plan.requests[0]["headers"])
    assert "Authorization" not in headers


@pytest.mark.asyncio
async def test_response_hit_count_is_capped_and_nonfinite_scores_disappear(
    live_server: _LiveServer,
) -> None:
    live_server.plan.body = (
        b'{"query":"q","results":['
        b'{"name":"one","type":"trace","score":1e400,"snippet":"a"},'
        b'{"name":"two","type":"trace","score":0.2,"snippet":"b"}]}'
    )
    result = await RemanentiaHttpRecall(live_server.url).recall("q", top_k=1)
    assert len(result.hits) == 1
    assert result.hits[0].score is None


@pytest.mark.parametrize(
    "url",
    [
        "",
        "ftp://example.test",
        "http:///missing-host",
        "http://memory.example.test",
        "https://user:pass@example.test",
        "https://example.test/base",
        "https://example.test?query=1",
        "https://example.test#fragment",
        "http://example.test:bad-port",
        " http://127.0.0.1:8001",
        "http://127.0.0.1:8001 ",
    ],
)
def test_url_must_be_a_credential_free_origin(url: str) -> None:
    with pytest.raises(ValueError, match="memory URL"):
        RemanentiaHttpRecall(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.2:8001",
        "http://[::1]:8001",
        "https://memory.example.test",
    ],
)
def test_transport_allows_literal_loopback_http_or_any_https_origin(url: str) -> None:
    assert RemanentiaHttpRecall(url).endpoint.endswith("/recall")


@pytest.mark.parametrize(
    "changes",
    [
        {"timeout_seconds": True},
        {"timeout_seconds": "2"},
        {"timeout_seconds": 0},
        {"timeout_seconds": 31},
        {"timeout_seconds": float("nan")},
        {"max_request_bytes": True},
        {"max_request_bytes": 127},
        {"max_request_bytes": 1024 * 1024 + 1},
        {"max_request_bytes": 128.5},
        {"max_response_bytes": True},
        {"max_response_bytes": 255},
        {"max_response_bytes": 8 * 1024 * 1024 + 1},
        {"max_response_bytes": 256.5},
    ],
)
def test_transport_bounds_are_strict(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        RemanentiaHttpRecall(
            "http://127.0.0.1:8001",
            **changes,  # type: ignore[arg-type]  # malformed values exercise runtime validation
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "top_k"),
    [("", 1), ("  ", 1), (cast(str, 1), 1), ("q", True), ("q", 0), ("q", 21), ("q", 1.5)],
)
async def test_query_and_top_k_are_strict(query: str, top_k: object) -> None:
    recall = RemanentiaHttpRecall("http://127.0.0.1:8001")
    with pytest.raises(ValueError):
        await recall.recall(
            query,
            top_k=top_k,  # type: ignore[arg-type]  # invalid types exercise runtime rejection
        )


@pytest.mark.asyncio
async def test_request_and_response_size_caps_fail_before_unbounded_use(
    live_server: _LiveServer,
) -> None:
    request_limited = RemanentiaHttpRecall(live_server.url, max_request_bytes=128)
    with pytest.raises(ValueError, match="request exceeds"):
        await request_limited.recall("q" * 1000, top_k=1)
    assert live_server.plan.requests == []

    live_server.plan.body = b"{" + b" " * 300 + b"}"
    response_limited = RemanentiaHttpRecall(live_server.url, max_response_bytes=256)
    with pytest.raises(ValueError, match="response exceeds"):
        await response_limited.recall("q", top_k=1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("contents", "message"),
    [
        (None, "unavailable"),
        (b"", "empty"),
        (b"x" * (MAX_TOKEN_BYTES + 1), "size"),
        (b"\xff", "UTF-8"),
        (b"two words", "invalid bearer"),
        (b"a\nb", "invalid bearer"),
    ],
)
async def test_token_file_failures_are_bounded_and_never_echo_the_path(
    live_server: _LiveServer,
    tmp_path: Path,
    contents: bytes | None,
    message: str,
) -> None:
    token_file = tmp_path / "sensitive-name-token"
    if contents is not None:
        token_file.write_bytes(contents)
    recall = RemanentiaHttpRecall(live_server.url, token_file=token_file)
    with pytest.raises((ValueError, RuntimeError), match=message) as failure:
        await recall.recall("q", top_k=1)
    assert str(token_file) not in str(failure.value)
    assert live_server.plan.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "content_type", "message"),
    [
        (b"not json", "application/json", "invalid JSON"),
        (b"\xff", "application/json", "invalid JSON"),
        (b'{"query":"q","results":[NaN]}', "application/json", "invalid JSON"),
        (_response(), "text/plain", "non-JSON"),
    ],
)
async def test_response_encoding_and_content_type_are_strict(
    live_server: _LiveServer,
    body: bytes,
    content_type: str,
    message: str,
) -> None:
    live_server.plan.body = body
    live_server.plan.content_type = content_type
    with pytest.raises(ValueError, match=message):
        await RemanentiaHttpRecall(live_server.url).recall("q", top_k=1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "mismatched"),
        ({"query": "wrong", "results": []}, "mismatched"),
        ({"query": "q", "results": None}, "results list"),
        ({"query": "q", "results": ["bad"]}, "malformed result"),
        ({"query": "q", "results": [_entry(name="")]}, "malformed result"),
        ({"query": "q", "results": [_entry(type=1)]}, "malformed result"),
        ({"query": "q", "results": [_entry(snippet="  ")]}, "malformed result"),
        ({"query": "q", "results": [_entry(score=True)]}, "malformed score"),
        ({"query": "q", "results": [_entry(score="0.5")]}, "malformed score"),
    ],
)
async def test_response_schema_is_strict(
    live_server: _LiveServer,
    payload: object,
    message: str,
) -> None:
    live_server.plan.body = json.dumps(payload).encode("utf-8")
    with pytest.raises(ValueError, match=message):
        await RemanentiaHttpRecall(live_server.url).recall("q", top_k=1)


@pytest.mark.asyncio
async def test_server_error_body_url_and_token_are_not_reflected(
    live_server: _LiveServer, tmp_path: Path
) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("hidden-token", encoding="utf-8")
    live_server.plan.status = 500
    live_server.plan.body = b"hidden-token https://private.invalid/secret"
    with pytest.raises(RuntimeError, match="memory service request failed") as failure:
        await RemanentiaHttpRecall(live_server.url, token_file=token_file).recall("q", top_k=1)
    text = str(failure.value)
    assert "hidden-token" not in text
    assert "https://" not in text


@pytest.mark.asyncio
async def test_redirect_is_refused_without_a_second_request(live_server: _LiveServer) -> None:
    live_server.plan.status = 302
    live_server.plan.location = live_server.url + "/stolen"
    with pytest.raises(RuntimeError, match="request failed"):
        await RemanentiaHttpRecall(live_server.url).recall("q", top_k=1)
    assert len(live_server.plan.requests) == 1


@pytest.mark.asyncio
async def test_timeout_becomes_a_generic_request_failure(live_server: _LiveServer) -> None:
    live_server.plan.delay = 0.1
    with pytest.raises(RuntimeError, match="request failed"):
        await RemanentiaHttpRecall(live_server.url, timeout_seconds=0.01).recall("q", top_k=1)
