# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — tests for the MCP bearer gate's request rules
"""The gate's framing, header, session, throttle, bearer and pin rules (G-1..G-7, MG-1..MG-9)."""

from __future__ import annotations

import hashlib
import logging
import ssl
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mcp_gate_helpers import owner_file
from synapse_channel.mcp.gate import (
    FAILURE_DELAY,
    FAILURE_LIMIT,
    FAILURE_WINDOW,
    MAX_BODY,
    FailureThrottle,
    Refusal,
    SessionTable,
    check_pin,
    client_response_head,
    error_response,
    parse_request,
    pinned_upstream_context,
    presented_bearer,
    resolve_seat,
    response_status,
    response_values,
    upstream_head,
)
from synapse_channel.mcp.gate_config import GateConfig, GateSeat, new_bearer

NOW = datetime(2026, 11, 19, 3, 0, tzinfo=UTC)


def head(*lines: str, request_line: str = "POST /mcp HTTP/1.1") -> bytes:
    """Return a request head from text lines."""
    return "\r\n".join([request_line, *lines]).encode("latin-1")


def refusal(raw: bytes) -> tuple[int, str]:
    with pytest.raises(Refusal) as caught:
        parse_request(raw)
    return caught.value.status, caught.value.reason


# --- framing (G-3, MG-4) ---------------------------------------------------------------------


def test_a_plain_request_is_parsed() -> None:
    request = parse_request(head("Host: gate", "Content-Length: 7", "Accept: */*"))
    assert (request.method, request.target, request.length) == (b"POST", b"/mcp", 7)
    assert request.values(b"accept") == [b"*/*"]
    assert parse_request(head(request_line="GET /mcp?x=1 HTTP/1.0")).length == 0
    assert parse_request(head("Transfer-Encoding: Chunked")).length is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (head("X: a\x00b"), (400, "NUL in the request head")),
        (head("X: a\rb"), (400, "bare CR or LF in the request head")),
        (head("X: a", " folded"), (400, "folded header line")),
        (head("no colon"), (400, "malformed header line")),
        (head("Bad Name: x"), (400, "malformed header line")),
        (head(": x"), (400, "malformed header line")),
        (head(request_line="POST /mcp"), (400, "malformed request line")),
        (head(request_line="POST /mcp HTTP/2.0"), (400, "malformed request line")),
        (head(request_line="CONNECT /x HTTP/1.1"), (400, "method refused")),
        (head(request_line="TRACE /x HTTP/1.1"), (400, "method refused")),
        (head(request_line="P{ST /x HTTP/1.1"), (400, "method refused")),
        (head(request_line=" /x HTTP/1.1"), (400, "method refused")),
        (head(request_line="GET http://evil/mcp HTTP/1.1"), (400, "only origin-form")),
        (head(request_line="GET //evil/mcp HTTP/1.1"), (400, "only origin-form")),
        (head("Host: a", "Host: b"), (400, "duplicate Host")),
        (head("Content-Length: 1", "Transfer-Encoding: chunked"), (400, "transfer framing")),
        (head("Transfer-Encoding: gzip, chunked"), (400, "transfer framing refused")),
        (head("Transfer-Encoding: chunked", "Transfer-Encoding: chunked"), (400, "transfer")),
        (head("Content-Length: 1", "Content-Length: 1"), (400, "content-length invalid")),
        (head("Content-Length: -1"), (400, "content-length invalid")),
        (head(f"Content-Length: {MAX_BODY + 1}"), (413, "body too large")),
    ],
)
def test_every_unaccepted_framing_is_refused(raw: bytes, expected: tuple[int, str]) -> None:
    status, reason = refusal(raw)
    assert status == expected[0]
    assert reason.startswith(expected[1])


# --- the bearer and the forwarded headers (G-1, MG-3) ---------------------------------------


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        ((), b""),
        (("Authorization: Bearer abc", "Authorization: Bearer def"), b""),
        (("Authorization: Basic abc",), b""),
        (("Authorization: Bearer abc",), b"abc"),
        (("Authorization: bearer  'abc' ",), b"abc"),
    ],
)
def test_the_bearer_comes_from_exactly_one_authorization_header(
    lines: tuple[str, ...], expected: bytes
) -> None:
    assert presented_bearer(parse_request(head(*lines))) == expected


def test_only_allow_listed_client_headers_reach_the_listener() -> None:
    request = parse_request(
        head(
            "Host: attacker",
            "Authorization: Bearer stable",
            "Content-Type: application/json",
            "Accept: text/event-stream",
            "Mcp-Session-Id: s1",
            "MCP-Protocol-Version: 2025-06-18",
            "Last-Event-ID: 4",
            "User-Agent: grok",
            "X-Forwarded-For: 1.2.3.4",
            "Forwarded: for=1.2.3.4",
            "X-Synapse-Identity: PROJ/root",
            "Cookie: a=b",
            "Connection: keep-alive",
            "Content-Length: 2",
        )
    )
    sent = upstream_head(request, b"seat-token", "gate.example", 2).decode("ascii")
    lines = sent.split("\r\n")
    assert lines[0] == "POST /mcp HTTP/1.1"
    for kept in (
        "content-type: application/json",
        "accept: text/event-stream",
        "mcp-session-id: s1",
        "mcp-protocol-version: 2025-06-18",
        "last-event-id: 4",
        "user-agent: grok",
        "Host: gate.example",
        "Authorization: Bearer seat-token",
        "Content-Length: 2",
        "Connection: close",
    ):
        assert kept in lines
    lowered = sent.lower()
    for dropped in ("x-forwarded-for", "forwarded:", "x-synapse", "cookie", "attacker", "stable"):
        assert dropped not in lowered
    assert sent.endswith("\r\n\r\n")


# --- responses (G-7) -----------------------------------------------------------------------


def test_responses_are_read_and_rewritten_without_a_challenge() -> None:
    raw = (
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nWWW-Authenticate: Bearer\r\n"
        b"Transfer-Encoding: chunked\r\nConnection: keep-alive\r\nMcp-Session-Id: s9"
    )
    assert response_status(raw) == 200
    assert response_values(raw, b"mcp-session-id") == [b"s9"]
    buffered = client_response_head(raw, length=5).decode("ascii")
    assert "WWW-Authenticate" not in buffered
    assert "Transfer-Encoding" not in buffered and "keep-alive" not in buffered
    assert buffered.endswith("Content-Length: 5\r\nConnection: close\r\n\r\n")
    streamed = client_response_head(raw, length=None).decode("ascii")
    assert "Transfer-Encoding: chunked" in streamed and "WWW-Authenticate" not in streamed


@pytest.mark.parametrize("raw", [b"garbage", b"HTTP/1.1 OK", b"SPDY/3 200 OK"])
def test_a_malformed_upstream_response_is_a_bad_gateway(raw: bytes) -> None:
    with pytest.raises(Refusal) as caught:
        response_status(raw)
    assert caught.value.status == 502


@pytest.mark.parametrize("status", [400, 401, 413, 431, 502, 503])
def test_error_responses_close_and_name_no_detail(status: int) -> None:
    response = error_response(status)
    assert response.startswith(f"HTTP/1.1 {status} ".encode())
    assert b"Connection: close" in response
    head_part, _, body = response.partition(b"\r\n\r\n")
    assert f"Content-Length: {len(body)}".encode() in head_part


# --- sessions (G-1, MG-9) and the throttle (MG-5) ------------------------------------------


def test_a_session_of_another_seat_is_refused_and_a_deleted_one_forgotten() -> None:
    table = SessionTable(limit=2)
    table.check(b"unknown", "PROJ/a")  # unknown sessions are the listener's to judge
    table.bind(b"s1", "PROJ/a")
    table.check(b"s1", "PROJ/a")
    with pytest.raises(Refusal) as caught:
        table.check(b"s1", "PROJ/b")
    assert caught.value.status == 401
    table.forget(b"s1")
    table.check(b"s1", "PROJ/b")
    for session in (b"s2", b"s3", b"s4"):
        table.bind(session, "PROJ/a")
    table.check(b"s2", "PROJ/b")  # evicted, least recently used first
    with pytest.raises(Refusal):
        table.check(b"s4", "PROJ/b")


def test_failed_authentication_is_throttled_past_its_limit() -> None:
    now = [0.0]
    throttle = FailureThrottle(clock=lambda: now[0])
    assert [throttle.record() for _ in range(FAILURE_LIMIT)] == [0.0] * FAILURE_LIMIT
    assert throttle.record() == FAILURE_DELAY
    now[0] = FAILURE_WINDOW + 1
    assert throttle.record() == 0.0


# --- seats and bearers (G-2, MG-2) -------------------------------------------------------------


def gate_config(tmp_path: Path, seats: int = 2) -> GateConfig:
    """Return a gate configuration whose seats PROJ/s0.. have no bearer records yet."""
    return GateConfig(
        listen_host="127.0.0.1",
        listen_port=0,
        upstream_host="127.0.0.1",
        upstream_port=1,
        upstream_cert_sha256="0" * 64,
        public_host="gate.example",
        tls_cert_file=tmp_path / "gate.crt",
        tls_key_file=tmp_path / "gate.key",
        grant_file=tmp_path / "grants.json",
        seats=tuple(
            GateSeat(f"PROJ/s{index}", tmp_path / f"s{index}.bearer", tmp_path / f"s{index}.token")
            for index in range(seats)
        ),
    )


def test_a_bearer_resolves_to_its_own_seat_only(tmp_path: Path) -> None:
    config = gate_config(tmp_path, seats=3)
    first = new_bearer(config.seats[0].bearer_file, now=NOW)
    second = new_bearer(config.seats[1].bearer_file, now=NOW)
    # the third seat has no record, so it is revoked and skipped
    assert resolve_seat(config, first.encode(), now=NOW).seat == "PROJ/s0"
    assert resolve_seat(config, second.encode(), now=NOW).seat == "PROJ/s1"
    for presented in (b"", b"wrong", first.encode() + b"x"):
        with pytest.raises(Refusal) as caught:
            resolve_seat(config, presented, now=NOW)
        assert (caught.value.status, caught.value.reason) == (401, "unauthorized")


def test_an_expired_bearer_is_refused_and_a_due_one_is_reported(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    config = gate_config(tmp_path, seats=1)
    secret = new_bearer(config.seats[0].bearer_file, now=NOW).encode()
    with caplog.at_level(logging.WARNING, logger="synapse.mcp.gate"):
        resolve_seat(config, secret, now=NOW + timedelta(days=80))
    assert "rotate it" in caplog.text
    with pytest.raises(Refusal) as caught:
        resolve_seat(config, secret, now=NOW + timedelta(days=90))
    assert caught.value.reason == "bearer expired"


def test_an_empty_bearer_never_matches_even_its_own_digest(tmp_path: Path) -> None:
    config = gate_config(tmp_path, seats=1)
    owner_file(
        config.seats[0].bearer_file,
        '{"schema": "synapse-mcp-gate-bearer.v1", "sha256": "'
        + hashlib.sha256(b"").hexdigest()
        + '", "issued_at": "2026-11-19T00:00:00Z", "expires_at": "2027-01-01T00:00:00Z"}',
    )
    with pytest.raises(Refusal):
        resolve_seat(config, b"", now=NOW)


# --- the upstream pin (MG-1) ----------------------------------------------------------------


def test_only_the_pinned_upstream_certificate_is_accepted() -> None:
    certificate = b"the listener's certificate"
    pin = hashlib.sha256(certificate).hexdigest()
    check_pin(certificate, pin)
    for presented in (None, b"a squatter's certificate"):
        with pytest.raises(Refusal) as caught:
            check_pin(presented, pin)
        assert (caught.value.status, caught.value.reason) == (502, "upstream identity mismatch")
    context = pinned_upstream_context()
    assert context.verify_mode is ssl.CERT_NONE and not context.check_hostname
