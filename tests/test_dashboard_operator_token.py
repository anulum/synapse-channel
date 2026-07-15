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
        assert _resolve_dashboard_token(dashboard_token="tok") == ("tok", False, True)

    def test_an_empty_supplied_token_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            _resolve_dashboard_token(dashboard_token="")

    def test_a_tokenless_caller_gets_a_generated_read_gating_token(self) -> None:
        # F3: reads are never left open, so a caller that supplies no token gets a
        # generated one that gates live/page reads (on loopback too).
        token, generated, protects_reads = _resolve_dashboard_token(dashboard_token=None)
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


async def test_read_only_dashboard_on_loopback_gets_a_read_gating_token() -> None:
    # F3: even a read-only loopback dashboard gates its live reads with a generated
    # token, so a same-host process cannot pull the cockpit's data without presenting it.
    async with running_hub(SynapseHub()) as (_hub, uri):
        server = _server(uri, operator=False)
        bearer = f"Bearer {server.dashboard_token}"
        try:
            assert server.dashboard_token is not None
            assert server.dashboard_token_generated is True
            assert _get_status(server.url("/studio.json")) == 401  # read: token required
            assert _get_status(server.url("/studio.json"), authorization=bearer) != 401
        finally:
            server.close()


async def test_operator_loopback_gates_both_reads_and_writes() -> None:
    # F3 + REV-SEC-04: on loopback a live read and an operator write each require the
    # generated token; a same-host non-browser process is refused both without it. The
    # validated React shell stays the only public exception so its unlock veil can load.
    async with running_hub(SynapseHub()) as (_hub, uri):
        server = _server(uri, operator=True)
        bearer = f"Bearer {server.dashboard_token}"
        try:
            assert _get_status(server.url("/studio.json")) == 401  # read: token required
            assert _get_status(server.url("/studio.json"), authorization=bearer) != 401
            assert _post_status(server.url("/message")) == 401  # write: token required
            # A write with the generated token clears auth (the unreachable hub then
            # makes it a 503, but auth passed — not a 401).
            assert _post_status(server.url("/message"), authorization=bearer) != 401
        finally:
            server.close()
