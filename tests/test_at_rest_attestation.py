# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for hardware attestation gate on at-rest keys

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from synapse_channel.core.at_rest_attestation import (
    ALGORITHM_HMAC_SHA256,
    ALGORITHM_TPM2_QUOTE,
    ATTESTATION_EVIDENCE_SCHEMA,
    ATTESTATION_POLICY_SCHEMA,
    AttestationEvidence,
    AttestationPolicy,
    create_hmac_evidence,
    create_hmac_policy,
    enforce_attestation_gate,
    fresh_nonce,
    load_evidence_file,
    load_policy_file,
    verify_attestation,
    write_evidence_file,
    write_policy_file,
)


def _pcr0() -> bytes:
    return hashlib.sha256(b"boot-measurement-fixture").digest()


def test_hmac_policy_evidence_round_trip(tmp_path: Path) -> None:
    policy = create_hmac_policy(policy_id="seat-a", pcr_digests={0: _pcr0(), 7: _pcr0()})
    policy_path = write_policy_file(tmp_path / "policy.json", policy)
    assert oct(policy_path.stat().st_mode & 0o777) == "0o600"
    loaded = load_policy_file(policy_path)
    assert loaded.policy_id == "seat-a"
    assert loaded.algorithm == ALGORITHM_HMAC_SHA256
    assert loaded.pcr_digests[0] == _pcr0()

    nonce = fresh_nonce()
    evidence = create_hmac_evidence(loaded, nonce=nonce)
    evidence_path = write_evidence_file(tmp_path / "evidence.json", evidence)
    document = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert document["schema"] == ATTESTATION_EVIDENCE_SCHEMA
    reloaded = load_evidence_file(evidence_path)
    enforce_attestation_gate(loaded, reloaded)


def test_signature_tamper_fails(tmp_path: Path) -> None:
    policy = create_hmac_policy(policy_id="seat-b", pcr_digests={0: _pcr0()})
    evidence = create_hmac_evidence(policy, nonce=fresh_nonce())
    tampered = AttestationEvidence(
        nonce=evidence.nonce,
        pcr_digests=evidence.pcr_digests,
        algorithm=evidence.algorithm,
        signature=bytes(b ^ 0xFF for b in evidence.signature),
        policy_id=evidence.policy_id,
    )
    with pytest.raises(ValueError, match="signature verification failed"):
        verify_attestation(policy, tampered)


def test_pcr_mismatch_fails() -> None:
    policy = create_hmac_policy(policy_id="seat-c", pcr_digests={0: _pcr0()})
    wrong = hashlib.sha256(b"other").digest()
    evidence = create_hmac_evidence(policy, nonce=fresh_nonce(), pcr_digests={0: wrong})
    with pytest.raises(ValueError, match="PCR 0 digest mismatch"):
        verify_attestation(policy, evidence)


def test_missing_required_pcr_fails() -> None:
    policy = create_hmac_policy(policy_id="seat-d", pcr_digests={0: _pcr0(), 1: _pcr0()})
    evidence = create_hmac_evidence(policy, nonce=fresh_nonce(), pcr_digests={0: _pcr0()})
    with pytest.raises(ValueError, match="missing required PCR 1"):
        verify_attestation(policy, evidence)


def test_policy_id_mismatch_fails() -> None:
    policy = create_hmac_policy(policy_id="seat-e", pcr_digests={})
    evidence = create_hmac_evidence(policy, nonce=fresh_nonce())
    wrong_policy = create_hmac_policy(
        policy_id="other",
        pcr_digests={},
        verification_key=policy.verification_key,
    )
    with pytest.raises(ValueError, match="policy_id mismatch"):
        verify_attestation(wrong_policy, evidence)


def test_tpm2_quote_tag_with_trust_key() -> None:
    # Software-assisted TPM2 tag: 32-byte trust key reuses HMAC under the hood.
    key = hashlib.sha256(b"tpm-trust").digest()
    policy = AttestationPolicy(
        policy_id="tpm-seat",
        pcr_digests={0: _pcr0()},
        algorithm=ALGORITHM_TPM2_QUOTE,
        verification_key=key,
    )
    hmac_policy = create_hmac_policy(
        policy_id="tpm-seat", pcr_digests={0: _pcr0()}, verification_key=key
    )
    evidence = create_hmac_evidence(hmac_policy, nonce=fresh_nonce())
    tpm_evidence = AttestationEvidence(
        nonce=evidence.nonce,
        pcr_digests=evidence.pcr_digests,
        algorithm=ALGORITHM_TPM2_QUOTE,
        signature=evidence.signature,
        policy_id=evidence.policy_id,
    )
    verify_attestation(policy, tpm_evidence)


def test_empty_nonce_rejected() -> None:
    policy = create_hmac_policy(policy_id="n", pcr_digests={})
    with pytest.raises(ValueError, match="nonce must not be empty"):
        create_hmac_evidence(policy, nonce=b"")


def test_fresh_nonce_minimum() -> None:
    with pytest.raises(ValueError, match="at least 8"):
        fresh_nonce(4)
    assert len(fresh_nonce(16)) == 16


def test_malformed_policy_file(tmp_path: Path) -> None:
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"schema": "nope"}), encoding="utf-8")
    with pytest.raises(ValueError, match="not a Synapse"):
        load_policy_file(path)


def test_policy_document_schema(tmp_path: Path) -> None:
    policy = create_hmac_policy(policy_id="seat", pcr_digests={0: _pcr0()})
    path = write_policy_file(tmp_path / "p.json", policy)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["schema"] == ATTESTATION_POLICY_SCHEMA
    assert "verification_key" in raw


def test_wrong_hmac_key_length() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        create_hmac_policy(policy_id="x", pcr_digests={}, verification_key=b"short")
