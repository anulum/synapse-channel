# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — signed capability-card verification tests
"""Security and lifecycle matrix for advisory card verification."""

from __future__ import annotations

import copy

import pytest

from synapse_channel.core.capability_card_signing import sign_capability_card
from synapse_channel.core.capability_card_trust import (
    CapabilityCardHistory,
    CapabilityCardTrustBundle,
)
from synapse_channel.core.capability_card_verification import (
    CapabilityCardVerification,
    CapabilityCardVerificationResult,
    verify_capability_card,
)
from synapse_channel.core.identity_keys import generate_signing_key
from synapse_channel.core.message_auth import EventSignatureKey


def _card() -> dict[str, object]:
    return {
        "agent": "P/worker",
        "project": "P",
        "description": "worker",
        "skills": ["python"],
        "task_classes": ["code"],
        "contracts": [{"task_class": "code"}],
        "meta": {},
        "manifest_digest": "sha256:abc",
    }


def _fixture(
    *,
    sequence: int = 1,
    signed_at: float = 100.0,
    expires_at: float = 200.0,
    revoked: bool = False,
    key_expires_at: float | None = None,
    history: CapabilityCardHistory | None = None,
) -> tuple[dict[str, object], CapabilityCardTrustBundle]:
    private = generate_signing_key()
    signed = sign_capability_card(
        _card(),
        key_id="P:key",
        private_key=private,
        sequence=sequence,
        signed_at=signed_at,
        expires_at=expires_at,
    )
    key = EventSignatureKey.from_private_key(
        key_id="P:key",
        private_key=private,
        senders=frozenset({"P/worker"}),
        projects=frozenset({"P"}),
        expires_at=key_expires_at,
        revoked=revoked,
    )
    return signed, CapabilityCardTrustBundle(
        keys={key.key_id: key},
        history=history or CapabilityCardHistory(),
        clock_skew_seconds=2.0,
    )


def _verify(
    card: dict[str, object],
    trust: CapabilityCardTrustBundle,
    **overrides: object,
) -> CapabilityCardVerification:
    kwargs: dict[str, object] = {
        "now": 150.0,
        "required_agent": "P/worker",
        "required_project": "P",
        "required_manifest_digest": "sha256:abc",
    }
    kwargs.update(overrides)
    return verify_capability_card(card, trust_bundle=trust, **kwargs)  # type: ignore[arg-type]


def test_valid_card_projects_complete_diagnostics() -> None:
    signed, trust = _fixture()
    result = _verify(signed, trust)

    assert result.result is CapabilityCardVerificationResult.VALID
    assert result.as_dict() == {
        "card_digest": signed["signature"]["card_digest"],  # type: ignore[index]
        "detail": "signature, bindings, expiry, and lifecycle checks passed",
        "expires_at": 200.0,
        "key_id": "P:key",
        "result": "valid",
        "sequence": 1,
        "signed_at": 100.0,
    }


def test_unsigned_and_non_object_signatures_remain_visible() -> None:
    _signed, trust = _fixture()
    unsigned = _card()
    assert _verify(unsigned, trust).result is CapabilityCardVerificationResult.MISSING_SIGNATURE
    malformed = {**unsigned, "signature": "bad"}
    assert _verify(malformed, trust).result is CapabilityCardVerificationResult.BAD_SIGNATURE


def test_unknown_revoked_and_expired_keys_are_distinct() -> None:
    signed, trust = _fixture()
    assert (
        _verify(signed, CapabilityCardTrustBundle(keys={})).result
        is CapabilityCardVerificationResult.UNKNOWN_KEY
    )
    revoked, revoked_trust = _fixture(revoked=True)
    assert _verify(revoked, revoked_trust).result is CapabilityCardVerificationResult.REVOKED_KEY
    expired, expired_trust = _fixture(key_expires_at=149.0)
    assert _verify(expired, expired_trust).result is CapabilityCardVerificationResult.EXPIRED


def test_verification_refuses_non_finite_now_and_binding_mismatches() -> None:
    signed, trust = _fixture()
    assert (
        _verify(signed, trust, now=float("nan")).result
        is CapabilityCardVerificationResult.BAD_SIGNATURE
    )
    assert (
        _verify(signed, trust, required_agent="P/other").result
        is CapabilityCardVerificationResult.AGENT_MISMATCH
    )
    assert (
        _verify(signed, trust, required_project="Q").result
        is CapabilityCardVerificationResult.PROJECT_SCOPE_MISMATCH
    )
    assert (
        _verify(signed, trust, required_manifest_digest="sha256:other").result
        is CapabilityCardVerificationResult.MANIFEST_MISMATCH
    )


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("version", 2, CapabilityCardVerificationResult.BAD_SIGNATURE),
        ("algorithm", "rsa", CapabilityCardVerificationResult.BAD_SIGNATURE),
        ("sequence", 0, CapabilityCardVerificationResult.SEQUENCE_MISMATCH),
        ("sequence", True, CapabilityCardVerificationResult.SEQUENCE_MISMATCH),
        ("signed_at", "bad", CapabilityCardVerificationResult.BAD_SIGNATURE),
        ("signed_at", "100", CapabilityCardVerificationResult.BAD_SIGNATURE),
        ("expires_at", True, CapabilityCardVerificationResult.BAD_SIGNATURE),
        ("expires_at", None, CapabilityCardVerificationResult.BAD_SIGNATURE),
        ("card_digest", "", CapabilityCardVerificationResult.BAD_SIGNATURE),
        ("signed_at", float("inf"), CapabilityCardVerificationResult.BAD_SIGNATURE),
    ],
)
def test_malformed_signature_metadata_fails_closed(
    field: str, value: object, expected: CapabilityCardVerificationResult
) -> None:
    signed, trust = _fixture()
    signed["signature"][field] = value  # type: ignore[index]
    assert _verify(signed, trust).result is expected


@pytest.mark.parametrize(
    ("signed_at", "expires_at", "now"),
    [
        (160.0, 200.0, 150.0),
        (100.0, 140.0, 150.0),
    ],
)
def test_card_validity_window_honours_clock_skew(
    signed_at: float, expires_at: float, now: float
) -> None:
    signed, trust = _fixture(signed_at=signed_at, expires_at=expires_at)
    assert _verify(signed, trust, now=now).result is CapabilityCardVerificationResult.EXPIRED


def test_verification_rejects_an_inverted_signed_window() -> None:
    signed, trust = _fixture()
    signed["signature"]["expires_at"] = 100.0  # type: ignore[index]
    assert _verify(signed, trust, now=100.0).result is CapabilityCardVerificationResult.EXPIRED


def test_tampered_card_digest_and_signature_fail() -> None:
    signed, trust = _fixture()
    tampered = copy.deepcopy(signed)
    tampered["description"] = "changed"
    assert _verify(tampered, trust).result is CapabilityCardVerificationResult.BAD_SIGNATURE

    bad_value = copy.deepcopy(signed)
    bad_value["signature"]["value"] = "not-base64!"  # type: ignore[index]
    assert _verify(bad_value, trust).result is CapabilityCardVerificationResult.BAD_SIGNATURE

    missing_value = copy.deepcopy(signed)
    missing_value["signature"]["value"] = ""  # type: ignore[index]
    assert _verify(missing_value, trust).result is CapabilityCardVerificationResult.BAD_SIGNATURE

    other_key = copy.deepcopy(signed)
    replacement = sign_capability_card(
        _card(),
        key_id="P:key",
        private_key=generate_signing_key(),
        sequence=1,
        signed_at=100.0,
        expires_at=200.0,
    )
    other_key["signature"]["value"] = replacement["signature"]["value"]  # type: ignore[index]
    assert _verify(other_key, trust).result is CapabilityCardVerificationResult.BAD_SIGNATURE


def test_history_detects_replay_and_signed_downgrade() -> None:
    private = generate_signing_key()
    key = EventSignatureKey.from_private_key(
        key_id="P:key",
        private_key=private,
        senders=frozenset({"P/worker"}),
        projects=frozenset({"P"}),
    )
    trust = CapabilityCardTrustBundle(keys={key.key_id: key})
    first = sign_capability_card(
        _card(), key_id="P:key", private_key=private, sequence=1, signed_at=100, expires_at=200
    )
    assert _verify(first, trust).result is CapabilityCardVerificationResult.VALID
    assert _verify(first, trust).result is CapabilityCardVerificationResult.SEQUENCE_MISMATCH

    reduced = _card()
    reduced["skills"] = []
    second = sign_capability_card(
        reduced,
        key_id="P:key",
        private_key=private,
        sequence=2,
        signed_at=101,
        expires_at=200,
    )
    assert _verify(second, trust).result is CapabilityCardVerificationResult.CAPABILITY_DOWNGRADE

    repeated = sign_capability_card(
        reduced,
        key_id="P:key",
        private_key=private,
        sequence=3,
        signed_at=102,
        expires_at=200,
    )
    assert _verify(repeated, trust).result is CapabilityCardVerificationResult.CAPABILITY_DOWNGRADE


def test_non_canonical_known_key_card_fails_closed() -> None:
    signed, trust = _fixture()
    signed["meta"] = {"bad": float("nan")}
    assert _verify(signed, trust).result is CapabilityCardVerificationResult.BAD_SIGNATURE


def test_history_full_is_visible_and_one_shot_verify_does_not_remember() -> None:
    signed, trust = _fixture(history=CapabilityCardHistory(max_entries=1))
    trust.history.assess_and_remember(
        agent="Q/other",
        key_id="Q:key",
        sequence=1,
        route_capabilities=frozenset(),
        card_digest="x",
        expires_at=200,
        now=100,
    )
    assert _verify(signed, trust).result is CapabilityCardVerificationResult.HISTORY_FULL

    signed, trust = _fixture()
    assert _verify(signed, trust, remember=False).result is CapabilityCardVerificationResult.VALID
    assert _verify(signed, trust, remember=False).result is CapabilityCardVerificationResult.VALID


def test_minimal_result_omits_empty_context() -> None:
    result = CapabilityCardVerification(
        result=CapabilityCardVerificationResult.MISSING_SIGNATURE,
        detail="unsigned",
    )
    assert result.as_dict() == {"detail": "unsigned", "result": "missing_signature"}


def test_route_capability_projection_tolerates_non_lists_and_non_contracts() -> None:
    private = generate_signing_key()
    card = _card()
    card["skills"] = "python"
    card["task_classes"] = ["", "code"]
    card["contracts"] = ["bad", {"task_class": "code"}]
    signed = sign_capability_card(
        card,
        key_id="P:key",
        private_key=private,
        sequence=1,
        signed_at=100,
        expires_at=200,
    )
    key = EventSignatureKey.from_private_key(
        key_id="P:key",
        private_key=private,
        senders=frozenset({"P/worker"}),
        projects=frozenset({"P"}),
    )
    trust = CapabilityCardTrustBundle(keys={key.key_id: key})
    assert _verify(signed, trust).result is CapabilityCardVerificationResult.VALID

    card["contracts"] = "not-a-list"
    card["task_classes"] = "not-a-list"
    signed_without_lists = sign_capability_card(
        card,
        key_id="P:key",
        private_key=private,
        sequence=1,
        signed_at=100,
        expires_at=200,
    )
    fresh_trust = CapabilityCardTrustBundle(keys={key.key_id: key})
    assert (
        _verify(signed_without_lists, fresh_trust).result is CapabilityCardVerificationResult.VALID
    )
