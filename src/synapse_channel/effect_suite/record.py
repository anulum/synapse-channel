# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — one signed, offline-verifiable result record per suite variant
"""Write, sign and verify the effect-safety suite's result records.

A record is canonical JSON (RFC 8785 under Core's integer-only evidence
profile). Its signature is Ed25519 over a domain-separated encoding of the
record without its signature, so a record signature can never double as any
other signature of the same key. The key is the operator's: a private key file
in the format ``synapse merkle keygen`` writes, read only when it is owner-only
and has no second link. Anyone holding the public key file verifies a record
offline.

Signing and verifying need the optional ``cryptography`` package, imported only
when they run, so the command line still starts on a core-only install.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from synapse_channel.core.aef_canonical import canonical_json
from synapse_channel.core.errors import SynapseError
from synapse_channel.core.receipt_signing import ReceiptVerificationKey, receipt_key_id
from synapse_channel.core.secure_path import read_owner_only_file_bytes

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

RECORD_SCHEMA = "synapse.effect-suite-result.v0"
SIGNATURE_DOMAIN = b"synapse.effect-suite-result.v0\n"
CUSTODY = "an operator-held key; the group's decision on key custody is still open"
MAX_KEY_BYTES = 16 * 1024
NEEDS_CRYPTOGRAPHY = (
    "the effect suite signs and verifies with the cryptography package, which the "
    "encryption extra installs: pip install 'synapse-channel[encryption]'"
)
"""Why signing and verifying are refused on a core-only install."""


class EffectSuiteKeyError(SynapseError, ValueError):
    """The suite cannot sign or verify: an unusable key file, or no ``cryptography``."""

    code = "effect_suite_key"


def require_cryptography() -> None:
    """Refuse with words when the ``cryptography`` package is not installed.

    Raises
    ------
    EffectSuiteKeyError
        With :data:`NEEDS_CRYPTOGRAPHY` when the package cannot be imported.
    """
    try:
        import cryptography  # noqa: F401 - the import is the availability check
    except ImportError as exc:
        raise EffectSuiteKeyError(NEEDS_CRYPTOGRAPHY) from exc


@dataclass(frozen=True)
class SigningKey:
    """A loaded Ed25519 signing key and the id of its public half."""

    key_id: str
    private_key: Ed25519PrivateKey


def load_signing_key(path: Path) -> SigningKey:
    """Read the operator's private key file; refuse one that others could read or a second link.

    Raises
    ------
    EffectSuiteKeyError
        When ``cryptography`` is not installed, or the file is not owner-only,
        has a second link, cannot be read, or does not hold an Ed25519 private
        key. The key is never echoed.
    """
    require_cryptography()
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        PublicFormat,
        load_pem_private_key,
    )

    try:
        pem = read_owner_only_file_bytes(
            path,
            purpose="effect-suite signing key",
            max_bytes=MAX_KEY_BYTES,
            require_single_link=True,
        )
        key = load_pem_private_key(pem, password=None)
    except (OSError, ValueError, TypeError) as exc:
        raise EffectSuiteKeyError(
            f"cannot use the signing key {path}: {type(exc).__name__}"
        ) from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise EffectSuiteKeyError(f"the signing key {path} is not an Ed25519 key")
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return SigningKey(receipt_key_id(public), key)


def record_bytes(record: Mapping[str, Any]) -> bytes:
    """Return the canonical bytes of ``record`` without its signature."""
    return canonical_json({name: value for name, value in record.items() if name != "signature"})


def sign_record(record: Mapping[str, Any], key: SigningKey) -> dict[str, Any]:
    """Return ``record`` with its signature by ``key``."""
    value = key.private_key.sign(SIGNATURE_DOMAIN + record_bytes(record))
    signature = {
        "algorithm": "ed25519",
        "key_id": key.key_id,
        "custody": CUSTODY,
        "value": base64.b64encode(value).decode("ascii"),
    }
    return {**record, "signature": signature}


def verification_refusal(record: Mapping[str, Any], key: ReceiptVerificationKey) -> str | None:
    """Return why ``record`` does not verify under ``key``, or ``None`` when it does.

    Call :func:`require_cryptography` first: without the package this raises
    ``ImportError``.
    """
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    signature = record.get("signature")
    if not isinstance(signature, Mapping) or record.get("schema") != RECORD_SCHEMA:
        return "not a signed effect-suite record"
    if signature.get("algorithm") != "ed25519" or signature.get("key_id") != key.key_id:
        return "signed by another key or algorithm"
    try:
        raw = base64.b64decode(str(signature.get("value") or ""), validate=True)
        Ed25519PublicKey.from_public_bytes(key.public_key).verify(
            raw, SIGNATURE_DOMAIN + record_bytes(record)
        )
    except (InvalidSignature, ValueError):
        return "the signature does not match the record"
    return None
