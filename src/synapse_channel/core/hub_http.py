# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — HTTP admission, metrics and health responses for the hub
"""Gate HTTP requests before serving probes or admitting WebSocket upgrades."""

from __future__ import annotations

import hmac
import json
from typing import TYPE_CHECKING

from websockets.datastructures import Headers
from websockets.http11 import Request, Response

from synapse_channel.core.auth import TokenAuthenticator
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
    """Extract the metrics token from the request.

    A repeated ``Authorization`` header is ambiguous, so it yields the empty
    token (a ``401`` when a token is configured) instead of choosing one value;
    the query-string fallback is not consulted for such a request.
    """
    values = request.headers.get_all("Authorization")
    if len(values) > 1:
        return ""
    authorization = values[0] if values else ""
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
    request: Request,
    *,
    metrics_token: str | None,
    query_token_ok: bool,
    metrics_token_set: TokenAuthenticator | None = None,
) -> bool:
    """Return whether a metrics request carries a configured token, if any is configured.

    The request is admitted by the fixed ``metrics_token`` or by an entry of
    ``metrics_token_set`` that is valid now. A token set is re-read when its file
    changed, so an operator rotates the metrics bearer without a restart; its
    entries are presented no agent name, so an entry that names agents admits no
    metrics request.
    """
    if metrics_token is None and metrics_token_set is None:
        return True
    presented = request_metrics_token(request, query_token_ok=query_token_ok)
    if metrics_token is not None and hmac.compare_digest(presented, metrics_token):
        return True
    return metrics_token_set is not None and metrics_token_set.authenticate(presented, "")[0]


def http_endpoint_response(hub: SynapseHub, request: Request) -> Response | None:
    """Return the HTTP response for a probe path, or ``None`` for WebSocket paths."""
    route = request.path.split("?", 1)[0]
    if route not in ("/metrics", "/health"):
        return None
    if not metrics_authorised(
        request,
        metrics_token=hub.metrics_token,
        query_token_ok=hub.metrics_query_token_ok,
        metrics_token_set=hub.metrics_token_set,
    ):
        return http_unauthorized()
    if route == "/metrics":
        body = render_prometheus(collect_hub_metrics(hub)).encode("utf-8")
        return http_ok(body, PROMETHEUS_CONTENT_TYPE)
    body = json.dumps(health_snapshot(hub)).encode("utf-8")
    return http_ok(body, HEALTH_CONTENT_TYPE)


def http_request_response(
    hub: SynapseHub,
    request: Request,
    *,
    bind_host: str,
    bind_port: int,
) -> Response | None:
    """Apply Origin/Host admission before HTTP probes or a WebSocket upgrade.

    Parameters
    ----------
    hub:
        Current runtime configuration and live metrics source.
    request:
        Original HTTP request, including its unmodified headers.
    bind_host, bind_port:
        The listener's actual address, including a kernel-assigned port.

    Returns
    -------
    Response or None
        A refusal or enabled probe response; None admits the normal upgrade.
    """
    from synapse_channel.core.hub_handshake import (
        handshake_guard_response,
        http_forbidden,
        trusted_host_authorities,
    )

    authorities = trusted_host_authorities(
        bind_host=bind_host,
        bind_port=bind_port,
        advertised_host=hub.advertised_host,
    )
    refusal = handshake_guard_response(
        request,
        allowed_origins=hub.allowed_origins,
        trusted_authorities=authorities,
    )
    if refusal is not None:
        return refusal
    route = request.path.split("?", 1)[0]
    if route in ("/metrics", "/health"):
        if hub.enable_metrics:
            return http_endpoint_response(hub, request)
        # Metrics off: do not upgrade probe paths to WebSocket either.
        return http_forbidden("metrics disabled")
    return None
