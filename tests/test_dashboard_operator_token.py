# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the operator dashboard write-path requires a token even on loopback

from __future__ import annotations

import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from hub_e2e_helpers import running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.dashboard import DashboardServer, start_dashboard_server
from synapse_channel.dashboard_bind import _resolve_dashboard_token


class TestResolveDashboardToken:
    def test_a_supplied_token_gates_reads_and_writes(self) -> None:
        assert _resolve_dashboard_token(
            "127.0.0.1", allow_non_loopback=False, dashboard_token="tok", operator=True
        ) == ("tok", False, True)

    def test_an_empty_supplied_token_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            _resolve_dashboard_token(
                "127.0.0.1", allow_non_loopback=False, dashboard_token="", operator=True
            )

    def test_a_read_only_loopback_dashboard_needs_no_token(self) -> None:
        assert _resolve_dashboard_token(
            "127.0.0.1", allow_non_loopback=False, dashboard_token=None, operator=False
        ) == (None, False, False)

    def test_a_read_only_exposed_bind_generates_a_read_gating_token(self) -> None:
        token, generated, protects_reads = _resolve_dashboard_token(
            "0.0.0.0", allow_non_loopback=True, dashboard_token=None, operator=False
        )
        assert generated is True
        assert protects_reads is True
        assert token is not None and len(token) > 0

    def test_operator_on_loopback_generates_a_write_only_token(self) -> None:
        # The fix: an armed write-path gets a token even on loopback, but it gates
        # writes only — reads stay open so the browser cockpit still loads.
        token, generated, protects_reads = _resolve_dashboard_token(
            "127.0.0.1", allow_non_loopback=False, dashboard_token=None, operator=True
        )
        assert generated is True
        assert protects_reads is False
        assert token is not None and len(token) > 0

    def test_an_exposed_operator_bind_still_gates_reads(self) -> None:
        token, generated, protects_reads = _resolve_dashboard_token(
            "0.0.0.0", allow_non_loopback=True, dashboard_token=None, operator=True
        )
        assert generated is True
        assert protects_reads is True
        assert token is not None and len(token) > 0


def _server(hub_uri: str, *, operator: bool) -> DashboardServer:
    return start_dashboard_server(
        host="127.0.0.1",
        port=0,
        uri=hub_uri,
        name="SYNAPSE-CHANNEL/dashboard",
        token=None,
        ready_timeout=1.0,
        response_timeout=1.0,
        refresh_seconds=5,
        allow_non_loopback=False,
        dashboard_token=None,
        operator=operator,
    )


def _get_status(url: str, *, authorization: str | None = None) -> int:
    headers = {"Connection": "close"}
    if authorization is not None:
        headers["Authorization"] = authorization
    try:
        with urlopen(Request(url, headers=headers), timeout=3) as response:  # nosec B310
            return int(response.status)
    except HTTPError as exc:
        return int(exc.code)


def _post_status(url: str, *, authorization: str | None = None) -> int:
    headers = {"Connection": "close", "Content-Type": "application/json"}
    if authorization is not None:
        headers["Authorization"] = authorization
    body = json.dumps({"to": "x", "text": "hi"}).encode("utf-8")
    try:
        with urlopen(Request(url, data=body, headers=headers, method="POST"), timeout=3) as resp:  # nosec B310
            return int(resp.status)
    except HTTPError as exc:
        return int(exc.code)


async def test_operator_dashboard_on_loopback_gets_a_generated_token() -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        server = _server(uri, operator=True)
        try:
            assert server.dashboard_token is not None
            assert server.dashboard_token_generated is True
        finally:
            server.close()


async def test_read_only_dashboard_on_loopback_stays_tokenless() -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        server = _server(uri, operator=False)
        try:
            assert server.dashboard_token is None
            assert server.dashboard_token_generated is False
        finally:
            server.close()


async def test_operator_loopback_leaves_reads_open_but_gates_writes() -> None:
    # The write-only token: a browser can still load the read-only cockpit (GET is
    # open), but a same-host non-browser process cannot POST a write without the token.
    async with running_hub(SynapseHub()) as (_hub, uri):
        server = _server(uri, operator=True)
        bearer = f"Bearer {server.dashboard_token}"
        try:
            assert _get_status(server.url("/cockpit.js")) == 200  # read: no token needed
            assert _post_status(server.url("/message")) == 401  # write: token required
            # A write with the generated token passes auth (the unreachable hub then
            # makes it a 503, but auth was cleared — not a 401).
            assert _post_status(server.url("/message"), authorization=bearer) != 401
        finally:
            server.close()
