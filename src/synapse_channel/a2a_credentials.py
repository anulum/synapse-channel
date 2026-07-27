# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — A2A bearer custody and plaintext transport guard
"""Resolve A2A bearer files and keep credentials off remote cleartext HTTP."""

from __future__ import annotations

from ipaddress import ip_address
from pathlib import Path

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.secret_files import read_secret_file


class A2APlaintextBearerError(SynapseError, ValueError):
    """Raised before an A2A bearer could leave over remote plaintext HTTP."""

    code = "a2a_plaintext_bearer"


def resolve_a2a_token(
    token: str | None,
    token_file: str | Path | None,
) -> str | None:
    """Resolve an A2A bearer with explicit argv-over-owner-file precedence.

    The file is not opened when ``token`` is non-empty. File errors are emitted
    by the shared same-descriptor owner-only loader and never include its value.
    """
    if token:
        return str(token)
    if token_file:
        return read_secret_file(token_file, flag="--a2a-token-file")
    return None


def is_literal_loopback_host(host: str) -> bool:
    """Return whether ``host`` is a literal IPv4 or IPv6 loopback address."""
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def guard_a2a_bearer_transport(
    *,
    scheme: str,
    host: str,
    token: str | None,
    allow_insecure_http: bool = False,
) -> None:
    """Refuse a bearer over plaintext HTTP outside a literal loopback address."""
    if not token or scheme.strip().lower() != "http":
        return
    if is_literal_loopback_host(host) or allow_insecure_http:
        return
    raise A2APlaintextBearerError(
        "refusing A2A bearer over plaintext HTTP outside a literal loopback IP; "
        "use HTTPS or pass --a2a-allow-insecure-http to accept cleartext credential risk"
    )


__all__ = [
    "A2APlaintextBearerError",
    "guard_a2a_bearer_transport",
    "is_literal_loopback_host",
    "resolve_a2a_token",
]
