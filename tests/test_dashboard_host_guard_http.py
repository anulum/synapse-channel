# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dashboard Host-boundary HTTP integration tests

"""End-to-end HTTP tests for the dashboard DNS-rebinding Host boundary."""

from __future__ import annotations

import http.client
from collections.abc import Iterator

import pytest

from synapse_channel.dashboard import DashboardServer, start_dashboard_server


def _request(
    server: DashboardServer,
    method: str,
    path: str,
    *,
    host_header: str | None,
) -> tuple[int, str]:
    """Send one request with a fully controlled ``Host`` header.

    Passing ``host_header=None`` omits the header entirely so the fail-closed
    path — a request without a ``Host`` — can be exercised.
    """
    connection = http.client.HTTPConnection(server.host, server.port, timeout=3)
    try:
        connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        if host_header is not None:
            connection.putheader("Host", host_header)
        connection.putheader("Connection", "close")
        connection.endheaders()
        response = connection.getresponse()
        body = response.read().decode("utf-8")
        return response.status, body
    finally:
        connection.close()


@pytest.fixture
def server() -> Iterator[DashboardServer]:
    """A loopback dashboard against an unreachable hub, closed after the test."""
    started = start_dashboard_server(
        host="127.0.0.1",
        port=0,
        uri="ws://127.0.0.1:1",
        name="SYNAPSE-CHANNEL/dashboard",
        token=None,
        ready_timeout=0.01,
        response_timeout=0.01,
        refresh_seconds=5,
        allow_non_loopback=False,
    )
    try:
        yield started
    finally:
        started.close()


def test_get_with_a_rebinding_host_is_refused(server: DashboardServer) -> None:
    """A GET carrying an attacker-chosen Host is refused with 403."""
    status, body = _request(server, "GET", "/snapshot.json", host_header="attacker.example:80")
    assert status == 403
    assert body == "dashboard host authority not allowed\n"


def test_get_with_a_loopback_host_passes_the_boundary(server: DashboardServer) -> None:
    """A GET on the served loopback authority clears the boundary (no 403)."""
    status, _ = _request(server, "GET", "/missing", host_header=f"127.0.0.1:{server.port}")
    assert status == 404


def test_get_with_the_localhost_name_passes_the_boundary(server: DashboardServer) -> None:
    """The ``localhost`` name on the served port clears the boundary."""
    status, _ = _request(server, "GET", "/missing", host_header=f"localhost:{server.port}")
    assert status == 404


def test_get_without_a_host_header_fails_closed(server: DashboardServer) -> None:
    """A GET without any Host header is refused."""
    status, body = _request(server, "GET", "/snapshot.json", host_header=None)
    assert status == 403
    assert body == "dashboard host authority not allowed\n"


def test_post_with_a_rebinding_host_is_refused_before_auth(server: DashboardServer) -> None:
    """A write with a foreign Host is refused before the write-auth decision."""
    status, body = _request(server, "POST", "/message", host_header="attacker.example:80")
    assert status == 403
    assert body == "dashboard host authority not allowed\n"


def test_operator_approved_extra_host_clears_the_boundary() -> None:
    """An operator-approved extra host clears the boundary at the served port."""
    started = start_dashboard_server(
        host="127.0.0.1",
        port=0,
        uri="ws://127.0.0.1:1",
        name="SYNAPSE-CHANNEL/dashboard",
        token=None,
        ready_timeout=0.01,
        response_timeout=0.01,
        refresh_seconds=5,
        allow_non_loopback=False,
        allow_hosts=("dash.internal",),
    )
    try:
        approved, _ = _request(
            started, "GET", "/missing", host_header=f"dash.internal:{started.port}"
        )
        rejected, _ = _request(started, "GET", "/missing", host_header="other.example:80")
    finally:
        started.close()
    assert approved == 404
    assert rejected == 403


def test_wildcard_bind_without_extras_defers_to_the_read_token() -> None:
    """On a wildcard bind with no extras the boundary defers to the read token.

    A ``0.0.0.0`` bind is off loopback, so a read-protecting token is generated;
    the guard relaxes rather than 403-ing every real client, so a foreign Host
    reaches authentication and is answered 401, not the guard's 403.
    """
    started = start_dashboard_server(
        host="0.0.0.0",  # nosec B104
        port=0,
        uri="ws://127.0.0.1:1",
        name="SYNAPSE-CHANNEL/dashboard",
        token=None,
        ready_timeout=0.01,
        response_timeout=0.01,
        refresh_seconds=5,
        allow_non_loopback=True,
    )
    try:
        foreign, _ = _request(started, "GET", "/snapshot.json", host_header="attacker.example:80")
        lan, _ = _request(started, "GET", "/snapshot.json", host_header="192.168.1.50:8765")
    finally:
        started.close()
    assert foreign == 401
    assert lan == 401


def test_wildcard_bind_with_extras_enforces_the_boundary() -> None:
    """Operator-approved extras opt a wildcard bind back into strict filtering.

    The approved host clears the boundary and then meets the read token (401),
    while an unlisted host is refused by the guard (403).
    """
    started = start_dashboard_server(
        host="0.0.0.0",  # nosec B104
        port=0,
        uri="ws://127.0.0.1:1",
        name="SYNAPSE-CHANNEL/dashboard",
        token=None,
        ready_timeout=0.01,
        response_timeout=0.01,
        refresh_seconds=5,
        allow_non_loopback=True,
        allow_hosts=("dash.internal",),
    )
    try:
        approved, _ = _request(
            started, "GET", "/snapshot.json", host_header=f"dash.internal:{started.port}"
        )
        rejected, body = _request(started, "GET", "/snapshot.json", host_header="other.example:80")
    finally:
        started.close()
    assert approved == 401
    assert rejected == 403
    assert body == "dashboard host authority not allowed\n"


def test_a_malformed_allow_host_is_refused_at_startup() -> None:
    """A malformed operator-approved host fails at startup, not per request."""
    with pytest.raises(ValueError):
        start_dashboard_server(
            host="127.0.0.1",
            port=0,
            uri="ws://127.0.0.1:1",
            name="SYNAPSE-CHANNEL/dashboard",
            token=None,
            ready_timeout=0.01,
            response_timeout=0.01,
            refresh_seconds=5,
            allow_non_loopback=False,
            allow_hosts=("bad host",),
        )
