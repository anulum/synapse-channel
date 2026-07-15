# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dashboard Host-header DNS-rebinding boundary
"""Always-on Host-header boundary for the dashboard HTTP surface.

The dashboard defaults to a loopback-only read observer whose live JSON and audit
feeds are unauthenticated so a browser cockpit — which cannot attach an
``Authorization`` header on navigation — can still load. That open read path is a
DNS-rebinding target: a page the operator visits can rebind its own name to the
loopback address and, because the request is then same-origin from the browser's
view, read the coordination state cross-origin. The transport-level barrier is the
HTTP ``Host`` header, which still carries the attacker-chosen authority rather than
the loopback one.

This module derives, from the server's own bind, the exact set of authorities a
legitimate browser presents — the loopback names and the concrete bind host at the
served port, plus any host the operator has explicitly approved for a deliberate
LAN or reverse-proxy exposure — and decides whether one request's ``Host`` header
is among them. The check is always on and fail-closed: a request whose ``Host`` is
absent, malformed, or unlisted is refused before authentication runs, so the guard
holds even where reads are intentionally open.
"""

from __future__ import annotations

import ipaddress
from typing import Final
from urllib.parse import urlsplit

from synapse_channel.a2a_http_protocol import normalise_authority

_LOOPBACK_HOST_NAMES: Final[tuple[str, ...]] = ("localhost", "127.0.0.1", "::1")
"""Host names a browser presents when reaching the dashboard over loopback."""


def is_unspecified_host(host: str) -> bool:
    """Return whether ``host`` is the wildcard (unspecified) bind address.

    A ``0.0.0.0`` or ``::`` bind accepts connections to every local address, so no
    fixed authority set describes it — the value itself is never sent as a ``Host``.
    Such a bind is off loopback and mandates a read-protecting token, which already
    defeats rebinding, so the caller relaxes the ``Host`` boundary for it unless the
    operator narrowed the admissible hosts.

    Parameters
    ----------
    host : str
        Concrete bind host, optionally bracketed.

    Returns
    -------
    bool
        ``True`` only for the IPv4 or IPv6 unspecified address.
    """
    try:
        return ipaddress.ip_address(host.strip().strip("[]")).is_unspecified
    except ValueError:
        return False


def _bracket(host: str) -> str:
    """Return ``host`` with an IPv6 literal bracketed for authority parsing."""
    candidate = host.strip()
    if ":" in candidate and not candidate.startswith("["):
        return f"[{candidate}]"
    return candidate


def _authority_at(host: str, port: int) -> str:
    """Return the normalised ``host:port`` authority for one host at ``port``."""
    return normalise_authority(f"{_bracket(host)}:{port}")


def _host_forms(host: str, port: int) -> set[str]:
    """Return both the bare and served-port authorities for one approved host.

    A conformant browser on a non-default port sends ``host:port``, but a lenient
    client or a reverse proxy that drops the default port sends the bare ``host``.
    Both are admitted for an approved host; a differing explicit port is not, so a
    request claiming ``host:<other-port>`` is still refused.
    """
    return {normalise_authority(_bracket(host)), _authority_at(host, port)}


def _extra_authorities(extra: str, port: int) -> set[str]:
    """Return the authorities admitted for one operator-approved host.

    An entry carrying an explicit port is honoured exactly, so an operator behind
    a reverse proxy on a fixed port admits only that authority. A port-less entry
    admits both the bare authority and the same host at the served port.
    """
    candidate = extra.strip()
    if not candidate:
        return set()
    if urlsplit(f"//{candidate}").port is not None:
        return {normalise_authority(candidate)}
    return _host_forms(candidate, port)


def allowed_host_authorities(
    bind_host: str, port: int, extra_hosts: tuple[str, ...] = ()
) -> frozenset[str]:
    """Return every ``Host`` authority the dashboard admits at ``port``.

    The set is the loopback names and the concrete bind host at the served port,
    plus every operator-approved extra host. A legitimate browser reaching the
    dashboard over loopback (or the approved authority) presents one of these; a
    DNS-rebinding page reaches it under an attacker-chosen authority that is absent
    from the set and is refused. A wildcard bind host is omitted because no client
    sends it as a ``Host``; the caller relaxes the boundary for such a bind (see
    :func:`is_unspecified_host`).

    Parameters
    ----------
    bind_host : str
        Concrete host the HTTP server bound, such as ``127.0.0.1``. A wildcard
        ``0.0.0.0`` or ``::`` contributes no bind authority.
    port : int
        Concrete TCP port the server is served on.
    extra_hosts : tuple of str, optional
        Operator-approved additional hosts for a deliberate LAN or reverse-proxy
        exposure. Each is a host name or ``host[:port]`` authority; an IPv6
        literal must be bracketed.

    Returns
    -------
    frozenset of str
        The normalised authorities admitted in the ``Host`` header, each host in
        both its bare and served-port form.

    Raises
    ------
    ValueError
        If an ``extra_hosts`` entry is not a valid host authority. The bind host
        and loopback names are trusted inputs and are not expected to raise.
    """
    authorities: set[str] = set()
    for name in _LOOPBACK_HOST_NAMES:
        authorities |= _host_forms(name, port)
    if not is_unspecified_host(bind_host):
        # A wildcard bind is a dead authority no client sends; skip it and rely on
        # the loopback names plus any operator-approved hosts.
        authorities |= _host_forms(bind_host, port)
    for extra in extra_hosts:
        authorities |= _extra_authorities(extra, port)
    return frozenset(authorities)


def host_allowed(host_header: str | None, allowed: frozenset[str]) -> bool:
    """Return whether a request's ``Host`` header names an admitted authority.

    Parameters
    ----------
    host_header : str or None
        The request's ``Host`` header, or ``None`` when absent.
    allowed : frozenset of str
        Authorities from :func:`allowed_host_authorities`.

    Returns
    -------
    bool
        ``True`` only when the header parses to one admitted authority. An absent
        or malformed header is refused so the boundary fails closed.
    """
    if not host_header:
        return False
    try:
        return normalise_authority(host_header) in allowed
    except ValueError:
        return False
