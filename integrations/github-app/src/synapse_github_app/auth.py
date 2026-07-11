# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE GITHUB APP — App JWT and installation-token contracts
"""Create short-lived App JWTs and validate installation-token responses."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import cast

import jwt

from synapse_github_app.errors import AuthenticationError, PayloadError


@dataclass(frozen=True)
class InstallationToken:
    """Opaque GitHub App installation token and its declared expiry."""

    value: str
    expires_at: datetime


def create_app_jwt(*, issuer: str, private_key_pem: bytes, now: datetime | None = None) -> str:
    """Create a clock-skew-tolerant RS256 GitHub App JWT.

    The token is backdated by 60 seconds and expires nine minutes after the
    supplied clock, remaining below GitHub's ten-minute maximum.
    """
    if not issuer or len(issuer) > 255 or not issuer.isprintable():
        raise AuthenticationError("App issuer must be a printable non-empty identifier")
    if not private_key_pem:
        raise AuthenticationError("App private key must not be empty")
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise AuthenticationError("JWT clock must be timezone-aware")
    issued_at = int(instant.timestamp()) - 60
    expires_at = int((instant + timedelta(minutes=9)).timestamp())
    try:
        encoded = jwt.encode(
            {"iat": issued_at, "exp": expires_at, "iss": issuer},
            private_key_pem,
            algorithm="RS256",
        )
    except (jwt.PyJWTError, ValueError, TypeError) as exc:
        raise AuthenticationError("unable to sign the GitHub App JWT") from exc
    return encoded


def parse_installation_token(value: object) -> InstallationToken:
    """Validate an installation-token REST response without prefix assumptions."""
    if not isinstance(value, dict):
        raise PayloadError("installation token response must be an object")
    payload = cast(Mapping[str, object], value)
    token = payload.get("token")
    expiry = payload.get("expires_at")
    if not isinstance(token, str) or not token or len(token) > 8192 or not token.isprintable():
        raise PayloadError("installation token is invalid")
    if not isinstance(expiry, str) or not expiry:
        raise PayloadError("installation token expiry is invalid")
    try:
        expires_at = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PayloadError("installation token expiry is invalid") from exc
    if expires_at.tzinfo is None:
        raise PayloadError("installation token expiry must include a timezone")
    return InstallationToken(value=token, expires_at=expires_at.astimezone(timezone.utc))
