# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — hub-key signing of receipt Merkle commitments

"""Tests for hub-key signing and verification of receipt Merkle commitments.

The signing side is pinned end to end (keypair files, fingerprints, canonical
bytes, envelopes) and the verification side is exercised deny-by-default: every
malformed, untrusted, orphaned, or tampered shape must fail, and only a receipt
with no signature at all reads as not applicable.
"""

from __future__ import annotations

import base64
import json
import stat
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.aef_verdict import AefVerdictCode
from synapse_channel.core.receipt_signing import (
    KEY_ID_HEX_CHARS,
    RECEIPT_COMMITMENT_DOMAIN,
    ReceiptSigningError,
    canonical_commitment_bytes,
    check_receipt_merkle_signature,
    generate_receipt_signing_key,
    load_receipt_signing_key,
    load_receipt_verification_key,
    receipt_key_id,
    sign_merkle_commitment,
)

COMMITMENT = {
    "root": "ab" * 32,
    "tree_size": 4,
    "first_seq": 1,
    "last_seq": 4,
    "through_seq": None,
}


def _signed_receipt(key_path: Path, merkle: dict[str, Any] | None = COMMITMENT) -> dict[str, Any]:
    """Build a receipt whose commitment is signed by the key at ``key_path``."""
    key = load_receipt_signing_key(key_path)
    verification: dict[str, Any] = {"merkle_signature": sign_merkle_commitment(COMMITMENT, key=key)}
    if merkle is not None:
        verification["merkle"] = dict(merkle)
    return {"task_id": "T1", "verification": verification}


def _trusted(pub_path: Path) -> dict[str, bytes]:
    """Load one verification key as a trust mapping."""
    key = load_receipt_verification_key(pub_path)
    return {key.key_id: key.public_key}


@pytest.fixture
def key_path(tmp_path: Path) -> Path:
    """Generate a keypair and return the private-key path."""
    path = tmp_path / "hub-receipt.key"
    generate_receipt_signing_key(path)
    return path


# --- key generation and files -------------------------------------------------------


def test_keygen_writes_owner_only_private_and_shareable_public(key_path: Path) -> None:
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    pub_path = key_path.with_name(key_path.name + ".pub")
    assert stat.S_IMODE(pub_path.stat().st_mode) == 0o644
    document = json.loads(pub_path.read_text(encoding="utf-8"))
    assert document["algorithm"] == "ed25519"
    assert len(document["key_id"]) == KEY_ID_HEX_CHARS
    assert len(bytes.fromhex(document["public_key"])) == 32


def test_keygen_key_id_is_derived_from_the_key_material(key_path: Path) -> None:
    verification = load_receipt_verification_key(key_path.with_name(key_path.name + ".pub"))
    assert verification.key_id == receipt_key_id(verification.public_key)
    signing = load_receipt_signing_key(key_path)
    assert signing.key_id == verification.key_id


def test_keygen_refuses_an_existing_private_key(key_path: Path) -> None:
    with pytest.raises(ReceiptSigningError, match="cannot write receipt-signing key"):
        generate_receipt_signing_key(key_path)


def test_keygen_refuses_an_existing_public_key_and_removes_the_private(
    tmp_path: Path,
) -> None:
    path = tmp_path / "fresh.key"
    path.with_name("fresh.key.pub").write_text("occupied", encoding="utf-8")
    with pytest.raises(ReceiptSigningError, match="cannot write receipt verification key"):
        generate_receipt_signing_key(path)
    assert not path.exists()


def test_keygen_reports_an_unwritable_directory(tmp_path: Path) -> None:
    with pytest.raises(ReceiptSigningError, match="cannot write receipt-signing key"):
        generate_receipt_signing_key(tmp_path / "absent-dir" / "hub.key")


# --- loading keys -------------------------------------------------------------------


def test_load_signing_key_reports_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ReceiptSigningError, match="cannot read receipt-signing key"):
        load_receipt_signing_key(tmp_path / "absent.key")


def test_load_signing_key_rejects_non_pem_content(tmp_path: Path) -> None:
    path = tmp_path / "garbage.key"
    path.write_bytes(b"not a pem")
    path.chmod(0o600)
    with pytest.raises(ReceiptSigningError, match="not a PEM private key"):
        load_receipt_signing_key(path)


def test_load_signing_key_rejects_a_non_ed25519_key(tmp_path: Path) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ec import SECP256R1, generate_private_key

    pem = generate_private_key(SECP256R1()).private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path = tmp_path / "ecdsa.key"
    path.write_bytes(pem)
    path.chmod(0o600)
    with pytest.raises(ReceiptSigningError, match="must be Ed25519"):
        load_receipt_signing_key(path)


def test_load_signing_key_refuses_world_readable_without_key_material(tmp_path: Path) -> None:
    path = tmp_path / "loose.key"
    path.write_text(
        "-----BEGIN PRIVATE KEY-----\nmust-not-appear\n-----END PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    path.chmod(0o644)
    with pytest.raises(ReceiptSigningError, match="cannot read receipt-signing key") as excinfo:
        load_receipt_signing_key(path)
    assert "must-not-appear" not in str(excinfo.value)
    assert "chmod 600" in str(excinfo.value)


def test_load_verification_key_reports_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ReceiptSigningError, match="cannot read receipt verification key"):
        load_receipt_verification_key(tmp_path / "absent.pub")


def test_load_verification_key_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "broken.pub"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ReceiptSigningError, match="cannot read receipt verification key"):
        load_receipt_verification_key(path)


def test_load_verification_key_rejects_a_non_object_document(tmp_path: Path) -> None:
    path = tmp_path / "list.pub"
    path.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ReceiptSigningError, match="must be a JSON object"):
        load_receipt_verification_key(path)


def _pub_document(key_path: Path, **overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = json.loads(
        key_path.with_name(key_path.name + ".pub").read_text(encoding="utf-8")
    )
    document.update(overrides)
    return document


def _write_pub(tmp_path: Path, document: dict[str, Any]) -> Path:
    path = tmp_path / "edited.pub"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_load_verification_key_rejects_a_foreign_algorithm(tmp_path: Path, key_path: Path) -> None:
    path = _write_pub(tmp_path, _pub_document(key_path, algorithm="rsa"))
    with pytest.raises(ReceiptSigningError, match="unsupported verification-key algorithm"):
        load_receipt_verification_key(path)


def test_load_verification_key_rejects_non_hex_material(tmp_path: Path, key_path: Path) -> None:
    path = _write_pub(tmp_path, _pub_document(key_path, public_key="zz"))
    with pytest.raises(ReceiptSigningError, match="not hex"):
        load_receipt_verification_key(path)


def test_load_verification_key_rejects_a_short_key(tmp_path: Path, key_path: Path) -> None:
    path = _write_pub(tmp_path, _pub_document(key_path, public_key="ab" * 16 + "cd"))
    with pytest.raises(ReceiptSigningError, match="must be 32 bytes"):
        load_receipt_verification_key(path)


def test_load_verification_key_rejects_a_mismatched_key_id(tmp_path: Path, key_path: Path) -> None:
    path = _write_pub(tmp_path, _pub_document(key_path, key_id="f" * KEY_ID_HEX_CHARS))
    with pytest.raises(ReceiptSigningError, match="does not match the key material"):
        load_receipt_verification_key(path)


# --- canonical bytes and signing ----------------------------------------------------


def test_canonical_bytes_are_domain_separated_and_key_ordered() -> None:
    payload = canonical_commitment_bytes({"b": 2, "a": 1})
    assert payload.startswith(RECEIPT_COMMITMENT_DOMAIN)
    assert payload.endswith(b'{"a":1,"b":2}')


def test_sign_produces_a_versioned_envelope_naming_the_key(key_path: Path) -> None:
    key = load_receipt_signing_key(key_path)
    envelope = sign_merkle_commitment(COMMITMENT, key=key)
    assert envelope["version"] == 1
    assert envelope["algorithm"] == "ed25519"
    assert envelope["key_id"] == key.key_id
    assert base64.b64decode(str(envelope["value"]), validate=True)


def test_signing_is_deterministic_for_one_commitment(key_path: Path) -> None:
    key = load_receipt_signing_key(key_path)
    first = sign_merkle_commitment(COMMITMENT, key=key)
    second = sign_merkle_commitment(dict(COMMITMENT), key=key)
    assert first == second


# --- verification -------------------------------------------------------------------


def test_check_passes_for_a_trusted_intact_signature(key_path: Path) -> None:
    receipt = _signed_receipt(key_path)
    check = check_receipt_merkle_signature(
        receipt, trusted_keys=_trusted(key_path.with_name(key_path.name + ".pub"))
    )
    assert check.status == "pass"
    assert check.key_id in check.reason
    assert check.verdict is AefVerdictCode.VALID_LEGACY


def test_check_is_not_applicable_without_a_signature() -> None:
    receipt = {"task_id": "T1", "verification": {"merkle": dict(COMMITMENT)}}
    check = check_receipt_merkle_signature(receipt, trusted_keys={})
    assert check.status == "not_applicable"
    assert check.verdict is AefVerdictCode.MALFORMED


def test_check_is_not_applicable_without_a_verification_block() -> None:
    check = check_receipt_merkle_signature({"task_id": "T1"}, trusted_keys={})
    assert check.status == "not_applicable"


def test_check_treats_a_non_object_verification_block_as_unsigned() -> None:
    check = check_receipt_merkle_signature(
        {"task_id": "T1", "verification": "yes"}, trusted_keys={}
    )
    assert check.status == "not_applicable"


def test_check_fails_a_non_object_envelope() -> None:
    receipt = {"verification": {"merkle": dict(COMMITMENT), "merkle_signature": "signed"}}
    check = check_receipt_merkle_signature(receipt, trusted_keys={})
    assert check.status == "fail"
    assert "not an object" in check.reason


def test_check_fails_a_foreign_algorithm(key_path: Path) -> None:
    receipt = _signed_receipt(key_path)
    receipt["verification"]["merkle_signature"]["algorithm"] = "hmac"
    check = check_receipt_merkle_signature(
        receipt, trusted_keys=_trusted(key_path.with_name(key_path.name + ".pub"))
    )
    assert check.status == "fail"
    assert "unsupported algorithm" in check.reason


def test_check_fails_an_unsupported_envelope_version(key_path: Path) -> None:
    receipt = _signed_receipt(key_path)
    receipt["verification"]["merkle_signature"]["version"] = 2
    check = check_receipt_merkle_signature(
        receipt, trusted_keys=_trusted(key_path.with_name(key_path.name + ".pub"))
    )
    assert check.status == "fail"
    assert check.verdict is AefVerdictCode.UNSUPPORTED_VERSION
    assert "unsupported envelope version" in check.reason


def test_check_fails_a_signature_with_no_commitment_to_cover(key_path: Path) -> None:
    receipt = _signed_receipt(key_path, merkle=None)
    check = check_receipt_merkle_signature(
        receipt, trusted_keys=_trusted(key_path.with_name(key_path.name + ".pub"))
    )
    assert check.status == "fail"
    assert "no commitment to cover" in check.reason


def test_check_fails_an_untrusted_key(key_path: Path) -> None:
    receipt = _signed_receipt(key_path)
    check = check_receipt_merkle_signature(receipt, trusted_keys={})
    assert check.status == "fail"
    assert "untrusted key" in check.reason
    assert check.key_id
    assert check.verdict is AefVerdictCode.UNKNOWN_KEY


def test_check_names_a_missing_key_id(key_path: Path) -> None:
    receipt = _signed_receipt(key_path)
    del receipt["verification"]["merkle_signature"]["key_id"]
    check = check_receipt_merkle_signature(receipt, trusted_keys={})
    assert check.status == "fail"
    assert "(missing key_id)" in check.reason


def test_check_fails_a_non_base64_value(key_path: Path) -> None:
    receipt = _signed_receipt(key_path)
    receipt["verification"]["merkle_signature"]["value"] = "@@@"
    check = check_receipt_merkle_signature(
        receipt, trusted_keys=_trusted(key_path.with_name(key_path.name + ".pub"))
    )
    assert check.status == "fail"
    assert "not base64" in check.reason
    assert check.verdict is AefVerdictCode.MALFORMED


def test_check_contains_a_malformed_trusted_key(key_path: Path) -> None:
    receipt = _signed_receipt(key_path)
    key_id = str(receipt["verification"]["merkle_signature"]["key_id"])
    check = check_receipt_merkle_signature(receipt, trusted_keys={key_id: b"short"})
    assert check.status == "fail"
    assert check.verdict is AefVerdictCode.MALFORMED
    assert "verification key is malformed" in check.reason


def test_check_fails_a_tampered_commitment(key_path: Path) -> None:
    receipt = _signed_receipt(key_path)
    receipt["verification"]["merkle"]["root"] = "0" * 64
    check = check_receipt_merkle_signature(
        receipt, trusted_keys=_trusted(key_path.with_name(key_path.name + ".pub"))
    )
    assert check.status == "fail"
    assert "does not verify" in check.reason
    assert check.verdict is AefVerdictCode.INVALID_SIGNATURE


def test_check_fails_a_signature_transplanted_to_another_key(
    tmp_path: Path, key_path: Path
) -> None:
    other = tmp_path / "other.key"
    generated = generate_receipt_signing_key(other)
    receipt = _signed_receipt(key_path)
    receipt["verification"]["merkle_signature"]["key_id"] = generated.key_id
    check = check_receipt_merkle_signature(
        receipt, trusted_keys=_trusted(other.with_name(other.name + ".pub"))
    )
    assert check.status == "fail"
    assert "does not verify" in check.reason
