# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — unit + live tests for hub WebSocket handshake Origin/Host guard

from __future__ import annotations

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
