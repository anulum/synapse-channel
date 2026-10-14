# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — operator HMAC statement gate for at-rest workflows
"""Verify operator-signed statements about declared platform measurements.

This module checks HMAC-SHA256 under a 32-byte operator policy key and compares
the statement's PCR values with that policy. It collects no measurements and
verifies no TPM quote, AK signature, EK trust chain or measured boot state.
Omitting measurements when creating evidence copies the policy's expected
values; that is an operator self-check, not an observation of the host.

The statement binds its supplied nonce, but this verifier neither issues nor
consumes challenges, checks an expected nonce, nor rejects old evidence by time.
Repeated verification of the same statement is allowed. A caller needing
freshness must supply independent challenge custody outside this boundary.

The historical ``tpm2-quote`` label also uses the software HMAC path with a
32-byte key. The label provides no hardware assurance; other trust-anchor forms
are refused. This compatibility path requires no TPM library or hardware.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from synapse_channel.core.at_rest import KEY_BYTES, _write_new_key_file

ATTESTATION_POLICY_SCHEMA = "synapse-at-rest-attestation-policy.v1"
"""Schema marker for an at-rest attestation policy document."""

ATTESTATION_EVIDENCE_SCHEMA = "synapse-at-rest-attestation-evidence.v1"
"""Schema marker for signed attestation evidence."""

ALGORITHM_HMAC_SHA256 = "hmac-sha256"
"""Software attestation algorithm: HMAC-SHA256 over the canonical statement."""

ALGORITHM_TPM2_QUOTE = "tpm2-quote"
"""Historical software-HMAC compatibility label; no TPM quote is verified."""

_SUPPORTED_ALGORITHMS = frozenset({ALGORITHM_HMAC_SHA256, ALGORITHM_TPM2_QUOTE})


def _canonical_pcr_map(pcr_digests: Mapping[int, bytes]) -> dict[str, str]:
    """Return a sorted PCR map with hex digests for deterministic signing."""
    return {str(int(idx)): digests.hex() for idx, digests in sorted(pcr_digests.items())}


def _statement_bytes(*, nonce: bytes, pcr_digests: Mapping[int, bytes]) -> bytes:
    """Build the canonical attestation statement bytes (nonce + PCR map)."""
    if not nonce:
        raise ValueError("attestation nonce must not be empty")
    document = {
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "pcr_digests": _canonical_pcr_map(pcr_digests),
    }
    return json.dumps(document, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )


@dataclass(frozen=True)
class AttestationPolicy:
    """Expected declared measurement values and operator verification material.

    Attributes
    ----------
    policy_id : str
        Operator label for this policy.
    pcr_digests : dict[int, bytes]
        Expected SHA-256 digests keyed by PCR index (may be empty for
        signature-only checks that only bind a nonce).
    algorithm : str
        :data:`ALGORITHM_HMAC_SHA256` or :data:`ALGORITHM_TPM2_QUOTE`.
    verification_key : bytes
        Operator HMAC key (32 bytes) for both accepted algorithm labels.
        Hardware AK trust anchors are not supported by this verifier.
    """

    policy_id: str
    pcr_digests: dict[int, bytes]
    algorithm: str
    verification_key: bytes

    def to_document(self) -> dict[str, Any]:
        """Serialise the policy (verification key is base64, not printed by CLI by default)."""
        return {
            "schema": ATTESTATION_POLICY_SCHEMA,
            "policy_id": self.policy_id,
            "algorithm": self.algorithm,
            "pcr_digests": _canonical_pcr_map(self.pcr_digests),
            "verification_key": base64.b64encode(self.verification_key).decode("ascii"),
        }


@dataclass(frozen=True)
class AttestationEvidence:
    """Operator-signed statement containing declared PCR digests.

    Attributes
    ----------
    nonce : bytes
        Nonce bound into the signature. Issuance, expected-challenge matching
        and single-use custody are outside this verifier.
    pcr_digests : dict[int, bytes]
        Digests supplied by the caller, not measurements collected here.
    algorithm : str
        Signing algorithm tag.
    signature : bytes
        HMAC signature bytes, including for the historical TPM label.
    policy_id : str
        Policy this evidence claims to satisfy.
    """

    nonce: bytes
    pcr_digests: dict[int, bytes]
    algorithm: str
    signature: bytes
    policy_id: str

    def to_document(self) -> dict[str, Any]:
        """Serialise evidence for on-disk exchange (no raw KEK material)."""
        return {
            "schema": ATTESTATION_EVIDENCE_SCHEMA,
            "policy_id": self.policy_id,
            "algorithm": self.algorithm,
            "nonce": base64.b64encode(self.nonce).decode("ascii"),
            "pcr_digests": _canonical_pcr_map(self.pcr_digests),
            "signature": base64.b64encode(self.signature).decode("ascii"),
        }


def create_hmac_policy(
    *,
    policy_id: str,
    pcr_digests: Mapping[int, bytes],
    verification_key: bytes | None = None,
) -> AttestationPolicy:
    """Build an HMAC-SHA256 attestation policy, drawing a key when omitted."""
    if not policy_id:
        raise ValueError("attestation policy_id must not be empty")
    key = verification_key if verification_key is not None else secrets.token_bytes(KEY_BYTES)
    if len(key) != KEY_BYTES:
        raise ValueError(f"HMAC attestation key must be {KEY_BYTES} bytes, got {len(key)}")
    digests = {int(k): bytes(v) for k, v in pcr_digests.items()}
    for idx, digest in digests.items():
        if idx < 0:
            raise ValueError(f"PCR index must be non-negative, got {idx}")
        if len(digest) != 32:
            raise ValueError(f"PCR digest for index {idx} must be 32 bytes (SHA-256)")
    return AttestationPolicy(
        policy_id=policy_id,
        pcr_digests=digests,
        algorithm=ALGORITHM_HMAC_SHA256,
        verification_key=key,
    )


def create_hmac_evidence(
    policy: AttestationPolicy,
    *,
    nonce: bytes,
    pcr_digests: Mapping[int, bytes] | None = None,
) -> AttestationEvidence:
    """Sign attestation evidence under an HMAC policy.

    When ``pcr_digests`` is omitted the policy's expected digests are attested
    (useful for golden-path tests and operator self-checks).
    """
    if policy.algorithm != ALGORITHM_HMAC_SHA256:
        raise ValueError(
            f"create_hmac_evidence requires {ALGORITHM_HMAC_SHA256}, got {policy.algorithm!r}"
        )
    measured = (
        {int(k): bytes(v) for k, v in pcr_digests.items()}
        if pcr_digests is not None
        else dict(policy.pcr_digests)
    )
    statement = _statement_bytes(nonce=nonce, pcr_digests=measured)
    signature = hmac.new(policy.verification_key, statement, hashlib.sha256).digest()
    return AttestationEvidence(
        nonce=bytes(nonce),
        pcr_digests=measured,
        algorithm=ALGORITHM_HMAC_SHA256,
        signature=signature,
        policy_id=policy.policy_id,
    )


def verify_attestation(policy: AttestationPolicy, evidence: AttestationEvidence) -> None:
    """Verify the operator HMAC and declared PCR values against a policy.

    Success verifies this software statement only. It does not establish
    hardware measurement, quote authenticity or challenge freshness.

    Raises
    ------
    ValueError
        When the algorithm, policy id, signature, or PCR digests do not match.
    """
    if policy.algorithm not in _SUPPORTED_ALGORITHMS:
        raise ValueError(f"unsupported attestation algorithm in policy: {policy.algorithm!r}")
    if evidence.algorithm != policy.algorithm:
        raise ValueError(
            f"attestation algorithm mismatch: evidence {evidence.algorithm!r} "
            f"vs policy {policy.algorithm!r}"
        )
    if evidence.policy_id != policy.policy_id:
        raise ValueError(
            f"attestation policy_id mismatch: evidence {evidence.policy_id!r} "
            f"vs policy {policy.policy_id!r}"
        )
    if policy.algorithm == ALGORITHM_HMAC_SHA256:
        _verify_hmac(policy, evidence)
    else:
        _verify_tpm2_quote(policy, evidence)
    _match_pcr_policy(policy.pcr_digests, evidence.pcr_digests)


def _verify_hmac(policy: AttestationPolicy, evidence: AttestationEvidence) -> None:
    """Verify an HMAC-SHA256 attestation signature."""
    statement = _statement_bytes(nonce=evidence.nonce, pcr_digests=evidence.pcr_digests)
    expected = hmac.new(policy.verification_key, statement, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, evidence.signature):
        raise ValueError("attestation signature verification failed")


def _verify_tpm2_quote(policy: AttestationPolicy, evidence: AttestationEvidence) -> None:
    """Verify software HMAC evidence carrying the historical TPM label.

    Only the 32-byte operator-key compatibility form is implemented. No quote
    parser or AK verifier is called, even when TPM libraries are installed.
    Other trust-anchor forms are refused instead of being treated as verified.
    """
    # Preserve the historical software form without implying hardware evidence.
    if len(policy.verification_key) == KEY_BYTES:
        _verify_hmac(
            AttestationPolicy(
                policy_id=policy.policy_id,
                pcr_digests=policy.pcr_digests,
                algorithm=ALGORITHM_HMAC_SHA256,
                verification_key=policy.verification_key,
            ),
            AttestationEvidence(
                nonce=evidence.nonce,
                pcr_digests=evidence.pcr_digests,
                algorithm=ALGORITHM_HMAC_SHA256,
                signature=evidence.signature,
                policy_id=evidence.policy_id,
            ),
        )
        return
    raise ValueError(
        "TPM2 quote verification requires a 32-byte trust key for this build's "
        "software-assisted path, or a future AK public-area trust anchor"
    )


def _match_pcr_policy(expected: Mapping[int, bytes], measured: Mapping[int, bytes]) -> None:
    """Require every expected PCR digest to match the measured map."""
    for idx, digest in expected.items():
        if idx not in measured:
            raise ValueError(f"attestation missing required PCR {idx}")
        if not hmac.compare_digest(digest, measured[idx]):
            raise ValueError(f"attestation PCR {idx} digest mismatch")


def enforce_attestation_gate(policy: AttestationPolicy, evidence: AttestationEvidence) -> None:
    """Verify an operator statement without granting hardware or freshness assurance."""
    verify_attestation(policy, evidence)


def write_policy_file(path: str | Path, policy: AttestationPolicy) -> Path:
    """Write an owner-only attestation policy file, never overwriting."""
    body = json.dumps(policy.to_document(), ensure_ascii=True, indent=2, sort_keys=True)
    return _write_new_key_file(Path(path), body.encode("utf-8") + b"\n")


def load_policy_file(path: str | Path) -> AttestationPolicy:
    """Load and validate an attestation policy document."""
    raw: Any = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema") != ATTESTATION_POLICY_SCHEMA:
        raise ValueError(f"not a Synapse at-rest attestation policy: {path}")
    try:
        policy_id = str(raw["policy_id"])
        algorithm = str(raw["algorithm"])
        key = base64.b64decode(raw["verification_key"], validate=True)
        pcr_raw = raw["pcr_digests"]
        if not isinstance(pcr_raw, dict):
            raise TypeError("pcr_digests must be an object")
        digests = {int(k): bytes.fromhex(str(v)) for k, v in pcr_raw.items()}
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"malformed attestation policy: {path}") from exc
    if algorithm not in _SUPPORTED_ALGORITHMS:
        raise ValueError(f"unsupported attestation algorithm {algorithm!r} in {path}")
    return AttestationPolicy(
        policy_id=policy_id,
        pcr_digests=digests,
        algorithm=algorithm,
        verification_key=key,
    )


def write_evidence_file(path: str | Path, evidence: AttestationEvidence) -> Path:
    """Write an owner-only attestation evidence file, never overwriting."""
    body = json.dumps(evidence.to_document(), ensure_ascii=True, indent=2, sort_keys=True)
    return _write_new_key_file(Path(path), body.encode("utf-8") + b"\n")


def load_evidence_file(path: str | Path) -> AttestationEvidence:
    """Load and validate an attestation evidence document."""
    raw: Any = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema") != ATTESTATION_EVIDENCE_SCHEMA:
        raise ValueError(f"not a Synapse at-rest attestation evidence: {path}")
    try:
        policy_id = str(raw["policy_id"])
        algorithm = str(raw["algorithm"])
        nonce = base64.b64decode(raw["nonce"], validate=True)
        signature = base64.b64decode(raw["signature"], validate=True)
        pcr_raw = raw["pcr_digests"]
        if not isinstance(pcr_raw, dict):
            raise TypeError("pcr_digests must be an object")
        digests = {int(k): bytes.fromhex(str(v)) for k, v in pcr_raw.items()}
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"malformed attestation evidence: {path}") from exc
    if algorithm not in _SUPPORTED_ALGORITHMS:
        raise ValueError(f"unsupported attestation algorithm {algorithm!r} in {path}")
    return AttestationEvidence(
        nonce=nonce,
        pcr_digests=digests,
        algorithm=algorithm,
        signature=signature,
        policy_id=policy_id,
    )


def fresh_nonce(size: int = 16) -> bytes:
    """Draw a fresh attestation challenge nonce."""
    if size < 8:
        raise ValueError("attestation nonce must be at least 8 bytes")
    return secrets.token_bytes(size)
