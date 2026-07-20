# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Agent Evidence Format verification regressions

from __future__ import annotations

import base64
import copy
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import pytest

from synapse_channel.core.aef_canonical import canonical_json
from synapse_channel.core.aef_domain import AEF_RECEIPT_DOMAIN
from synapse_channel.core.aef_verdict import AefVerdictCode
from synapse_channel.core.aef_verification import (
    AefInclusionVerdict,
    AefReceiptIndex,
    AefTrustedKey,
    AefTrustStore,
    receipt_id_for,
    verify_aef_inclusion,
    verify_aef_receipt,
)

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "aef_receipt_v0_1.json"
_FIXTURE = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
_VECTORS: dict[str, dict[str, Any]] = {vector["name"]: vector for vector in _FIXTURE["vectors"]}
_LOG_ID = "f3320c94a8b070b04d652b5b0099baa9e12ff8cf8f375093282c7c31becbe0d6"
_KEY_ID = "56475aa75463474c"
_PUBLIC_KEY = bytes.fromhex("03a107bff3ce10be1d70dd18e74bc09967e4d6309ba50d5f1ddc8664125531b8")
_PRIVATE_SEED = bytes(range(32))


def _receipt(name: str) -> dict[str, Any]:
    return copy.deepcopy(cast(dict[str, Any], _VECTORS[name]["receipt"]))


def _trust_store(
    *,
    revoked: bool = False,
    not_before: int | None = None,
    not_after: int | None = None,
    senders: frozenset[str] | None = None,
    trust_log: bool = True,
) -> AefTrustStore:
    key = AefTrustedKey(
        _PUBLIC_KEY,
        revoked=revoked,
        not_before=not_before,
        not_after=not_after,
        senders=senders,
    )
    logs = {_LOG_ID: _KEY_ID} if trust_log else {}
    return AefTrustStore(keys={_KEY_ID: key}, logs=logs)


def _sign(receipt: dict[str, Any]) -> dict[str, Any]:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    receipt["receipt_id"] = receipt_id_for(receipt)
    unsigned = copy.deepcopy(receipt)
    unsigned["signature"].pop("value", None)
    signature = Ed25519PrivateKey.from_private_bytes(_PRIVATE_SEED).sign(
        AEF_RECEIPT_DOMAIN.preimage(canonical_json(unsigned))
    )
    receipt["signature"]["value"] = base64.urlsafe_b64encode(signature).decode("ascii")
    return receipt


@pytest.mark.parametrize(
    "name",
    [
        "v01-valid-lease-grant",
        "v02-bad-signature",
        "v03-wrong-domain",
        "v04-expired-receipt",
        "v05-replayed-seq",
        "v08-unknown-field-tolerance",
        "v09-unknown-type",
        "v10-revoked-key",
    ],
)
def test_supplied_receipt_vectors_match_the_normative_verdict(name: str) -> None:
    vector = _VECTORS[name]
    seen = AefReceiptIndex()
    for known in vector.get("chain_context", {}).get("known", []):
        seen.remember(known["log_id"], known["seq"], known["receipt_id"])
    store = _trust_store(revoked=name == "v10-revoked-key")

    result = verify_aef_receipt(
        vector["receipt"], trust_store=store, now_ms=vector["now_ms"], seen=seen
    )

    assert result.verdict.value == vector["expected"]


def test_expiry_vector_records_the_source_enum_typo_without_accepting_it() -> None:
    vector = _VECTORS["v04-expired-receipt"]

    assert vector["source_expected"] == "INVALID_EXPIRED"
    assert vector["expected"] == AefVerdictCode.EXPIRED.value
    assert _FIXTURE["corrections"]["v04-expired-receipt"].startswith(
        "source token INVALID_EXPIRED corrected"
    )


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("v06-inclusion-pass", AefInclusionVerdict.INCLUSION_VALID),
        ("v07-inclusion-fail", AefInclusionVerdict.INCLUSION_INVALID),
    ],
)
def test_supplied_inclusion_vectors_verify_through_the_production_boundary(
    name: str, expected: AefInclusionVerdict
) -> None:
    vector = _VECTORS[name]
    inclusion = vector["inclusion"]

    assert (
        verify_aef_inclusion(
            vector["receipt"],
            inclusion["sth"],
            inclusion["proof"],
            trust_store=_trust_store(),
        )
        is expected
    )


def test_receipt_id_is_derived_only_from_the_unsigned_content_core() -> None:
    receipt = _receipt("v01-valid-lease-grant")
    expected = receipt["receipt_id"]
    receipt["signature"]["value"] = "ignored-for-content-id"
    receipt["receipt_id"] = "aef1:" + "f" * 64

    assert receipt_id_for(receipt) == expected


def test_repeated_valid_receipt_is_classified_as_replayed() -> None:
    receipt = _receipt("v01-valid-lease-grant")
    seen = AefReceiptIndex()

    first = verify_aef_receipt(
        receipt, trust_store=_trust_store(), now_ms=1_783_941_000_000, seen=seen
    )
    repeated = verify_aef_receipt(
        receipt, trust_store=_trust_store(), now_ms=1_783_941_000_000, seen=seen
    )

    assert first.verdict is AefVerdictCode.VALID
    assert repeated.verdict is AefVerdictCode.REPLAYED


@pytest.mark.parametrize(
    ("mutation", "store", "expected"),
    [
        ({"aef": "1.0"}, _trust_store(), AefVerdictCode.UNSUPPORTED_VERSION),
        ({"action": "unknown"}, _trust_store(), AefVerdictCode.MALFORMED),
        ({"log_id": "a" * 64}, _trust_store(), AefVerdictCode.UNTRUSTED_LOG),
        ({"receipt_id": "aef1:" + "1" * 64}, _trust_store(), AefVerdictCode.INVALID_RECEIPT_ID),
    ],
)
def test_pre_signature_verdicts_remain_distinct(
    mutation: dict[str, Any], store: AefTrustStore, expected: AefVerdictCode
) -> None:
    receipt = _receipt("v01-valid-lease-grant")
    receipt.update(mutation)

    assert (
        verify_aef_receipt(receipt, trust_store=store, now_ms=1_783_941_000_000).verdict is expected
    )


def test_unknown_receipt_key_is_distinct_from_an_untrusted_log() -> None:
    receipt = _receipt("v01-valid-lease-grant")
    receipt["signature"]["key_id"] = "0" * 16

    result = verify_aef_receipt(receipt, trust_store=_trust_store(), now_ms=1_783_941_000_000)

    assert result.verdict is AefVerdictCode.UNKNOWN_KEY


def test_key_time_and_sender_policy_are_checked_before_crypto() -> None:
    receipt = _receipt("v01-valid-lease-grant")

    too_early = verify_aef_receipt(
        receipt,
        trust_store=_trust_store(not_before=1_783_940_400_001),
        now_ms=1_783_941_000_000,
    )
    wrong_sender = verify_aef_receipt(
        receipt,
        trust_store=_trust_store(senders=frozenset({"agent-8"})),
        now_ms=1_783_941_000_000,
    )

    assert too_early.verdict is AefVerdictCode.KEY_WINDOW_INVALID
    assert wrong_sender.verdict is AefVerdictCode.SENDER_SCOPE_MISMATCH


def test_key_not_after_and_invalid_verifier_time_fail_closed() -> None:
    receipt = _receipt("v01-valid-lease-grant")

    too_late = verify_aef_receipt(
        receipt,
        trust_store=_trust_store(not_after=1_783_940_399_999),
        now_ms=1_783_941_000_000,
    )
    bad_now = verify_aef_receipt(receipt, trust_store=_trust_store(), now_ms=True)

    assert too_late.verdict is AefVerdictCode.KEY_WINDOW_INVALID
    assert bad_now.verdict is AefVerdictCode.MALFORMED


@pytest.mark.parametrize(
    "mutation",
    [
        {"seq": True},
        {"actor": "agent-7"},
        {"prev_receipt": "bad"},
        {"decision": "deny", "reason_code": ""},
        {"decision": "maybe"},
        {"signature": {"alg": "rsa", "domain": "aef:receipt:v0.1", "key_id": _KEY_ID}},
        {"signature": "not-an-envelope"},
        {
            "signature": {
                "alg": "ed25519",
                "domain": "aef:receipt:v0.1",
                "key_id": "bad",
                "value": "x",
            }
        },
        {"subject": {"items": list(range(1025))}},
        {"hub_id": "x" * 4097},
        {"evidence": {"optional": None}},
        {"receipt_id": "bad"},
    ],
)
def test_malformed_receipt_shapes_fail_at_the_single_boundary(mutation: dict[str, Any]) -> None:
    receipt = _receipt("v01-valid-lease-grant")
    receipt.update(mutation)

    result = verify_aef_receipt(receipt, trust_store=_trust_store(), now_ms=1_783_941_000_000)

    assert result.verdict is AefVerdictCode.MALFORMED


def test_noncanonical_base64url_signature_is_rejected() -> None:
    receipt = _receipt("v01-valid-lease-grant")
    receipt["signature"]["value"] = cast(str, receipt["signature"]["value"]).rstrip("=")

    result = verify_aef_receipt(receipt, trust_store=_trust_store(), now_ms=1_783_941_000_000)

    assert result.verdict is AefVerdictCode.INVALID_SIGNATURE


def test_wrong_length_signature_is_rejected() -> None:
    receipt = _receipt("v01-valid-lease-grant")
    receipt["signature"]["value"] = base64.urlsafe_b64encode(b"x" * 63).decode("ascii")

    result = verify_aef_receipt(receipt, trust_store=_trust_store(), now_ms=1_783_941_000_000)

    assert result.verdict is AefVerdictCode.INVALID_SIGNATURE


@pytest.mark.parametrize(
    ("receipt_type", "action", "subject"),
    [
        (
            "lease",
            "takeover",
            {
                "task_id": "task-1",
                "epoch": 2,
                "lease_expires_at": 1_783_944_000_000,
                "prev_owner": "agent-1",
                "paths": ["src/"],
                "worktree": "repo",
            },
        ),
        (
            "message",
            "send",
            {
                "message_id": 1,
                "message_seq": 2,
                "sender": "agent-7",
                "target": "agent-8",
                "body_sha256": "a" * 64,
            },
        ),
        (
            "tool_call",
            "execute",
            {"tool": "Bash", "guard": "sandbox", "call_id": "call-1", "exit": "ok"},
        ),
        (
            "federation",
            "import",
            {"peer_domain": "peer.example", "namespace": "repo", "direction": "in"},
        ),
    ],
)
def test_registered_subject_profiles_accept_real_signed_receipts(
    receipt_type: str, action: str, subject: dict[str, Any]
) -> None:
    receipt = _receipt("v01-valid-lease-grant")
    receipt.update({"receipt_type": receipt_type, "action": action, "subject": subject})
    receipt.pop("decision", None)

    result = verify_aef_receipt(
        _sign(receipt), trust_store=_trust_store(), now_ms=1_783_941_000_000
    )

    assert result.verdict is AefVerdictCode.VALID


@pytest.mark.parametrize(
    ("receipt_type", "action", "subject"),
    [
        ("lease", "deny", {"task_id": "task-1"}),
        (
            "lease",
            "takeover",
            {"task_id": "task-1", "epoch": -1, "lease_expires_at": 1},
        ),
        ("message", "send", {"message_id": 1}),
        ("tool_call", "execute", {"tool": "Bash", "guard": "sandbox", "call_id": "c"}),
        (
            "federation",
            "import",
            {"peer_domain": "p", "namespace": "n", "direction": "sideways"},
        ),
    ],
)
def test_registered_subject_profiles_reject_missing_or_invalid_semantics(
    receipt_type: str, action: str, subject: dict[str, Any]
) -> None:
    receipt = _receipt("v01-valid-lease-grant")
    receipt.update({"receipt_type": receipt_type, "action": action, "subject": subject})

    result = verify_aef_receipt(receipt, trust_store=_trust_store(), now_ms=1_783_941_000_000)

    assert result.verdict is AefVerdictCode.MALFORMED


def test_inclusion_refuses_untrusted_and_malformed_tree_heads() -> None:
    vector = _VECTORS["v06-inclusion-pass"]
    inclusion = vector["inclusion"]
    malformed_sth = copy.deepcopy(inclusion["sth"])
    malformed_sth["root"] = "f" * 64

    untrusted = verify_aef_inclusion(
        vector["receipt"],
        inclusion["sth"],
        inclusion["proof"],
        trust_store=_trust_store(trust_log=False),
    )
    malformed = verify_aef_inclusion(
        vector["receipt"], malformed_sth, inclusion["proof"], trust_store=_trust_store()
    )

    assert untrusted is AefInclusionVerdict.STH_UNTRUSTED
    assert malformed is AefInclusionVerdict.STH_INVALID


@pytest.mark.parametrize(
    ("sth_mutation", "proof_mutation", "expected"),
    [
        ({"log_id": "a" * 64}, {}, AefInclusionVerdict.STH_INVALID),
        ({"timestamp": "not-time"}, {}, AefInclusionVerdict.STH_INVALID),
        ({"aef": "1.0"}, {}, AefInclusionVerdict.STH_INVALID),
        (
            {
                "signature": {
                    "alg": "ed25519",
                    "domain": "aef:sth:v0.1",
                    "key_id": _KEY_ID,
                    "value": "bad",
                }
            },
            {},
            AefInclusionVerdict.STH_INVALID,
        ),
        ({}, {"tree_size": 3}, AefInclusionVerdict.INCLUSION_INVALID),
        ({}, {"leaf_index": -1}, AefInclusionVerdict.INCLUSION_INVALID),
        ({}, {"leaf_hash": "0" * 64}, AefInclusionVerdict.INCLUSION_INVALID),
        ({}, {"audit_path": ["bad"]}, AefInclusionVerdict.INCLUSION_INVALID),
    ],
)
def test_inclusion_failure_classes_are_stable(
    sth_mutation: dict[str, Any],
    proof_mutation: dict[str, Any],
    expected: AefInclusionVerdict,
) -> None:
    vector = _VECTORS["v06-inclusion-pass"]
    sth = copy.deepcopy(vector["inclusion"]["sth"])
    proof = copy.deepcopy(vector["inclusion"]["proof"])
    sth.update(sth_mutation)
    proof.update(proof_mutation)

    result = verify_aef_inclusion(vector["receipt"], sth, proof, trust_store=_trust_store())

    assert result is expected


def test_trust_store_recomputes_key_ids_and_requires_known_log_keys() -> None:
    key = AefTrustedKey(_PUBLIC_KEY)

    with pytest.raises(ValueError, match="key id does not match"):
        AefTrustStore(keys={"0" * 16: key}, logs={})
    with pytest.raises(ValueError, match="unknown STH key"):
        AefTrustStore(keys={_KEY_ID: key}, logs={_LOG_ID: "0" * 16})
    with pytest.raises(ValueError, match="64 lowercase hex"):
        AefTrustStore(keys={_KEY_ID: key}, logs={"bad": _KEY_ID})


def test_trusted_key_rejects_invalid_material_windows_and_senders() -> None:
    with pytest.raises(ValueError, match="32 raw"):
        AefTrustedKey(b"short")
    with pytest.raises(ValueError, match="validity window is inverted"):
        AefTrustedKey(_PUBLIC_KEY, not_before=2, not_after=1)
    with pytest.raises(ValueError, match="sender constraints"):
        AefTrustedKey(_PUBLIC_KEY, senders=frozenset({""}))
    with pytest.raises(ValueError, match="canonical timestamp"):
        AefTrustedKey(_PUBLIC_KEY, not_before=cast(int, True))
    with pytest.raises(ValueError, match="sender constraints"):
        AefTrustedKey(_PUBLIC_KEY, senders=frozenset({"\ud800"}))


def test_trust_store_snapshots_caller_mappings_after_validation() -> None:
    keys = {_KEY_ID: AefTrustedKey(_PUBLIC_KEY)}
    logs = {_LOG_ID: _KEY_ID}
    store = AefTrustStore(keys=keys, logs=logs)
    keys.clear()
    logs.clear()

    assert set(store.keys) == {_KEY_ID}
    assert set(store.logs) == {_LOG_ID}


def test_receipt_index_detects_reused_identity_at_a_different_sequence() -> None:
    index = AefReceiptIndex()
    receipt_id = "aef1:" + "1" * 64
    index.remember(_LOG_ID, 1, receipt_id)

    assert index.classify(_LOG_ID, 2, receipt_id) is AefVerdictCode.REPLAYED


def test_fixture_is_a_complete_ten_vector_conformance_set() -> None:
    assert _FIXTURE["format"] == "aef-conformance-v0.1"
    assert len(_VECTORS) == 10
    assert set(_VECTORS) == {
        "v01-valid-lease-grant",
        "v02-bad-signature",
        "v03-wrong-domain",
        "v04-expired-receipt",
        "v05-replayed-seq",
        "v06-inclusion-pass",
        "v07-inclusion-fail",
        "v08-unknown-field-tolerance",
        "v09-unknown-type",
        "v10-revoked-key",
    }


def test_fixture_receipts_remain_json_mappings() -> None:
    for vector in _VECTORS.values():
        assert isinstance(cast(Mapping[str, object], vector["receipt"]), Mapping)
