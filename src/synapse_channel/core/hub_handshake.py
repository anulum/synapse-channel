# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — WebSocket handshake Origin/Host boundary for the routing hub
"""Browser and Host boundary for the hub WebSocket handshake (K3-F1).

Hostile pages must not complete a WebSocket upgrade against a local or advertised
hub and then request history. This module is pure policy: it never opens a socket
and never reads a frame body. The hub installs it as the always-on
``process_request`` hook so Origin/Host enforcement runs even when HTTP metrics
are disabled.

Policy summary
--------------
* Trusted Host authorities are derived from the bind address, listen port, and
  optional advertised host — never from a client-supplied value alone.
* A request that presents ``Origin`` (including the opaque ``null`` token or any
  malformed value) is a browser-shaped request: it is admitted only when that
  origin is on an explicit concrete allow-list **and** Host matches a trusted
  authority. With an empty allow-list, every browser Origin is refused.
* An origin-less request (native CLI / agent clients) is admitted only when Host
  matches a trusted authority, so DNS-rebinding cannot masquerade as a native
  client.
* Off-loopback binds without an advertised host admit no Host authority until
  the operator configures one — fail closed rather than wildcard trust.
"""

from __future__ import annotations

from websockets.datastructures import Headers
from websockets.http11 import Request, Response

from synapse_channel.a2a_http_protocol import (
    normalise_authority,
    normalise_origin,
)
from synapse_channel.core.rate_policy import is_loopback_bind

_LOOPBACK_NAMES: tuple[str, ...] = ("localhost", "127.0.0.1", "::1", "::ffff:127.0.0.1")


def http_forbidden(detail: str = "origin/host not allowed") -> Response:
    """Build a deterministic ``403`` refusal for a disallowed handshake."""
    body = f"{detail}\n".encode()
    headers = Headers()
    headers["Content-Type"] = "text/plain; charset=utf-8"
    headers["Content-Length"] = str(len(body))
    return Response(403, "Forbidden", headers, body)


def trusted_host_authorities(
    *,
    bind_host: str,
    bind_port: int,
    advertised_host: str | None = None,
) -> tuple[str, ...]:
    """Return exact Host authorities the handshake admits for this bind.

    Parameters
    ----------
    bind_host : str
        The address passed to ``websockets.serve`` / ``synapse hub --host``.
    bind_port : int
        The listening port.
    advertised_host : str or None
        Optional operator-advertised authority (``host`` or ``host:port``) for
        reverse-proxy or off-loopback deployment. Required for non-loopback
        binds that use a bind-all sentinel (``0.0.0.0`` / ``::``).

    Returns
    -------
    tuple[str, ...]
        Normalised ``host[:port]`` authorities, sorted. Empty when no safe
        authority can be derived (fail closed).
    """
    authorities: set[str] = set()
    port = int(bind_port)
    if is_loopback_bind(bind_host) or bind_host.strip().lower() in {
        name.lower() for name in _LOOPBACK_NAMES
    }:
        for name in _LOOPBACK_NAMES:
            authorities.update(_authorities_for_host(name, port))
    else:
        candidate = bind_host.strip()
        if candidate and candidate not in {"0.0.0.0", "::", "[::]"}:
            authorities.update(_authorities_for_host(candidate, port))
    if advertised_host:
        authorities.update(_authorities_from_advertised(advertised_host, port))
    return tuple(sorted(authorities))


def handshake_allowed(
    *,
    origin_header: str | None,
    host_header: str | None,
    allowed_origins: tuple[str, ...],
    trusted_authorities: tuple[str, ...],
) -> bool:
    """Return whether a WebSocket upgrade may proceed under the handshake policy.

    Parameters
    ----------
    origin_header : str or None
        Raw ``Origin`` header, or ``None`` when absent.
    host_header : str or None
        Raw ``Host`` header.
    allowed_origins : tuple[str, ...]
        Normalised concrete origins operators explicitly allow. Empty means no
        browser Origin is admitted.
    trusted_authorities : tuple[str, ...]
        Normalised Host authorities derived from bind/advertised configuration.

    Returns
    -------
    bool
        ``True`` only when Host is trusted and, if Origin is present, it is an
        exact allow-list member.
    """
    if not trusted_authorities:
        return False
    try:
        authority = normalise_authority(host_header or "")
    except ValueError:
        return False
    if authority not in trusted_authorities:
        return False
    if origin_header is None:
        return True
    if not allowed_origins:
        return False
    try:
        origin = normalise_origin(origin_header)
    except ValueError:
        return False
    return origin in allowed_origins


def normalise_allow_origins(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Normalise operator ``--allow-origin`` values; raise on unsafe entries."""
    return tuple(normalise_origin(value) for value in values)


def handshake_guard_response(
    request: Request,
    *,
    allowed_origins: tuple[str, ...],
    trusted_authorities: tuple[str, ...],
) -> Response | None:
    """Return a ``403`` when the upgrade must not proceed; else ``None``.

    Metrics and health paths are not handled here — the hub routes those first.
    """
    origin = request.headers.get("Origin")
    # websockets may omit the header entirely; treat missing as origin-less.
    if origin is not None and origin == "":
        origin = None
    host = request.headers.get("Host")
    if handshake_allowed(
        origin_header=origin,
        host_header=host,
        allowed_origins=allowed_origins,
        trusted_authorities=trusted_authorities,
    ):
        return None
    return http_forbidden()


def _authorities_for_host(host: str, port: int) -> frozenset[str]:
    """Return host and host:port authority forms for one name."""
    values: set[str] = set()
    for raw in (host, f"{host}:{port}"):
        try:
            values.add(normalise_authority(raw))
        except ValueError:
            continue
    # IPv6 with brackets for Host headers
    if ":" in host and not host.startswith("["):
        for raw in (f"[{host}]", f"[{host}]:{port}"):
            try:
                values.add(normalise_authority(raw))
            except ValueError:
                continue
    return frozenset(values)


def _authorities_from_advertised(advertised: str, bind_port: int) -> frozenset[str]:
    """Expand an advertised host or host:port into trusted authorities."""
    candidate = advertised.strip()
    if not candidate:
        return frozenset()
    if "://" in candidate:
        try:
            origin = normalise_origin(candidate)
        except ValueError:
            return frozenset()
        candidate = origin.split("://", 1)[1]
    try:
        primary = normalise_authority(candidate)
    except ValueError:
        return frozenset()
    values = {primary}
    # When the operator omitted a port, also admit the bind port form.
    if ":" not in candidate.rstrip("]").rsplit("]", 1)[-1]:
        try:
            values.add(normalise_authority(f"{candidate}:{bind_port}"))
        except ValueError:
            pass
    return frozenset(values)
