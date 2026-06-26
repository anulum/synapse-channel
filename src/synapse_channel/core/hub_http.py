# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — HTTP metrics and health probe handling for the hub
"""HTTP metrics and health probe handling for the routing hub."""

from __future__ import annotations

import hmac
import json
from typing import TYPE_CHECKING

from websockets.datastructures import Headers
from websockets.http11 import Request, Response

from synapse_channel.core.metrics import (
    HEALTH_CONTENT_TYPE,
    PROMETHEUS_CONTENT_TYPE,
    collect_hub_metrics,
    health_snapshot,
    render_prometheus,
)

if TYPE_CHECKING:
    from synapse_channel.core.hub import SynapseHub


def http_ok(body: bytes, content_type: str) -> Response:
    """Build a ``200 OK`` HTTP response with a body and content type."""
    headers = Headers()
    headers["Content-Type"] = content_type
    headers["Content-Length"] = str(len(body))
    return Response(200, "OK", headers, body)


def http_unauthorized() -> Response:
    """Build a ``401`` response for a metrics request missing a valid token."""
    body = b"unauthorized\n"
    headers = Headers()
    headers["Content-Type"] = "text/plain; charset=utf-8"
    headers["Content-Length"] = str(len(body))
    headers["WWW-Authenticate"] = 'Bearer realm="synapse-metrics"'
    return Response(401, "Unauthorized", headers, body)


def request_metrics_token(request: Request, *, query_token_ok: bool) -> str:
    """Extract the metrics token from the request."""
    authorization = request.headers.get("Authorization", "")
    prefix = "Bearer "
    if authorization.startswith(prefix):
        return authorization[len(prefix) :].strip()
    if query_token_ok:
        _, _, query = request.path.partition("?")
        for part in query.split("&"):
            if part.startswith("token="):
                return part[len("token=") :]
    return ""


def metrics_authorised(
    request: Request, *, metrics_token: str | None, query_token_ok: bool
) -> bool:
    """Return whether a metrics request carries the configured token, if any."""
    if metrics_token is None:
        return True
    return hmac.compare_digest(
        request_metrics_token(request, query_token_ok=query_token_ok), metrics_token
    )


def http_endpoint_response(hub: SynapseHub, request: Request) -> Response | None:
    """Return the HTTP response for a probe path, or ``None`` for WebSocket paths."""
    route = request.path.split("?", 1)[0]
    if route not in ("/metrics", "/health"):
        return None
    if not metrics_authorised(
        request,
        metrics_token=hub.metrics_token,
        query_token_ok=hub.metrics_query_token_ok,
    ):
        return http_unauthorized()
    if route == "/metrics":
        body = render_prometheus(collect_hub_metrics(hub)).encode("utf-8")
        return http_ok(body, PROMETHEUS_CONTENT_TYPE)
    body = json.dumps(health_snapshot(hub)).encode("utf-8")
    return http_ok(body, HEALTH_CONTENT_TYPE)
