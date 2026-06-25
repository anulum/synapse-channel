# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the routing hub using fake server sockets

from __future__ import annotations

import asyncio
import json
import signal
from typing import Any, cast

import pytest
from websockets.datastructures import Headers
from websockets.http11 import Request

from hub_helpers import FakeServerWS
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub import (
    InsecureBindError,
    SynapseHub,
)

# --- Sprint A: caps, capacity gate, takeover cooldown, signal handlers -------


def test_hub_caps_clamped() -> None:
    hub = SynapseHub(max_clients=0, max_msg_bytes=0, takeover_cooldown=-5.0)
    assert hub.max_clients == 1
    assert hub.max_msg_bytes == 1
    assert hub.takeover_cooldown == 0.0


async def test_handler_rejects_at_capacity() -> None:
    hub = SynapseHub(max_clients=1)
    hub.connected_clients.add(object())  # already at capacity
    ws = FakeServerWS()
    await hub.handler(ws)
    assert ws.closed == (4013, "hub at capacity")
    assert ws not in hub.connected_clients


async def test_handler_rejects_when_unauth_cap_reached() -> None:
    hub = SynapseHub(authenticator=TokenAuthenticator(["t"]), max_unauth_clients=1)
    hub.unauth_clients.add(object())  # one socket already mid-authentication
    ws = FakeServerWS()
    await hub.handler(ws)
    assert ws.closed == (4014, "too many unauthenticated connections")
    assert ws not in hub.connected_clients  # never registered


def test_max_unauth_clients_defaults_to_max_clients_and_clamps() -> None:
    assert SynapseHub(max_clients=20).max_unauth_clients == 20  # tracks max_clients
    assert SynapseHub(max_clients=20, max_unauth_clients=5).max_unauth_clients == 5
    assert SynapseHub(max_unauth_clients=0).max_unauth_clients == 1  # clamped up to 1


async def test_takeover_cooldown_blocks_rapid_eviction() -> None:
    clock = [100.0]
    hub = SynapseHub(takeover_cooldown=2.0, clock=lambda: clock[0])
    old, w1, w2, w3 = FakeServerWS(), FakeServerWS(), FakeServerWS(), FakeServerWS()
    hub.agent_sockets["A"] = old
    assert await hub._resolve_sender("A", w1, takeover=True) == "A"
    assert old.closed == (4010, "superseded")
    # a second takeover within the cooldown window is refused, protecting w1
    clock[0] = 101.0
    hub.agent_sockets["A"] = w1
    assert await hub._resolve_sender("A", w2, takeover=True) is None
    assert w2.closed == (4014, "takeover cooldown")
    # once the cooldown elapses, takeover is allowed again
    clock[0] = 103.0
    hub.agent_sockets["A"] = w1
    assert await hub._resolve_sender("A", w3, takeover=True) == "A"


def test_install_signal_handlers_wires_both() -> None:
    hub = SynapseHub()
    wired: list[int] = []

    class FakeLoop:
        def add_signal_handler(self, sig: int, callback: Any) -> None:
            wired.append(sig)

    hub._install_signal_handlers(cast(asyncio.AbstractEventLoop, FakeLoop()), asyncio.Event())
    assert signal.SIGTERM in wired
    assert signal.SIGINT in wired


def test_install_signal_handlers_suppresses_unsupported() -> None:
    hub = SynapseHub()

    class FakeLoop:
        def add_signal_handler(self, sig: int, callback: Any) -> None:
            raise NotImplementedError

    hub._install_signal_handlers(cast(asyncio.AbstractEventLoop, FakeLoop()), asyncio.Event())


# --- HTTP /metrics and /health endpoints -------------------------------------


def _request(path: str, *, authorization: str | None = None) -> Request:
    """Build an HTTP request for ``path`` with an optional ``Authorization`` header."""
    headers = Headers()
    if authorization is not None:
        headers["Authorization"] = authorization
    return Request(path, headers)


def test_metrics_disabled_by_default() -> None:
    assert SynapseHub().enable_metrics is False


def test_uptime_seconds_is_elapsed_and_never_negative() -> None:
    forward = iter([10.0, 13.5])
    hub = SynapseHub(clock=lambda: next(forward))
    assert hub.uptime_seconds() == 3.5
    backward = iter([10.0, 9.0])  # a clock that appears to go back
    hub2 = SynapseHub(clock=lambda: next(backward))
    assert hub2.uptime_seconds() == 0.0  # clamped, never negative


def test_process_request_serves_prometheus_metrics() -> None:
    hub = SynapseHub(enable_metrics=True)
    response = hub._process_request(None, _request("/metrics"))
    assert response is not None
    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("text/plain")
    assert response.headers["Content-Length"] == str(len(response.body))
    assert b"synapse_up 1" in response.body


def test_process_request_serves_health_json() -> None:
    hub = SynapseHub(hub_id="syn-probe", enable_metrics=True)
    response = hub._process_request(None, _request("/health"))
    assert response is not None
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/json"
    body = json.loads(response.body)
    assert body["status"] == "ok"
    assert body["hub_id"] == "syn-probe"


def test_process_request_ignores_a_query_string() -> None:
    hub = SynapseHub(enable_metrics=True)
    response = hub._process_request(None, _request("/metrics?step=15s"))
    assert response is not None
    assert b"synapse_up 1" in response.body


def test_process_request_falls_through_for_websocket_paths() -> None:
    hub = SynapseHub(enable_metrics=True)
    # A normal WebSocket client (or any other path) is handed back to the handshake.
    assert hub._process_request(None, _request("/")) is None
    assert hub._process_request(None, _request("/socket")) is None


def test_metrics_token_rejects_a_request_without_the_token() -> None:
    hub = SynapseHub(enable_metrics=True, metrics_token="m3tr1c")
    response = hub._process_request(None, _request("/metrics"))
    assert response is not None
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"].startswith("Bearer")
    assert b"synapse_up" not in response.body  # no metadata leaked


def test_metrics_token_admits_a_bearer_header() -> None:
    hub = SynapseHub(enable_metrics=True, metrics_token="m3tr1c")
    response = hub._process_request(None, _request("/metrics", authorization="Bearer m3tr1c"))
    assert response is not None
    assert response.status_code == 200
    assert b"synapse_up 1" in response.body


def test_metrics_token_rejects_a_query_string_token_by_default() -> None:
    # A query token can leak into logs/history, so it is ignored unless opted in.
    hub = SynapseHub(enable_metrics=True, metrics_token="m3tr1c")
    response = hub._process_request(None, _request("/metrics?token=m3tr1c"))
    assert response is not None
    assert response.status_code == 401


def test_metrics_token_admits_a_query_string_token_when_opted_in() -> None:
    hub = SynapseHub(enable_metrics=True, metrics_token="m3tr1c", metrics_query_token_ok=True)
    response = hub._process_request(None, _request("/metrics?token=m3tr1c"))
    assert response is not None
    assert response.status_code == 200
    # A non-token query parameter is skipped before the token is found.
    multi = hub._process_request(None, _request("/metrics?foo=bar&token=m3tr1c"))
    assert multi is not None
    assert multi.status_code == 200
    # An opted-in query that carries no token at all is still rejected.
    missing = hub._process_request(None, _request("/metrics?foo=bar"))
    assert missing is not None
    assert missing.status_code == 401
    # The Bearer header still works alongside the opt-in query form.
    bearer = hub._process_request(None, _request("/metrics", authorization="Bearer m3tr1c"))
    assert bearer is not None
    assert bearer.status_code == 200


def test_metrics_token_rejects_a_wrong_token() -> None:
    hub = SynapseHub(enable_metrics=True, metrics_token="m3tr1c")
    response = hub._process_request(None, _request("/health", authorization="Bearer nope"))
    assert response is not None
    assert response.status_code == 401


def test_guard_exposure_refuses_unauthenticated_metrics_off_loopback() -> None:
    hub = SynapseHub(authenticator=TokenAuthenticator(["t"]), enable_metrics=True)
    with pytest.raises(InsecureBindError, match="metrics"):
        hub._guard_exposure("0.0.0.0")


def test_guard_exposure_warns_on_unauthenticated_metrics_when_overridden(
    caplog: pytest.LogCaptureFixture,
) -> None:
    hub = SynapseHub(
        authenticator=TokenAuthenticator(["t"]),
        enable_metrics=True,
        insecure_off_loopback=True,
    )
    with caplog.at_level("WARNING", logger="synapse.hub"):
        hub._guard_exposure("0.0.0.0")
    assert any("metrics" in r.message for r in caplog.records)


def test_guard_exposure_passes_when_metrics_token_set(
    caplog: pytest.LogCaptureFixture,
) -> None:
    hub = SynapseHub(
        authenticator=TokenAuthenticator(["t"]), enable_metrics=True, metrics_token="m"
    )
    with caplog.at_level("WARNING", logger="synapse.hub"):
        hub._guard_exposure("0.0.0.0")  # both guards satisfied: no raise, no warning
    assert caplog.records == []
