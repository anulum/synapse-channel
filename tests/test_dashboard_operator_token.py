# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the operator dashboard write-path requires a token even on loopback

from __future__ import annotations

import pytest

from hub_e2e_helpers import running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.dashboard import DashboardServer, start_dashboard_server
from synapse_channel.dashboard_bind import _resolve_dashboard_token


class TestResolveDashboardToken:
    def test_a_supplied_token_is_used_verbatim(self) -> None:
        assert _resolve_dashboard_token(
            "127.0.0.1", allow_non_loopback=False, dashboard_token="tok", operator=True
        ) == ("tok", False)

    def test_an_empty_supplied_token_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            _resolve_dashboard_token(
                "127.0.0.1", allow_non_loopback=False, dashboard_token="", operator=True
            )

    def test_a_read_only_loopback_dashboard_needs_no_token(self) -> None:
        assert _resolve_dashboard_token(
            "127.0.0.1", allow_non_loopback=False, dashboard_token=None, operator=False
        ) == (None, False)

    def test_a_read_only_exposed_bind_generates_a_token(self) -> None:
        token, generated = _resolve_dashboard_token(
            "0.0.0.0", allow_non_loopback=True, dashboard_token=None, operator=False
        )
        assert generated is True
        assert token is not None and len(token) > 0

    def test_operator_on_loopback_generates_a_token(self) -> None:
        # The fix: an armed write-path must never be reachable unauthenticated, even
        # on loopback, so a token is generated when the caller supplied none.
        token, generated = _resolve_dashboard_token(
            "127.0.0.1", allow_non_loopback=False, dashboard_token=None, operator=True
        )
        assert generated is True
        assert token is not None and len(token) > 0

    def test_operator_on_an_exposed_bind_generates_a_token(self) -> None:
        token, generated = _resolve_dashboard_token(
            "0.0.0.0", allow_non_loopback=True, dashboard_token=None, operator=True
        )
        assert generated is True
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


async def test_operator_dashboard_on_loopback_gets_a_generated_token() -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        server = _server(uri, operator=True)
        try:
            # The write-path is armed, so a bearer token was generated (do_POST enforces
            # it because dashboard_token is no longer None) — no unauthenticated writes.
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
