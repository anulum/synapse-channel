# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — module-owned hub HTTP probe and authentication tests
"""Exercise HTTP response, token extraction, authorization, and route contracts."""

from __future__ import annotations

import json

import pytest
from websockets.datastructures import Headers
from websockets.http11 import Request

from synapse_channel.core import hub_http
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.metrics import HEALTH_CONTENT_TYPE, PROMETHEUS_CONTENT_TYPE


def _request(path: str, *, authorization: str | None = None) -> Request:
    """Build the HTTP request shape passed to the WebSocket server hook."""
    headers = Headers()
    if authorization is not None:
        headers["Authorization"] = authorization
    return Request(path, headers)


def test_response_builders_pin_status_headers_and_body_lengths() -> None:
    """Success and rejection responses remain complete HTTP/1.1 response objects."""
    body = b'{"status":"ok"}'
    ok = hub_http.http_ok(body, HEALTH_CONTENT_TYPE)
    assert ok.status_code == 200
    assert ok.reason_phrase == "OK"
    assert ok.headers["Content-Type"] == HEALTH_CONTENT_TYPE
    assert ok.headers["Content-Length"] == str(len(body))
    assert ok.body == body

    denied = hub_http.http_unauthorized()
    assert denied.status_code == 401
    assert denied.reason_phrase == "Unauthorized"
    assert denied.headers["Content-Type"] == "text/plain; charset=utf-8"
    assert denied.headers["Content-Length"] == str(len(denied.body))
    assert denied.headers["WWW-Authenticate"] == 'Bearer realm="synapse-metrics"'
    assert denied.body == b"unauthorized\n"


@pytest.mark.parametrize(
    ("path", "authorization", "query_token_ok", "expected"),
    [
        pytest.param(
            "/metrics?token=query",
            "Bearer  header-token  ",
            True,
            "header-token",
            id="bearer-wins-and-strips",
        ),
        pytest.param(
            "/metrics?token=query",
            "Basic ignored",
            False,
            "",
            id="query-disabled",
        ),
        pytest.param(
            "/metrics?step=15&token=query-token",
            None,
            True,
            "query-token",
            id="query-enabled-after-other-field",
        ),
        pytest.param(
            "/metrics?step=15&mode=full",
            None,
            True,
            "",
            id="query-enabled-but-absent",
        ),
    ],
)
def test_request_metrics_token_obeys_header_precedence_and_query_opt_in(
    path: str,
    authorization: str | None,
    query_token_ok: bool,
    expected: str,
) -> None:
    """Bearer tokens take precedence and URL tokens remain explicitly opt-in."""
    request = _request(path, authorization=authorization)
    assert hub_http.request_metrics_token(request, query_token_ok=query_token_ok) == expected


@pytest.mark.parametrize(
    ("metrics_token", "authorization", "query_token_ok", "expected"),
    [
        pytest.param(None, None, False, True, id="unconfigured-is-open"),
        pytest.param("secret", "Bearer secret", False, True, id="matching-bearer"),
        pytest.param("secret", "Bearer wrong", False, False, id="wrong-bearer"),
        pytest.param("secret", None, False, False, id="missing-token"),
    ],
)
def test_metrics_authorised_enforces_only_a_configured_exact_token(
    metrics_token: str | None,
    authorization: str | None,
    query_token_ok: bool,
    expected: bool,
) -> None:
    """A configured token admits only an exact extracted token."""
    request = _request("/metrics", authorization=authorization)
    assert (
        hub_http.metrics_authorised(
            request,
            metrics_token=metrics_token,
            query_token_ok=query_token_ok,
        )
        is expected
    )


def test_endpoint_falls_through_and_privately_rejects_missing_credentials() -> None:
    """WebSocket paths fall through while protected probe paths fail with 401."""
    hub = SynapseHub(metrics_token="secret")
    assert hub_http.http_endpoint_response(hub, _request("/socket?token=secret")) is None

    denied = hub_http.http_endpoint_response(hub, _request("/metrics"))
    assert denied is not None
    assert denied.status_code == 401
    assert denied.body == b"unauthorized\n"
    assert b"synapse_up" not in denied.body


def test_endpoint_renders_authorised_metrics_and_health_snapshots() -> None:
    """Both probe routes expose their production payload and content type."""
    hub = SynapseHub(
        hub_id="syn-http-test",
        metrics_token="secret",
        metrics_query_token_ok=True,
        clock=lambda: 100.0,
    )

    metrics = hub_http.http_endpoint_response(
        hub,
        _request("/metrics?step=15&token=secret"),
    )
    assert metrics is not None
    assert metrics.status_code == 200
    assert metrics.headers["Content-Type"] == PROMETHEUS_CONTENT_TYPE
    assert metrics.headers["Content-Length"] == str(len(metrics.body))
    assert b"# TYPE synapse_up gauge" in metrics.body
    assert b"synapse_up 1" in metrics.body

    health = hub_http.http_endpoint_response(
        hub,
        _request("/health?verbose=1", authorization="Bearer secret"),
    )
    assert health is not None
    assert health.status_code == 200
    assert health.headers["Content-Type"] == HEALTH_CONTENT_TYPE
    assert health.headers["Content-Length"] == str(len(health.body))
    snapshot = json.loads(health.body)
    assert snapshot["status"] == "ok"
    assert snapshot["hub_id"] == "syn-http-test"
    assert snapshot["uptime_seconds"] == 0.0
