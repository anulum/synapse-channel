# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — hub-key signing of a receipt's Merkle commitment
"""Sign and verify a release receipt's coordination-log commitment with a hub key.

A verified release receipt can already commit to the exact coordination history
behind a release (``verification.merkle``, the RFC 6962 root the event store had
at release time), and :func:`~synapse_channel.core.release_verification.
check_receipt_merkle_commitment` recomputes that prefix later. What the bare
commitment cannot give a third party is provenance: whoever holds the receipt
file could have written any root into it. This module closes that gap with an
Ed25519 signature by the hub deployment's receipt-signing key over the canonical
commitment, so a verifier holding only the receipt and the deployment's public
key can check that *this hub* attested *this log state* — no trust in whoever
delivered the file, and no access to the live log, required.

The signature is deterministic and replay-safe by construction: it covers a
domain-separated canonicalisation of the commitment itself (root, bounds, tree
size), so the same signature re-attached elsewhere still only ever attests that
one log state, and a commitment signature can never double as an event or frame
signature. Verification is deny-by-default: an unknown key, a malformed
envelope, or a signature without a commitment all fail rather than degrade.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from synapse_channel.core.errors import SynapseError

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

RECEIPT_COMMITMENT_DOMAIN = b"synapse-channel:release-receipt-merkle-commitment:v1\n"
"""Domain-separation prefix signed with the canonical commitment bytes."""

RECEIPT_SIGNATURE_ALGORITHM = "ed25519"
"""Algorithm name recorded in the signature envelope and key files."""

RECEIPT_SIGNATURE_VERSION = 1
"""Envelope schema version recorded alongside the signature."""

KEY_ID_HEX_CHARS = 16
"""Fingerprint length: leading hex characters of SHA-256 over the public key."""

_PUBLIC_KEY_BYTES = 32
"""Raw Ed25519 public key length."""


class ReceiptSigningError(SynapseError, ValueError):
    """A receipt-signing key could not be generated, loaded, or used."""

    code = "receipt_signing"


@dataclass(frozen=True)
class ReceiptVerificationKey:
    """The public half of a receipt-signing key.

    Attributes
    ----------
    key_id : str
        Fingerprint of the public key (see :func:`receipt_key_id`), carried as
        ``key_id`` in signature envelopes and key files.
    public_key : bytes
        Raw Ed25519 public key bytes.
    """

    key_id: str
    public_key: bytes


@dataclass(frozen=True)
class ReceiptSigningKey:
    """A loaded private receipt-signing key and its fingerprint.

    Attributes
    ----------
    key_id : str
        Fingerprint of the corresponding public key.
    private_key : Ed25519PrivateKey
        The signing key itself.
    """

    key_id: str
    private_key: Ed25519PrivateKey


class MerkleSignatureCheck(NamedTuple):
    """The outcome of verifying a receipt's commitment signature.

    Attributes
    ----------
    status : str
        ``"pass"`` when a trusted key's signature verifies over the receipt's
        commitment, ``"fail"`` for any defect (malformed envelope, untrusted or
        mismatched key, bad signature, or a signature with no commitment to
        cover), ``"not_applicable"`` when the receipt carries no signature.
    reason : str
        One line explaining the outcome.
    key_id : str
        The envelope's key id when one was present; empty otherwise.
    """

    status: str
    reason: str
    key_id: str = ""


def receipt_key_id(public_key: bytes) -> str:
    """Return the fingerprint identifying a receipt-signing public key.

    The fingerprint is the leading :data:`KEY_ID_HEX_CHARS` hex characters of
    SHA-256 over the raw public key bytes, so the id is derived from — never
    asserted alongside — the key material.
    """
    return hashlib.sha256(public_key).hexdigest()[:KEY_ID_HEX_CHARS]


def _public_bytes(private_key: Ed25519PrivateKey) -> bytes:
    """Return the raw public key bytes for ``private_key``."""
    from cryptography.hazmat.primitives import serialization

    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _write_exclusive(path: Path, payload: bytes, *, mode: int) -> None:
    """Create ``path`` with ``mode``, refusing to overwrite an existing file."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)


def generate_receipt_signing_key(path: str | Path) -> ReceiptVerificationKey:
    """Generate a receipt-signing keypair for this hub deployment.

    The private key is written to ``path`` as unencrypted PKCS#8 PEM with
    owner-only (``0600``) permissions; the public half goes to ``path.pub`` as a
    small JSON document (``algorithm``, ``key_id``, ``public_key`` hex) that is
    safe to distribute to verifiers. Both writes are exclusive-create, so an
    existing key is never silently overwritten.

    Returns
    -------
    ReceiptVerificationKey
        The generated key's fingerprint and public bytes.

    Raises
    ------
    ReceiptSigningError
        When either file already exists or cannot be written.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private_path = Path(path)
    public_path = private_path.with_name(private_path.name + ".pub")
    private_key = Ed25519PrivateKey.generate()
    public_key = _public_bytes(private_key)
    key_id = receipt_key_id(public_key)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_document = json.dumps(
        {
            "algorithm": RECEIPT_SIGNATURE_ALGORITHM,
            "key_id": key_id,
            "public_key": public_key.hex(),
        },
        indent=2,
        sort_keys=True,
    )
    try:
        _write_exclusive(private_path, pem, mode=0o600)
    except OSError as exc:
        msg = f"cannot write receipt-signing key {private_path}: {exc}"
        raise ReceiptSigningError(msg) from exc
    try:
        _write_exclusive(public_path, (public_document + "\n").encode("utf-8"), mode=0o644)
    except OSError as exc:
        private_path.unlink(missing_ok=True)
        msg = f"cannot write receipt verification key {public_path}: {exc}"
        raise ReceiptSigningError(msg) from exc
    return ReceiptVerificationKey(key_id=key_id, public_key=public_key)


def load_receipt_signing_key(path: str | Path) -> ReceiptSigningKey:
    """Load the private receipt-signing key written by ``merkle keygen``.

    The private PEM is read through the shared owner-only secret floor so a
    group/world-readable or symlinked key file is refused the same way as hub
    connect-token files. The error never includes key material.

    Raises
    ------
    ReceiptSigningError
        When the file is unreadable, is not owner-only, is not PEM, or holds a
        non-Ed25519 key.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from synapse_channel.core.secret_files import SecretFileError, read_secret_file

    key_path = Path(path)
    try:
        # PEM is UTF-8; strip only outer whitespace (matches exclusive 0o600 write).
        pem_text = read_secret_file(key_path, flag="receipt-signing-key")
    except SecretFileError as exc:
        msg = f"cannot read receipt-signing key {key_path}: {exc}"
        raise ReceiptSigningError(msg) from exc
    pem = pem_text.encode("utf-8")
    try:
        private_key = serialization.load_pem_private_key(pem, password=None)
    except (ValueError, TypeError) as exc:
        msg = f"not a PEM private key: {key_path}: {exc}"
        raise ReceiptSigningError(msg) from exc
    if not isinstance(private_key, Ed25519PrivateKey):
        msg = f"receipt-signing key must be Ed25519: {key_path}"
        raise ReceiptSigningError(msg)
    return ReceiptSigningKey(
        key_id=receipt_key_id(_public_bytes(private_key)),
        private_key=private_key,
    )


def load_receipt_verification_key(path: str | Path) -> ReceiptVerificationKey:
    """Load a ``.pub`` verification-key document written by ``merkle keygen``.

    The document is validated for internal consistency: the algorithm must be
    Ed25519, the public key must be 32 raw bytes, and the recorded ``key_id``
    must equal the fingerprint recomputed from the key material — a document
    asserting a foreign id for its key is rejected rather than trusted.

    Raises
    ------
    ReceiptSigningError
        When the file is unreadable, malformed, or self-inconsistent.
    """
    from synapse_channel.core.secret_files import SecretFileError, read_regular_file_bytes

    key_path = Path(path)
    try:
        raw = read_regular_file_bytes(key_path, label="receipt-verification-key")
        document = json.loads(raw.decode("utf-8"))
    except SecretFileError as exc:
        msg = f"cannot read receipt verification key {key_path}: {exc}"
        raise ReceiptSigningError(msg) from exc
    except (UnicodeDecodeError, ValueError) as exc:
        msg = f"cannot read receipt verification key {key_path}: {exc}"
        raise ReceiptSigningError(msg) from exc
    if not isinstance(document, Mapping):
        msg = f"receipt verification key must be a JSON object: {key_path}"
        raise ReceiptSigningError(msg)
    if document.get("algorithm") != RECEIPT_SIGNATURE_ALGORITHM:
        msg = f"unsupported verification-key algorithm in {key_path}"
        raise ReceiptSigningError(msg)
    try:
        public_key = bytes.fromhex(str(document.get("public_key") or ""))
    except ValueError as exc:
        msg = f"verification key is not hex in {key_path}"
        raise ReceiptSigningError(msg) from exc
    if len(public_key) != _PUBLIC_KEY_BYTES:
        msg = f"verification key must be {_PUBLIC_KEY_BYTES} bytes in {key_path}"
        raise ReceiptSigningError(msg)
    key_id = receipt_key_id(public_key)
    if document.get("key_id") != key_id:
        msg = f"key_id does not match the key material in {key_path}"
        raise ReceiptSigningError(msg)
    return ReceiptVerificationKey(key_id=key_id, public_key=public_key)


def canonical_commitment_bytes(merkle: Mapping[str, object]) -> bytes:
    """Return the domain-separated canonical bytes a commitment signature covers."""
    return RECEIPT_COMMITMENT_DOMAIN + json.dumps(
        dict(merkle),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sign_merkle_commitment(
    merkle: Mapping[str, object],
    *,
    key: ReceiptSigningKey,
) -> dict[str, object]:
    """Sign a receipt's Merkle commitment and return the signature envelope.

    Parameters
    ----------
    merkle : Mapping[str, object]
        The ``verification.merkle`` commitment being attested (as produced by
        :func:`~synapse_channel.core.merkle.root_to_json`).
    key : ReceiptSigningKey
        The hub deployment's receipt-signing key.

    Returns
    -------
    dict[str, object]
        The ``verification.merkle_signature`` envelope: ``version``,
        ``algorithm``, ``key_id``, and the base64 ``value``.
    """
    signature = key.private_key.sign(canonical_commitment_bytes(merkle))
    return {
        "version": RECEIPT_SIGNATURE_VERSION,
        "algorithm": RECEIPT_SIGNATURE_ALGORITHM,
        "key_id": key.key_id,
        "value": base64.b64encode(signature).decode("ascii"),
    }


def check_receipt_merkle_signature(
    receipt: Mapping[str, object],
    *,
    trusted_keys: Mapping[str, bytes],
) -> MerkleSignatureCheck:
    """Verify a receipt's commitment signature against trusted hub keys.

    Deny-by-default: every defect is a ``fail`` — a signature envelope with no
    commitment to cover, a malformed envelope, a key id outside
    ``trusted_keys``, or a signature that does not verify. Only a receipt with
    no signature at all is ``not_applicable``, so an unsigned receipt is
    visible rather than wrongly failed.

    Parameters
    ----------
    receipt : Mapping[str, object]
        A parsed release-receipt JSON document.
    trusted_keys : Mapping[str, bytes]
        Trusted raw Ed25519 public keys by fingerprint, from
        :func:`load_receipt_verification_key`.

    Returns
    -------
    MerkleSignatureCheck
        Pass/fail/not-applicable with the envelope's key id when present.
    """
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    verification = receipt.get("verification")
    if not isinstance(verification, Mapping):
        verification = {}
    envelope = verification.get("merkle_signature")
    if envelope is None:
        return MerkleSignatureCheck(
            status="not_applicable",
            reason="receipt carries no commitment signature",
        )
    if not isinstance(envelope, Mapping):
        return MerkleSignatureCheck(status="fail", reason="commitment signature is not an object")
    key_id = str(envelope.get("key_id") or "")
    if envelope.get("algorithm") != RECEIPT_SIGNATURE_ALGORITHM:
        return MerkleSignatureCheck(
            status="fail",
            reason="commitment signature names an unsupported algorithm",
            key_id=key_id,
        )
    merkle = verification.get("merkle")
    if not isinstance(merkle, Mapping):
        return MerkleSignatureCheck(
            status="fail",
            reason="commitment signature present but the receipt has no commitment to cover",
            key_id=key_id,
        )
    public_key = trusted_keys.get(key_id)
    if public_key is None:
        return MerkleSignatureCheck(
            status="fail",
            reason=f"commitment signed by an untrusted key: {key_id or '(missing key_id)'}",
            key_id=key_id,
        )
    try:
        signature = base64.b64decode(str(envelope.get("value") or ""), validate=True)
    except ValueError:
        return MerkleSignatureCheck(
            status="fail",
            reason="commitment signature value is not base64",
            key_id=key_id,
        )
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature, canonical_commitment_bytes(merkle)
        )
    except InvalidSignature:
        return MerkleSignatureCheck(
            status="fail",
            reason="commitment signature does not verify over the recorded commitment",
            key_id=key_id,
        )
    return MerkleSignatureCheck(
        status="pass",
        reason=f"hub key {key_id} attested this coordination-log commitment",
        key_id=key_id,
    )
