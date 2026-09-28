# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — unit + live tests for hub WebSocket handshake Origin/Host guard

from __future__ import annotations

import asyncio
import base64
import contextlib
import os

import pytest
from websockets.asyncio.client import connect
from websockets.datastructures import Headers
from websockets.exceptions import InvalidStatus
from websockets.http11 import Request

from hub_e2e_helpers import http_get, read_until_type, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.hub_handshake import (
    handshake_allowed,
    handshake_guard_response,
    normalise_allow_origins,
    trusted_host_authorities,
)


def test_loopback_authorities_include_localhost_and_loopback_ips() -> None:
    authorities = trusted_host_authorities(bind_host="localhost", bind_port=8876)
    assert "localhost:8876" in authorities
    assert "127.0.0.1:8876" in authorities
    assert "localhost" in authorities


def test_bind_all_without_advertised_is_empty_fail_closed() -> None:
    assert trusted_host_authorities(bind_host="0.0.0.0", bind_port=8876) == ()


def test_advertised_host_admits_off_loopback_bind() -> None:
    authorities = trusted_host_authorities(
        bind_host="0.0.0.0",
        bind_port=8876,
        advertised_host="hub.example:8876",
    )
    assert "hub.example:8876" in authorities


def test_origin_less_requires_trusted_host() -> None:
    authorities = ("localhost:8876",)
    assert handshake_allowed(
        origin_header=None,
        host_header="localhost:8876",
        allowed_origins=(),
        trusted_authorities=authorities,
    )
    assert not handshake_allowed(
        origin_header=None,
        host_header="evil.example:8876",
        allowed_origins=(),
        trusted_authorities=authorities,
    )


def test_browser_origin_refused_without_allow_list() -> None:
    authorities = ("localhost:8876",)
    assert not handshake_allowed(
        origin_header="https://app.example",
        host_header="localhost:8876",
        allowed_origins=(),
        trusted_authorities=authorities,
    )


def test_allowed_origin_and_host_admit_browser() -> None:
    origins = normalise_allow_origins(("https://app.example",))
    authorities = ("localhost:8876",)
    assert handshake_allowed(
        origin_header="https://app.example",
        host_header="localhost:8876",
        allowed_origins=origins,
        trusted_authorities=authorities,
    )


def test_opaque_null_origin_refused() -> None:
    origins = normalise_allow_origins(("https://app.example",))
    assert not handshake_allowed(
        origin_header="null",
        host_header="localhost:8876",
        allowed_origins=origins,
        trusted_authorities=("localhost:8876",),
    )


def test_malformed_origin_refused() -> None:
    origins = normalise_allow_origins(("https://app.example",))
    assert not handshake_allowed(
        origin_header="not a origin",
        host_header="localhost:8876",
        allowed_origins=origins,
        trusted_authorities=("localhost:8876",),
    )


def test_wrong_host_dns_rebinding_shape_refused() -> None:
    origins = normalise_allow_origins(("https://app.example",))
    assert not handshake_allowed(
        origin_header="https://app.example",
        host_header="127.0.0.1:8876",
        allowed_origins=origins,
        trusted_authorities=("hub.example:8876",),
    )


def test_guard_response_returns_403_when_refused() -> None:
    headers = Headers()
    headers["Host"] = "evil.example:8876"
    headers["Origin"] = "https://hostile.example"
    request = Request("/", headers)
    response = handshake_guard_response(
        request,
        allowed_origins=(),
        trusted_authorities=("localhost:8876",),
    )
    assert response is not None
    assert response.status_code == 403


def test_guard_response_none_when_origin_less_host_ok() -> None:
    headers = Headers()
    headers["Host"] = "localhost:8876"
    request = Request("/", headers)
    assert (
        handshake_guard_response(
            request,
            allowed_origins=(),
            trusted_authorities=("localhost:8876",),
        )
        is None
    )


async def test_live_origin_less_native_client_connects() -> None:
    async with running_hub(SynapseHub(hub_id="syn-hs")) as (_, uri):
        async with connect(uri) as websocket:
            welcome = await read_until_type(websocket, "welcome")
            assert welcome["type"] == "welcome"


async def test_live_hostile_origin_refused_before_upgrade() -> None:
    async with running_hub(SynapseHub(hub_id="syn-hs")) as (_, uri):
        with pytest.raises(InvalidStatus) as exc_info:
            async with connect(uri, additional_headers={"Origin": "https://evil.example"}):
                pass
        assert exc_info.value.response.status_code == 403


async def test_live_allowed_origin_connects() -> None:
    hub = SynapseHub(hub_id="syn-hs", allowed_origins=("https://app.example",))
    async with running_hub(hub) as (_, uri):
        async with connect(uri, additional_headers={"Origin": "https://app.example"}) as websocket:
            welcome = await read_until_type(websocket, "welcome")
            assert welcome["type"] == "welcome"


def test_allowed_origin_wrong_host_refused_by_guard() -> None:
    headers = Headers()
    headers["Host"] = "evil.example:8876"
    headers["Origin"] = "https://app.example"
    request = Request("/", headers)
    response = handshake_guard_response(
        request,
        allowed_origins=normalise_allow_origins(("https://app.example",)),
        trusted_authorities=("hub.example:8876",),
    )
    assert response is not None
    assert response.status_code == 403


async def test_metrics_still_served_when_enabled() -> None:
    async with running_hub(SynapseHub(enable_metrics=True)) as (_, uri):
        status, _, body = await http_get(uri, "/metrics")
        assert status == 200
        assert "synapse_up" in body


async def test_metrics_path_refused_when_disabled() -> None:
    async with running_hub(SynapseHub(enable_metrics=False)) as (_, uri):
        status, _, body = await http_get(uri, "/metrics")
        assert status == 403
        assert "metrics disabled" in body


async def test_malformed_origin_header_refused_live() -> None:
    async with running_hub(SynapseHub(allowed_origins=("https://app.example",))) as (
        _,
        uri,
    ):
        with pytest.raises(InvalidStatus) as exc_info:
            async with connect(uri, additional_headers={"Origin": "null"}):
                pass
        assert exc_info.value.response.status_code == 403


def test_parser_allow_origin_and_advertised_host() -> None:
    from synapse_channel import cli

    args = cli.build_parser().parse_args(
        [
            "hub",
            "--allow-origin",
            "https://a.example",
            "--allow-origin",
            "https://b.example:8443",
            "--advertised-host",
            "hub.example:8876",
        ]
    )
    assert args.allow_origin == ["https://a.example", "https://b.example:8443"]
    assert args.advertised_host == "hub.example:8876"


def _upgrade_lines() -> list[str]:
    """Return the RFC 6455 upgrade headers with a fresh 16-byte client nonce."""
    nonce = base64.b64encode(os.urandom(16)).decode("ascii")
    return [
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {nonce}",
        "Sec-WebSocket-Version: 13",
    ]


async def _raw_upgrade(uri: str, header_lines: list[str]) -> tuple[bytes, bytes]:
    """Send one hand-written upgrade request; return its status line and body.

    The websockets client cannot emit a repeated ``Host`` header, so the request
    is written byte-for-byte to exercise the server's parser and hook as a
    request-desync probe would.
    """
    port = int(uri.rsplit(":", 1)[1])
    reader, writer = await asyncio.open_connection("localhost", port)
    try:
        request = "\r\n".join(["GET / HTTP/1.1", *header_lines, *_upgrade_lines()])
        writer.write(f"{request}\r\n\r\n".encode("ascii"))
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(4096), timeout=3.0)
    finally:
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()
    head, _, body = raw.partition(b"\r\n\r\n")
    return head.split(b"\r\n", 1)[0], body


@pytest.mark.parametrize(
    ("hosts", "origins"),
    [
        pytest.param(("localhost", "evil.example"), (), id="trusted-then-foreign-host"),
        pytest.param(("evil.example", "localhost"), (), id="foreign-then-trusted-host"),
        pytest.param(("localhost", "localhost"), (), id="identical-repeated-host"),
        pytest.param(("localhost",), ("https://app.example",) * 2, id="repeated-allowed-origin"),
        pytest.param((), (), id="missing-host"),
    ],
)
def test_guard_refuses_any_request_without_exactly_one_host_or_one_origin(
    hosts: tuple[str, ...], origins: tuple[str, ...]
) -> None:
    """Header multiplicity is decided before any value is interpreted."""
    headers = Headers()
    for host in hosts:
        headers["Host"] = host
    for origin in origins:
        headers["Origin"] = origin
    response = handshake_guard_response(
        Request("/", headers),
        allowed_origins=normalise_allow_origins(("https://app.example",)),
        trusted_authorities=("localhost",),
    )
    assert response is not None
    assert response.status_code == 403
    assert response.body == b"duplicate or missing origin/host header\n"


def test_guard_treats_an_empty_single_origin_as_origin_less() -> None:
    """One empty Origin keeps the native-client meaning it had before."""
    headers = Headers()
    headers["Host"] = "localhost:8876"
    headers["Origin"] = ""
    assert (
        handshake_guard_response(
            Request("/", headers),
            allowed_origins=(),
            trusted_authorities=("localhost:8876",),
        )
        is None
    )


@pytest.mark.parametrize(
    "extra_lines",
    [
        pytest.param(["Host: evil.example"], id="trusted-then-foreign-host"),
        pytest.param(["Host: {authority}"], id="identical-repeated-host"),
        pytest.param(
            ["Origin: https://app.example", "Origin: https://app.example"],
            id="repeated-allowed-origin",
        ),
    ],
)
async def test_live_repeated_boundary_header_is_refused_403_not_500(
    extra_lines: list[str],
) -> None:
    """A request-desync probe gets the deterministic refusal, not a server error."""
    hub = SynapseHub(hub_id="syn-hs", allowed_origins=("https://app.example",))
    async with running_hub(hub) as (_, uri):
        authority = uri.removeprefix("ws://")
        lines = [f"Host: {authority}", *(line.format(authority=authority) for line in extra_lines)]
        status, body = await _raw_upgrade(uri, lines)
        assert status == b"HTTP/1.1 403 Forbidden"
        assert body == b"duplicate or missing origin/host header\n"
        # The refusal leaves the hub serving: a well-formed upgrade still succeeds.
        status, _ = await _raw_upgrade(uri, [f"Host: {authority}"])
        assert status == b"HTTP/1.1 101 Switching Protocols"
