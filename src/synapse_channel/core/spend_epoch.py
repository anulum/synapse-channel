# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — fence a pool owner's epoch and hand the pool to a new owner (F02 phase 5)
"""Revoke a pool owner's epoch with an operator signature, and prove the ledger handed over.

A pool owner can fail. The reviewed contract fences it with three pieces of
evidence, all required:

1. **An operator-signed owner revocation for epoch n.** The operator signs it with an
   Ed25519 key that the pool's configuration lists under ``revocation_keys``. The
   revocation is distributed to every peer, and a peer refuses to *start* new
   consumption on any grant of an epoch at or below a revoked one
   (:func:`usable_grant`).
2. **The ledger state that epoch n+1 starts from.** The revocation names the pool's
   ledger sequence and chain digest (:func:`pool_ledger_digest`) of the verified copy
   the new owner holds. The new owner refuses a copy that does not match exactly.
3. **The operator's attestation** that the old owner's signing key is revoked or its
   host is retired.

A heartbeat timeout, a database copy on its own, or a replicated balance never
suffices. Grants of epoch n remain outstanding exposure in epoch n+1 until they are
settled or reconciled (review correction C1).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.spend_pool import SpendPoolError, timestamp, token

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ATTESTATIONS = ("old_key_revoked", "host_retired")
"""What the operator attests about the failed owner (reviewed condition 3)."""

_GENESIS = hashlib.sha256(b"synapse-spend-ledger-v1").hexdigest()
_BODY_FIELDS = frozenset(
    {
        "pool_id",
        "revoked_epoch",
        "new_epoch",
        "new_owner_hub_id",
        "ledger_sequence",
        "ledger_digest",
        "attestation",
        "cause",
        "issued_at",
    }
)
_SIGNED_FIELDS = frozenset({"revocation", "key_id", "signature"})
_DIGEST_HEX = frozenset("0123456789abcdef")


class SpendEpochError(SynapseError, ValueError):
    """Raised when an owner revocation is malformed, unsigned or does not verify."""

    code = "spend_epoch"


def _canonical(document: Mapping[str, Any]) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def pool_ledger_digest(events: Iterable[Mapping[str, Any]]) -> str:
    """Return the chain digest over one pool's events (``seq``, ``kind``, ``body``), in order."""
    digest = _GENESIS
    for event in events:
        record = {"seq": event["seq"], "kind": event["kind"], "body": event["body"]}
        digest = hashlib.sha256(digest.encode("ascii") + _canonical(record)).hexdigest()
    return digest


@dataclass(frozen=True)
class OwnerRevocation:
    """A verified operator revocation of one pool owner's epoch."""

    pool_id: str
    revoked_epoch: int
    new_epoch: int
    new_owner_hub_id: str
    ledger_sequence: int
    ledger_digest: str
    attestation: str
    cause: str
    issued_at: str
    key_id: str

    def body(self) -> dict[str, object]:
        """Return the signed body."""
        return {
            "pool_id": self.pool_id,
            "revoked_epoch": self.revoked_epoch,
            "new_epoch": self.new_epoch,
            "new_owner_hub_id": self.new_owner_hub_id,
            "ledger_sequence": self.ledger_sequence,
            "ledger_digest": self.ledger_digest,
            "attestation": self.attestation,
            "cause": self.cause,
            "issued_at": self.issued_at,
        }


def _positive(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SpendEpochError(f"{name} must be a positive integer")
    return value


def _validate_body(body: object) -> dict[str, Any]:
    if not isinstance(body, Mapping) or set(body) != _BODY_FIELDS:
        raise SpendEpochError("a revocation has exactly the documented fields")
    revoked = _positive(body["revoked_epoch"], "revoked_epoch")
    if _positive(body["new_epoch"], "new_epoch") != revoked + 1:
        raise SpendEpochError("new_epoch must follow the revoked epoch directly")
    _positive(body["ledger_sequence"], "ledger_sequence")
    digest = body["ledger_digest"]
    if not isinstance(digest, str) or len(digest) != 64 or not set(digest) <= _DIGEST_HEX:
        raise SpendEpochError("ledger_digest must be a SHA-256 hex digest")
    if body["attestation"] not in ATTESTATIONS:
        raise SpendEpochError(f"attestation must be one of {', '.join(ATTESTATIONS)}")
    cause = body["cause"]
    if not isinstance(cause, str) or not cause.strip() or len(cause) > 500:
        raise SpendEpochError("a revocation needs a cause")
    try:
        token(body["pool_id"], "pool_id")
        token(body["new_owner_hub_id"], "new_owner_hub_id")
        timestamp(body["issued_at"], "issued_at")
    except SpendPoolError as exc:
        raise SpendEpochError(str(exc)) from exc
    return dict(body)


def sign_owner_revocation(
    body: Mapping[str, Any], private_key: Ed25519PrivateKey, key_id: str
) -> dict[str, object]:
    """Return the operator-signed revocation document for ``body``.

    Raises
    ------
    SpendEpochError
        When the body is malformed.
    """
    checked = _validate_body(body)
    signature = private_key.sign(_canonical(checked))
    return {
        "revocation": checked,
        "key_id": key_id,
        "signature": base64.b64encode(signature).decode("ascii"),
    }


def verify_owner_revocation(document: object, keys: Mapping[str, str]) -> OwnerRevocation:
    """Return the revocation when an operator key in ``keys`` signed it.

    Parameters
    ----------
    document : object
        The signed revocation document.
    keys : Mapping[str, str]
        Trusted operator keys, key id to base64 raw Ed25519 public key; normally the
        pool configuration's ``revocation_keys``.

    Raises
    ------
    SpendEpochError
        When the document is malformed, names an unknown key, or does not verify.
    """
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if not isinstance(document, Mapping) or set(document) != _SIGNED_FIELDS:
        raise SpendEpochError("a signed revocation has exactly revocation, key_id and signature")
    body = _validate_body(document["revocation"])
    key_id = document["key_id"]
    public = keys.get(key_id) if isinstance(key_id, str) else None
    if public is None:
        raise SpendEpochError("the revocation is signed by a key the pool does not trust")
    try:
        signature = base64.b64decode(str(document["signature"]), validate=True)
        verifier = Ed25519PublicKey.from_public_bytes(base64.b64decode(public))
        verifier.verify(signature, _canonical(body))
    except (binascii.Error, ValueError, InvalidSignature) as exc:
        raise SpendEpochError("the revocation signature does not verify") from exc
    return OwnerRevocation(key_id=str(key_id), **body)


def usable_grant(grant: Mapping[str, Any], revocations: Iterable[OwnerRevocation]) -> bool:
    """Return whether a peer may start consumption on ``grant``.

    A grant of a revoked epoch, or of any earlier epoch of the same pool, must not
    start new consumption. It may still be settled.
    """
    pool_id = grant.get("pool_id")
    epoch = grant.get("epoch")
    if grant.get("admitted") is not True or isinstance(epoch, bool) or not isinstance(epoch, int):
        return False
    return all(
        revocation.pool_id != pool_id or epoch > revocation.revoked_epoch
        for revocation in revocations
    )
