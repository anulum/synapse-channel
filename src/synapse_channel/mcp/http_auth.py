# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — remote MCP bearer verification
"""Verify issuer-bound access tokens without granting authority from token claims."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import jwt
from mcp.server.auth.provider import AccessToken

from synapse_channel.mcp.http_config import HttpConfigError, RemoteSubject, load_http_auth_config


@dataclass(frozen=True)
class AuthenticatedHttpPrincipal:
    """Keep verified bearer identity and operator authority from one policy read.

    Attributes
    ----------
    access_token : AccessToken
        Verified issuer identity and token scopes for the SDK authentication backend.
    authority : RemoteSubject
        Current operator grants; these never originate in access-token claims.
    """

    access_token: AccessToken = field(repr=False)
    authority: RemoteSubject = field(repr=False)


class HttpTokenVerifier:
    """Verify Ed25519 access tokens and reload operator revocations on every request.

    Parameters
    ----------
    policy_file : str or pathlib.Path
        Owner-only issuer keys, principal grants and revocation policy.

    Notes
    -----
    This is an OAuth resource-server verifier, not an authorization server. The
    issuer issues tokens; its discovery and consent flow remain issuer-owned.
    Startup issuer and resource identifiers are immutable until server restart.
    """

    def __init__(self, policy_file: str | Path) -> None:
        self.policy_file = Path(policy_file)
        config = load_http_auth_config(self.policy_file)
        self.issuer = config.issuer
        self.resource = config.resource

    async def verify_token(self, token: str) -> AccessToken | None:
        """Adapt a freshly authenticated principal to the SDK token-verifier protocol.

        Parameters
        ----------
        token : str
            HTTP bearer, checked against current issuer policy.

        Returns
        -------
        AccessToken or None
            Authenticated SDK identity, or a uniform rejection.
        """
        principal = await self.authenticate(token)
        return principal.access_token if principal is not None else None

    async def authenticate(self, token: str) -> AuthenticatedHttpPrincipal | None:
        """Authenticate one bearer using current grants, audience, expiry and revocation.

        Parameters
        ----------
        token : str
            Bearer received in an HTTP Authorization header; never logged.

        Returns
        -------
        AuthenticatedHttpPrincipal or None
            Verified identity and operator grants from one consistent policy read.
        """
        if not token or len(token) > 8192:
            return None
        try:
            config = load_http_auth_config(self.policy_file)
            if config.issuer != self.issuer or config.resource != self.resource:
                return None
            header = jwt.get_unverified_header(token)
            key_id = header.get("kid")
            if header.get("alg") != "EdDSA" or not isinstance(key_id, str):
                return None
            key = config.public_keys.get(key_id)
            if key is None:
                return None
            claims = jwt.decode(
                token,
                key,
                algorithms=["EdDSA"],
                issuer=self.issuer,
                audience=self.resource,
                options={
                    "require": ["iss", "sub", "aud", "exp", "iat", "jti", "client_id", "scope"],
                    "strict_aud": True,
                },
            )
            subject, token_id, client_id = claims["sub"], claims["jti"], claims["client_id"]
            scope = claims["scope"]
            issued_at, expires_at = claims["iat"], claims["exp"]
            if (
                not all(
                    isinstance(value, str) and 0 < len(value) <= 256
                    for value in (subject, token_id, client_id)
                )
                or not isinstance(scope, str)
                or len(scope) > 256
                or type(issued_at) is not int
                or type(expires_at) is not int
            ):
                return None
            authority = config.subjects.get(subject)
            now = time.time()
            if (
                authority is None
                or not authority.enabled
                or token_id in config.revoked_token_ids
                or issued_at <= authority.revoked_before
                or not 0 < expires_at - issued_at <= config.max_token_age_seconds
                or now - issued_at > config.max_token_age_seconds
            ):
                return None
            scopes = set(scope.split())
            if "synapse:read" not in scopes or not scopes <= {"synapse:read", "synapse:mutate"}:
                return None
            access_token = AccessToken(
                token=token,
                client_id=client_id,
                subject=subject,
                scopes=sorted(scopes),
                expires_at=expires_at,
                resource=self.resource,
                claims={"iss": self.issuer, "jti": token_id},
            )
            return AuthenticatedHttpPrincipal(access_token, authority)
        except (HttpConfigError, jwt.PyJWTError, ValueError, TypeError):
            return None
