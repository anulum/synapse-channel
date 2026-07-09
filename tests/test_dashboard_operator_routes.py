# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dashboard operator write-route tests

"""Tests for the dashboard's operator write/task HTTP routes (write side)."""

from __future__ import annotations

import json
import socket

import pytest

import synapse_channel.dashboard as dashboard_module
import synapse_channel.dashboard_operator_writes as operator_writes_module
from synapse_channel.dashboard import DashboardServer, start_dashboard_server
from synapse_channel.dashboard_operator import (
    ACCEPTED,
    DELIVERED,
    DENIED,
    REJECTED,
    UNDELIVERED,
    UNREACHABLE,
    RelayOutcome,
)


def _http_post(
    url: str,
    body: bytes | str,
    *,
    authorization: str | None = None,
    content_type: str = "application/json",
) -> tuple[int, str, str]:
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    headers = {"Connection": "close", "Content-Type": content_type}
    if authorization is not None:
        headers["Authorization"] = authorization
    data = body.encode("utf-8") if isinstance(body, str) else body
    request = Request(url, data=data, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=3) as response:  # nosec B310
            return (
                response.status,
                response.headers.get_content_type(),
                response.read().decode("utf-8"),
            )
    except HTTPError as exc:
        return exc.code, exc.headers.get_content_type(), exc.read().decode("utf-8")


# The operator write-path now always has a token (generated if none is supplied, even
# on loopback), so the write-path tests authenticate with a known one; auth itself is
# exercised separately by test_operator_write_requires_bearer_when_token_set.
_OP_TOKEN = "op-token"
_OP_BEARER = f"Bearer {_OP_TOKEN}"


def _operator_post(
    url: str,
    body: bytes | str,
    *,
    authorization: str | None = _OP_BEARER,
    content_type: str = "application/json",
) -> tuple[int, str, str]:
    """POST to an operator route, carrying the known bearer token by default."""
    return _http_post(url, body, authorization=authorization, content_type=content_type)


def _stub_relay_class(outcome: RelayOutcome) -> type:
    """Return a drop-in OperatorRelay that yields ``outcome`` without a hub."""

    class _StubRelay:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def relay_message(self, to: str, text: str) -> RelayOutcome:
            return outcome

        async def relay_task(
            self, task_id: str, title: str, *, depends_on: object = ()
        ) -> RelayOutcome:
            return outcome

        async def relay_task_update(
            self, task_id: str, *, status: str | None = None, note: str | None = None
        ) -> RelayOutcome:
            return outcome

    return _StubRelay


def _operator_server(*, dashboard_token: str | None = _OP_TOKEN) -> DashboardServer:
    return start_dashboard_server(
        host="127.0.0.1",
        port=0,
        uri="ws://hub.invalid",
        name="DASH",
        token=None,
        ready_timeout=0.2,
        response_timeout=0.2,
        refresh_seconds=5,
        allow_non_loopback=False,
        operator=True,
        dashboard_token=dashboard_token,
    )


def test_operator_write_is_404_without_operator_mode() -> None:
    server = start_dashboard_server(
        host="127.0.0.1",
        port=0,
        uri="ws://hub.invalid",
        name="DASH",
        token=None,
        ready_timeout=0.2,
        response_timeout=0.2,
        refresh_seconds=5,
        allow_non_loopback=False,
    )
    try:
        status, _, _ = _http_post(server.url("/message"), json.dumps({"to": "x", "text": "hi"}))
    finally:
        server.close()

    assert status == 404


def test_operator_write_requires_bearer_when_token_set() -> None:
    server = _operator_server(dashboard_token="viewer")
    try:
        missing, _, _ = _http_post(server.url("/message"), json.dumps({"to": "x", "text": "hi"}))
        wrong, _, _ = _http_post(
            server.url("/message"),
            json.dumps({"to": "x", "text": "hi"}),
            authorization="Bearer nope",
        )
    finally:
        server.close()

    assert missing == 401
    assert wrong == 401


def test_operator_write_rejects_bad_bodies() -> None:
    server = _operator_server()
    try:
        non_json, _, _ = _operator_post(server.url("/message"), "not json at all")
        missing_to, _, _ = _operator_post(server.url("/message"), json.dumps({"text": "hi"}))
        empty_text, _, _ = _operator_post(
            server.url("/message"), json.dumps({"to": "x", "text": "   "})
        )
        not_object, _, _ = _operator_post(server.url("/message"), json.dumps(["to", "text"]))
        unknown_route, _, _ = _operator_post(
            server.url("/other"), json.dumps({"to": "x", "text": "hi"})
        )
    finally:
        server.close()

    assert non_json == 400
    assert missing_to == 400
    assert empty_text == 400
    assert not_object == 400
    assert unknown_route == 404


def test_operator_write_rejects_a_non_json_content_type() -> None:
    # The CSRF defence: a cross-origin page can POST a body to a loopback surface
    # without a preflight only with a "simple" content type (text/plain / form /
    # multipart). Requiring application/json turns that request away with a 415,
    # even though the body is valid JSON, so a web page cannot drive a write.
    server = _operator_server()
    try:
        text_plain, _, body = _operator_post(
            server.url("/message"),
            json.dumps({"to": "x", "text": "hi"}),
            content_type="text/plain",
        )
        form, _, _ = _operator_post(
            server.url("/message"),
            json.dumps({"to": "x", "text": "hi"}),
            content_type="application/x-www-form-urlencoded",
        )
    finally:
        server.close()

    assert text_plain == 415
    assert "application/json" in body
    assert form == 415


def test_operator_write_accepts_json_with_a_charset_parameter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A charset (or any) parameter after the media type must not defeat the check —
    # application/json; charset=utf-8 is still JSON and must reach the relay.
    monkeypatch.setattr(
        operator_writes_module,
        "OperatorRelay",
        _stub_relay_class(RelayOutcome(DELIVERED, "delivered")),
    )
    server = _operator_server()
    try:
        status, _, _ = _operator_post(
            server.url("/message"),
            json.dumps({"to": "x", "text": "hi"}),
            content_type="application/json; charset=utf-8",
        )
    finally:
        server.close()

    assert status == 200


@pytest.mark.parametrize(
    ("outcome", "expected_status"),
    [
        (RelayOutcome(DELIVERED, "delivered to a live recipient"), 200),
        (RelayOutcome(UNDELIVERED, "accepted; no live recipient (dead-lettered)"), 200),
        (RelayOutcome(DENIED, "no chat rule for team-b"), 403),
        (RelayOutcome(UNREACHABLE, "could not reach hub"), 503),
    ],
)
def test_operator_write_maps_relay_outcome_to_status(
    monkeypatch: pytest.MonkeyPatch, outcome: RelayOutcome, expected_status: int
) -> None:
    monkeypatch.setattr(operator_writes_module, "OperatorRelay", _stub_relay_class(outcome))
    server = _operator_server()
    try:
        status, content_type, body = _operator_post(
            server.url("/message"), json.dumps({"to": "SC-NEUROCORE", "text": "ship it"})
        )
    finally:
        server.close()

    assert status == expected_status
    assert content_type == "application/json"
    document = json.loads(body)
    assert document["action"] == "message"
    assert document["to"] == "SC-NEUROCORE"
    assert document["status"] == outcome.status
    assert document["ok"] is outcome.ok


def test_operator_write_is_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dashboard_module, "OPERATOR_RATE_MAX", 1)
    monkeypatch.setattr(
        operator_writes_module,
        "OperatorRelay",
        _stub_relay_class(RelayOutcome(DELIVERED, "delivered")),
    )
    server = _operator_server()
    try:
        first, _, _ = _operator_post(server.url("/message"), json.dumps({"to": "x", "text": "hi"}))
        second, _, body = _operator_post(
            server.url("/message"), json.dumps({"to": "x", "text": "hi"})
        )
    finally:
        server.close()

    assert first == 200
    assert second == 429
    assert "rate limit" in body


def test_operator_task_rejects_bad_bodies() -> None:
    server = _operator_server()
    try:
        missing_id, _, _ = _operator_post(server.url("/task"), json.dumps({"title": "Ship"}))
        empty_title, _, _ = _operator_post(
            server.url("/task"), json.dumps({"id": "T-1", "title": "  "})
        )
        bad_deps, _, _ = _operator_post(
            server.url("/task"),
            json.dumps({"id": "T-1", "title": "Ship", "depends_on": [1, 2]}),
        )
    finally:
        server.close()

    assert missing_id == 400
    assert empty_title == 400
    assert bad_deps == 400


def test_operator_task_update_rejects_bad_bodies() -> None:
    server = _operator_server()
    try:
        missing_id, _, _ = _operator_post(
            server.url("/task/update"), json.dumps({"status": "done"})
        )
        neither, _, _ = _operator_post(server.url("/task/update"), json.dumps({"id": "T-1"}))
        bad_status, _, _ = _operator_post(
            server.url("/task/update"), json.dumps({"id": "T-1", "status": 7})
        )
        bad_note_type, _, _ = _operator_post(
            server.url("/task/update"), json.dumps({"id": "T-1", "note": 7})
        )
        empty_note, _, _ = _operator_post(
            server.url("/task/update"), json.dumps({"id": "T-1", "note": "   "})
        )
    finally:
        server.close()

    assert missing_id == 400
    assert neither == 400
    assert bad_status == 400
    assert bad_note_type == 400  # a present note that is not a string
    assert empty_note == 400  # a present note that is blank


@pytest.mark.parametrize(
    ("outcome", "expected_status"),
    [
        (RelayOutcome(ACCEPTED, "task 'T-1' declared on the board"), 200),
        (RelayOutcome(DENIED, "no board rule for team-b"), 403),
        (RelayOutcome(REJECTED, "Task title is required."), 409),
        (RelayOutcome(UNREACHABLE, "could not reach hub"), 503),
    ],
)
def test_operator_task_maps_relay_outcome_to_status(
    monkeypatch: pytest.MonkeyPatch, outcome: RelayOutcome, expected_status: int
) -> None:
    monkeypatch.setattr(operator_writes_module, "OperatorRelay", _stub_relay_class(outcome))
    server = _operator_server()
    try:
        status, content_type, body = _operator_post(
            server.url("/task"),
            json.dumps({"id": "T-1", "title": "Ship", "depends_on": ["T-0"]}),
        )
    finally:
        server.close()

    assert status == expected_status
    assert content_type == "application/json"
    document = json.loads(body)
    assert document["action"] == "task"
    assert document["id"] == "T-1"
    assert document["status"] == outcome.status
    assert document["ok"] is outcome.ok


@pytest.mark.parametrize(
    ("outcome", "expected_status"),
    [
        (RelayOutcome(ACCEPTED, "task 'T-1' update applied on the board"), 200),
        (RelayOutcome(DENIED, "no board rule for team-b"), 403),
        (RelayOutcome(REJECTED, "Unknown ledger status 'nope'."), 409),
        (RelayOutcome(UNREACHABLE, "could not reach hub"), 503),
    ],
)
def test_operator_task_update_maps_relay_outcome_to_status(
    monkeypatch: pytest.MonkeyPatch, outcome: RelayOutcome, expected_status: int
) -> None:
    monkeypatch.setattr(operator_writes_module, "OperatorRelay", _stub_relay_class(outcome))
    server = _operator_server()
    try:
        status, content_type, body = _operator_post(
            server.url("/task/update"),
            json.dumps({"id": "T-1", "status": "done", "note": "shipped"}),
        )
    finally:
        server.close()

    assert status == expected_status
    assert content_type == "application/json"
    document = json.loads(body)
    assert document["action"] == "task_update"
    assert document["id"] == "T-1"
    assert document["status"] == outcome.status
    assert document["ok"] is outcome.ok


def _raising_relay_class(exc: Exception) -> type:
    """Return a drop-in OperatorRelay whose relay coroutine raises ``exc``."""

    class _RaisingRelay:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def relay_message(self, to: str, text: str) -> RelayOutcome:
            raise exc

    return _RaisingRelay


def test_operator_write_maps_a_relay_exception_to_503(monkeypatch: pytest.MonkeyPatch) -> None:
    # A relay that dies mid-flight (a dropped socket, a runtime fault) must
    # surface as a fail-visible 503, never a 500 stack trace on a write surface.
    monkeypatch.setattr(
        operator_writes_module, "OperatorRelay", _raising_relay_class(OSError("connection reset"))
    )
    server = _operator_server()
    try:
        status, _, body = _operator_post(
            server.url("/message"), json.dumps({"to": "x", "text": "hi"})
        )
    finally:
        server.close()

    assert status == 503
    assert "operator relay failed" in body
    assert "connection reset" in body


def test_operator_write_refuses_an_oversize_body() -> None:
    # A body past the 64 KiB ceiling is refused before it is read as JSON, so a
    # write route cannot be used to feed the process an unbounded payload.
    server = _operator_server()
    oversize = json.dumps({"to": "x", "text": "z" * (64 * 1024 + 16)})
    try:
        status, _, body = _operator_post(server.url("/message"), oversize)
    finally:
        server.close()

    assert status == 400
    assert "within the size limit" in body


def test_operator_write_refuses_a_non_numeric_content_length() -> None:
    # A hand-crafted request whose Content-Length is not a number must be
    # refused with a 400, not crash the handler — urllib always sends a valid
    # length, so this defence is exercised over a raw socket.
    server = _operator_server()
    host = str(server.server.server_address[0])
    port = int(server.server.server_address[1])
    raw = (
        "POST /message HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Authorization: {_OP_BEARER}\r\n"
        "Content-Type: application/json\r\n"
        "Content-Length: not-a-number\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).encode("ascii")
    try:
        with socket.create_connection((host, port), timeout=3) as connection:
            connection.sendall(raw)
            response = b""
            while True:
                chunk = connection.recv(4096)
                if not chunk:
                    break
                response += chunk
    finally:
        server.close()

    status_line = response.split(b"\r\n", 1)[0].decode("ascii")
    assert "400" in status_line
