# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dashboard bind validation and HTTP bearer-token resolution
"""Bind-address and bearer-token policy for the dashboard HTTP server.

The dashboard defaults to a loopback-only, read-only observer. Two decisions guard
its exposure, kept here as one small responsibility so the server module does not
carry them: whether a bind host may be served at all (:func:`validate_dashboard_bind`)
and what bearer token, if any, gates its HTTP requests (:func:`_resolve_dashboard_token`).
The token policy is security-critical — an armed operator write-path must never be
reachable unauthenticated, even on loopback — so it lives in its own testable unit.
"""

from __future__ import annotations

import contextlib
import ipaddress
import secrets
from typing import Final

LOOPBACK_HOSTS: Final[frozenset[str]] = frozenset({"localhost", "127.0.0.1", "::1"})


def _is_loopback_host(host: str) -> bool:
    """Return whether ``host`` names a loopback-only bind target."""
    candidate = host.strip().lower()
    if candidate in LOOPBACK_HOSTS:
        return True
    with contextlib.suppress(ValueError):
        return ipaddress.ip_address(candidate).is_loopback
    return False


def validate_dashboard_bind(host: str, *, allow_non_loopback: bool) -> None:
    """Refuse non-loopback dashboard binds unless explicitly allowed.

    Parameters
    ----------
    host : str
        Host literal or name passed to the dashboard HTTP server.
    allow_non_loopback : bool
        Whether the caller explicitly accepts exposing the read-only dashboard
        beyond loopback.

    Raises
    ------
    ValueError
        If ``host`` is not a loopback target and ``allow_non_loopback`` is false.
    """
    if allow_non_loopback or _is_loopback_host(host):
        return
    raise ValueError(
        "dashboard binds to loopback by default; pass --allow-non-loopback "
        "only behind trusted local network controls"
    )


def _resolve_dashboard_token(
    *,
    dashboard_token: str | None,
) -> tuple[str, bool, bool]:
    """Return the effective dashboard HTTP bearer token and what it protects.

    Live/page reads are gated by a bearer in every posture. A caller-supplied token
    gates reads and writes; when the caller supplies none the server generates one —
    printed to the operator so the cockpit unlock veil can present it — so a same-host
    process cannot pull the cockpit's live data unbidden, on loopback too. Writes are
    separately gated whenever the operator write-path is armed. The validated React
    cockpit shell stays the one public exception: navigation cannot carry an
    ``Authorization`` header, so its static files load before the veil, which then
    authenticates every live read.

    Parameters
    ----------
    dashboard_token : str or None
        Caller-provided dashboard HTTP bearer token. When ``None`` the server
        generates a read-gating token and reports it as generated.

    Returns
    -------
    tuple[str, bool, bool]
        The effective bearer token (never empty), whether the server generated it,
        and whether it protects live/page reads — always ``True`` now that reads are
        never left open, even on loopback.

    Raises
    ------
    ValueError
        If the caller supplied an empty dashboard token.
    """
    if dashboard_token is not None and len(dashboard_token) == 0:
        raise ValueError("dashboard token must not be empty")
    if dashboard_token is not None:
        # An operator-supplied token gates live/page reads and writes. The handler
        # retains one narrow exception for validated React-shell static files.
        return dashboard_token, False, True
    # No caller token: generate a read-gating token (it also gates writes when the
    # operator path is armed), printed to the operator so the cockpit unlock veil can
    # authenticate. Reads are never left open — not even on loopback — so a same-host
    # process cannot pull live cockpit data without presenting the bearer.
    return secrets.token_urlsafe(32), True, True
