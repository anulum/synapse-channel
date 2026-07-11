# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE GITHUB APP — real RS256 and installation-token tests
"""Verify GitHub App authentication without exposing or assuming token formats."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest

from crypto_material import rsa_pem_pair
from synapse_github_app.auth import create_app_jwt, parse_installation_token
from synapse_github_app.errors import AuthenticationError, PayloadError


def test_app_jwt_has_real_rs256_signature_and_bounded_claims() -> None:
    private_pem, public_pem = rsa_pem_pair()
    now = datetime(2026, 7, 11, 14, 0, tzinfo=timezone.utc)

    encoded = create_app_jwt(issuer="Iv1.client", private_key_pem=private_pem, now=now)
    claims = jwt.decode(
        encoded,
        public_pem,
        algorithms=["RS256"],
        options={"verify_exp": False, "verify_iat": False},
    )

    assert claims == {
        "iat": int(now.timestamp()) - 60,
        "exp": int((now + timedelta(minutes=9)).timestamp()),
        "iss": "Iv1.client",
    }


@pytest.mark.parametrize("issuer", ["", "x\n", "x" * 256])
def test_app_jwt_rejects_invalid_issuer(issuer: str) -> None:
    private_pem, _ = rsa_pem_pair()
    with pytest.raises(AuthenticationError, match="issuer"):
        create_app_jwt(issuer=issuer, private_key_pem=private_pem)


def test_app_jwt_rejects_empty_invalid_key_and_naive_clock() -> None:
    with pytest.raises(AuthenticationError, match="must not be empty"):
        create_app_jwt(issuer="app", private_key_pem=b"")
    with pytest.raises(AuthenticationError, match="unable to sign"):
        create_app_jwt(issuer="app", private_key_pem=b"not-a-key")
    private_pem, _ = rsa_pem_pair()
    with pytest.raises(AuthenticationError, match="timezone-aware"):
        create_app_jwt(
            issuer="app",
            private_key_pem=private_pem,
            now=datetime(2026, 7, 11),
        )


def test_installation_token_is_opaque_and_expiry_is_normalized() -> None:
    token = "future_format_" + "x" * 91
    parsed = parse_installation_token({"token": token, "expires_at": "2026-07-11T15:00:00Z"})

    assert parsed.value == token
    assert parsed.expires_at == datetime(2026, 7, 11, 15, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"token": "", "expires_at": "2026-07-11T15:00:00Z"},
        {"token": "x" * 8193, "expires_at": "2026-07-11T15:00:00Z"},
        {"token": "x\n", "expires_at": "2026-07-11T15:00:00Z"},
        {"token": "opaque", "expires_at": None},
        {"token": "opaque", "expires_at": "not-a-date"},
        {"token": "opaque", "expires_at": "2026-07-11T15:00:00"},
    ],
)
def test_installation_token_rejects_malformed_responses(payload: object) -> None:
    with pytest.raises(PayloadError):
        parse_installation_token(payload)
