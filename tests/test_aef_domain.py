# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Agent Evidence Format domain-separation regressions

from __future__ import annotations

from typing import cast

import pytest

from synapse_channel.core.aef_canonical import IJSON_MAX_INTEGER
from synapse_channel.core.aef_domain import (
    AEF_RECEIPT_DOMAIN,
    AEF_STH_DOMAIN,
    AEF_WITNESS_COSIGN_DOMAIN,
    MAX_AEF_PURPOSE_LENGTH,
    AefDomain,
    AefDomainError,
    aef_signature_preimage,
    parse_aef_domain,
)


@pytest.mark.parametrize(
    ("domain", "expected"),
    [
        (AEF_RECEIPT_DOMAIN, "aef:receipt:v0.1"),
        (AEF_STH_DOMAIN, "aef:sth:v0.1"),
        (AEF_WITNESS_COSIGN_DOMAIN, "aef:witness-cosig:v0.1"),
        (AefDomain("vendor1-receipt", 12, 34), "aef:vendor1-receipt:v12.34"),
    ],
)
def test_domains_render_canonical_ascii(domain: AefDomain, expected: str) -> None:
    assert str(domain) == expected
    assert parse_aef_domain(expected) == domain


def test_preimage_uses_exact_domain_nul_payload_shape() -> None:
    payload = b'{"seq":7}'

    assert AEF_RECEIPT_DOMAIN.preimage(payload) == b"aef:receipt:v0.1\x00" + payload
    assert aef_signature_preimage("aef:receipt:v0.1", payload) == (
        b"aef:receipt:v0.1\x00" + payload
    )


def test_extension_purpose_accepts_the_maximum_length() -> None:
    purpose = "a" * MAX_AEF_PURPOSE_LENGTH

    assert str(AefDomain(purpose, 0, 1)) == f"aef:{purpose}:v0.1"


@pytest.mark.parametrize(
    "purpose",
    [
        "",
        "Receipt",
        "receipt_kind",
        "receipt--proof",
        "-receipt",
        "receipt-",
        "receipt.proof",
        "réceipt",
        "1receipt",
        "a" * (MAX_AEF_PURPOSE_LENGTH + 1),
    ],
)
def test_invalid_purpose_tokens_are_rejected(purpose: str) -> None:
    with pytest.raises(AefDomainError):
        AefDomain(purpose, 0, 1)


def test_non_text_purpose_is_rejected() -> None:
    with pytest.raises(AefDomainError, match="purpose must be text"):
        AefDomain(cast(str, 1), 0, 1)


@pytest.mark.parametrize(
    "raw",
    [
        "aef:receipt:v00.1",
        "aef:receipt:v0.01",
        "aef:receipt:v-1.0",
        "aef:receipt:v0.-1",
        "aef:receipt:v0.1\x00",
        "aef:receipt:v0.1\n",
        "AEF:receipt:v0.1",
        "aef:receipt:V0.1",
        "aef::v0.1",
        "receipt:v0.1",
        "aef:receipt:v0.1:extra",
    ],
)
def test_noncanonical_domain_spellings_are_rejected(raw: str) -> None:
    with pytest.raises(AefDomainError, match="invalid AEF signature domain"):
        parse_aef_domain(raw)


@pytest.mark.parametrize("raw", [None, b"aef:receipt:v0.1", 1, True])
def test_domain_parser_requires_text(raw: object) -> None:
    with pytest.raises(AefDomainError, match="domain must be text"):
        parse_aef_domain(raw)


@pytest.mark.parametrize(
    ("major", "minor"),
    [
        (-1, 0),
        (0, -1),
        (True, 0),
        (0, False),
        (1.0, 0),
        (0, 1.0),
        (IJSON_MAX_INTEGER + 1, 0),
        (0, IJSON_MAX_INTEGER + 1),
    ],
)
def test_version_components_are_bounded_non_boolean_integers(major: object, minor: object) -> None:
    with pytest.raises(AefDomainError, match="I-JSON non-negative integer"):
        AefDomain("receipt", cast(int, major), cast(int, minor))


def test_parser_contains_an_oversized_version_literal() -> None:
    with pytest.raises(AefDomainError, match="I-JSON non-negative integer"):
        parse_aef_domain(f"aef:receipt:v{'9' * 5000}.1")


def test_parser_enforces_version_bounds_after_conversion() -> None:
    with pytest.raises(AefDomainError, match="I-JSON non-negative integer"):
        parse_aef_domain(f"aef:receipt:v{IJSON_MAX_INTEGER + 1}.0")


@pytest.mark.parametrize("payload", [bytearray(b"x"), memoryview(b"x"), "x", None])
def test_preimage_requires_immutable_bytes(payload: object) -> None:
    with pytest.raises(AefDomainError, match="payload must be bytes"):
        AEF_RECEIPT_DOMAIN.preimage(cast(bytes, payload))


def test_preimage_rejects_a_non_domain_object() -> None:
    with pytest.raises(AefDomainError, match="text or AefDomain"):
        aef_signature_preimage(cast("AefDomain | str", 1), b"payload")
