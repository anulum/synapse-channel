# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bind exposure checks for the A2A HTTP bridge
"""Bind exposure checks for the Agent2Agent HTTP bridge.

Mirrors the hub R4 posture from :mod:`synapse_channel.core.hub_exposure` for the
stdlib ``synapse a2a-serve`` edge: a non-loopback bind without bearer auth is
refused, and a non-loopback bind *with* bearer auth over plaintext HTTP is also
refused so the bearer never rides the LAN in the clear by default. Operators who
accept either risk pass ``--insecure-off-loopback`` (the same flag as the hub).
"""

from __future__ import annotations

from synapse_channel.core.hub_exposure import is_loopback_host

__all__ = ("a2a_bind_problems", "is_loopback_host")


def a2a_bind_problems(
    host: str,
    *,
    bearer_auth: bool,
    tls_active: bool = False,
) -> list[str]:
    """Return exposure problems for binding the A2A bridge on ``host``.

    Loopback binds never produce problems (local development is the default
    safe path). Off loopback:

    * without bearer auth the bridge is an open HTTP edge — refuse unless the
      operator opts in with ``--insecure-off-loopback``;
    * with bearer auth over plaintext HTTP (``tls_active=False``) the bearer
      and protected route traffic ride the wire in the clear — refuse unless
      the same override is set. ``tls_active=True`` clears only the plaintext
      bearer problem (native TLS or a documented TLS-terminating front); the
      A2A bridge itself is currently HTTP-only, so callers pass ``False``
      unless a future TLS bind path sets it.

    Returns a list of human-readable problem strings suitable for refuse or
    warning messages. Empty means the bind is allowed without override.
    """
    if is_loopback_host(host):
        return []
    problems: list[str] = []
    if not bearer_auth:
        problems.append(
            f"bound to non-loopback host {host!r} without --bearer-auth and "
            "--a2a-token; pass --insecure-off-loopback to bind anyway"
        )
        return problems
    if not tls_active:
        problems.append(
            f"authenticates with a bearer token on non-loopback host {host!r} "
            "over plaintext HTTP; the token and protected A2A traffic are "
            "readable on the network path — terminate TLS at a reverse proxy "
            "in front of a loopback bind, or pass --insecure-off-loopback to "
            "accept cleartext bearer traffic"
        )
    return problems
