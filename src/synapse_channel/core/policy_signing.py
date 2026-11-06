# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — signed, versioned policy documents and their provenance
"""Sign a policy document, verify it, and name it in a decision report.

A policy may carry an operator-chosen ``policy_version`` and a ``signature``
envelope of exactly ``version``, ``algorithm``, ``key_id`` and ``value``. The
signed bytes are the domain ``SYNAPSE-CHANNEL:POLICY:v1`` and a NUL byte,
followed by the RFC 8785 canonical JSON of the whole document with only
``signature.value`` left out. The key id and the algorithm are therefore bound,
and a client in any language can recompute the bytes. Trust comes from a
separate Ed25519 trust bundle in the MCP config bundle format, so a repository
that can replace a policy cannot also replace the key that authorises it.

Every decision report names its policy through :class:`PolicyProvenance`: the
digest of the canonical policy content, the version, the key id, and whether a
signature was present and whether it was verified.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from synapse_channel.core.frame_canonical import FrameCanonicalError, frame_canonical_json
from synapse_channel.core.mcp_config_signing import parse_ed25519_trust_bundle
from synapse_channel.core.policy_engine import PolicyError

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

POLICY_SIGNATURE_DOMAIN = b"SYNAPSE-CHANNEL:POLICY:v1\x00"
"""Domain separator prepended to the canonical signed bytes."""

POLICY_SIGNATURE_VERSION = 1
"""Only signature envelope version accepted."""

POLICY_SIGNATURE_ALGORITHM = "ed25519"
"""Only signature algorithm accepted."""

POLICY_CANONICAL_FORM = "rfc8785"
"""Name of the canonical form, as reported beside every digest."""

_SIGNATURE_FIELDS = frozenset({"algorithm", "key_id", "value", "version"})


class PolicySigningError(PolicyError):
    """Raised when a policy cannot be signed, or its signature or trust fails."""

    code = "policy_signature"


@dataclass(frozen=True)
class PolicyProvenance:
    """Which policy produced a report, and how far its origin was checked.

    Parameters
    ----------
    digest : str or None
        ``sha256:`` digest of the canonical policy content without its signature;
        None for an unchecked policy holding a value RFC 8785 cannot represent
        (such as a TOML date), which can therefore never be verified either.
    policy_version : int or None
        Operator-chosen version, when the policy declares one.
    key_id : str or None
        Signing key id, when the policy carries a signature.
    signed : bool
        Whether a signature envelope is present.
    verified : bool
        Whether that signature was checked against a trust bundle and held.
    """

    digest: str | None
    policy_version: int | None
    key_id: str | None
    signed: bool
    verified: bool

    def as_dict(self) -> dict[str, Any]:
        """Return the report's ``policy`` block."""
        return {
            "canonical": POLICY_CANONICAL_FORM,
            "digest": self.digest,
            "policy_version": self.policy_version,
            "key_id": self.key_id,
            "signed": self.signed,
            "verified": self.verified,
        }


def load_policy_document(path: str | Path) -> dict[str, Any]:
    """Load a JSON policy document as a mapping, for signing or verification.

    Raises
    ------
    PolicySigningError
        When the file is missing, is not JSON (a TOML policy must be converted
        to JSON before it can be signed or verified), or is not an object.
    """
    target = Path(path)
    if target.suffix.lower() != ".json":
        raise PolicySigningError(
            f"only a JSON policy can be signed or verified; convert {target.name} to JSON"
        )
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise PolicySigningError(f"policy file does not exist: {target}") from exc
    try:
        document = json.loads(raw, object_pairs_hook=_unique_members)
    except json.JSONDecodeError as exc:
        raise PolicySigningError(f"invalid policy JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise PolicySigningError("policy file must contain a mapping at the top level")
    return document


def _unique_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a JSON object, refusing a repeated member name.

    Parsers disagree on which of two equal names wins, so a policy with one
    could mean different rules to the signer and to another verifier.
    """
    members: dict[str, Any] = {}
    for name, value in pairs:
        if name in members:
            raise PolicySigningError(f"policy JSON repeats the member {name!r}")
        members[name] = value
    return members


def _canonical(value: object) -> bytes:
    """Return RFC 8785 bytes, refusing a value that form cannot represent."""
    try:
        return frame_canonical_json(value)
    except FrameCanonicalError as exc:
        raise PolicySigningError(
            f"policy holds a value RFC 8785 canonical JSON cannot represent: {exc}"
        ) from exc


def canonical_policy_signed_bytes(document: Mapping[str, Any]) -> bytes:
    """Return the bytes a policy signature covers: everything but its value."""
    unsigned = dict(document)
    signature = unsigned.get("signature")
    if isinstance(signature, dict):
        unsigned["signature"] = {key: item for key, item in signature.items() if key != "value"}
    return POLICY_SIGNATURE_DOMAIN + _canonical(unsigned)


def policy_digest(document: Mapping[str, Any]) -> str:
    """Return the ``sha256:`` digest of the policy content without its signature."""
    content = {key: item for key, item in document.items() if key != "signature"}
    return "sha256:" + hashlib.sha256(_canonical(content)).hexdigest()


def _policy_version(document: Mapping[str, Any]) -> int:
    """Return the ``policy_version`` a signed policy must declare."""
    if "policy_version" not in document:
        raise PolicySigningError("a signed policy must declare policy_version")
    version = document["policy_version"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise PolicySigningError("policy_version must be an integer of at least 1")
    return version


def sign_policy_document(
    document: Mapping[str, Any], *, private_key: Ed25519PrivateKey, key_id: str
) -> dict[str, Any]:
    """Return a copy of ``document`` with a fresh signature envelope.

    Raises
    ------
    PolicySigningError
        When ``policy_version`` is missing or invalid, ``key_id`` is empty or
        padded, or the document holds a value RFC 8785 cannot represent.
    """
    _policy_version(document)
    if not key_id or key_id != key_id.strip():
        raise PolicySigningError("key_id must be non-empty without surrounding whitespace")
    signed = dict(document)
    signed["signature"] = {
        "version": POLICY_SIGNATURE_VERSION,
        "algorithm": POLICY_SIGNATURE_ALGORITHM,
        "key_id": key_id,
    }
    value = private_key.sign(canonical_policy_signed_bytes(signed))
    signed["signature"]["value"] = base64.b64encode(value).decode("ascii")
    return signed


def _envelope(document: Mapping[str, Any]) -> tuple[str, bytes]:
    """Return the key id and raw signature of a well-formed envelope."""
    signature = document.get("signature")
    if not isinstance(signature, dict):
        raise PolicySigningError("the policy is not signed")
    if set(signature) != _SIGNATURE_FIELDS:
        raise PolicySigningError(
            "policy signature must contain exactly version, algorithm, key_id, value"
        )
    version = signature["version"]
    if isinstance(version, bool) or version != POLICY_SIGNATURE_VERSION:
        raise PolicySigningError(f"policy signature version must be {POLICY_SIGNATURE_VERSION}")
    if signature["algorithm"] != POLICY_SIGNATURE_ALGORITHM:
        raise PolicySigningError(f"policy signature algorithm must be {POLICY_SIGNATURE_ALGORITHM}")
    key_id = signature["key_id"]
    if not isinstance(key_id, str) or not key_id or key_id != key_id.strip():
        raise PolicySigningError(
            "policy signature key_id must be non-empty without surrounding whitespace"
        )
    value = signature["value"]
    if not isinstance(value, str):
        raise PolicySigningError("policy signature value must be base64 text")
    try:
        return key_id, base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise PolicySigningError("policy signature value is not valid base64") from exc


def verify_policy_document(document: Mapping[str, Any], trust_document: Mapping[str, Any]) -> str:
    """Verify ``document`` against a trust bundle and return its key id.

    Raises
    ------
    PolicySigningError
        When the policy is unsigned, lacks ``policy_version``, has a malformed
        envelope, names an unknown or revoked key, or fails verification.
    """
    _policy_version(document)
    key_id, raw_signature = _envelope(document)
    keys = parse_ed25519_trust_bundle(trust_document, label="policy", error=PolicySigningError)
    public_key = keys.get(key_id)
    if public_key is None:
        raise PolicySigningError(f"policy signature key {key_id!r} is not trusted")
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            raw_signature, canonical_policy_signed_bytes(document)
        )
    except InvalidSignature as exc:
        raise PolicySigningError("policy signature verification failed") from exc
    return key_id


def _unchecked_provenance(document: Mapping[str, Any]) -> PolicyProvenance:
    """Describe a policy without checking any signature it carries."""
    signature = document.get("signature")
    key_id = signature.get("key_id") if isinstance(signature, dict) else None
    version = document.get("policy_version")
    try:
        digest: str | None = policy_digest(document)
    except PolicySigningError:
        digest = None
    return PolicyProvenance(
        digest=digest,
        policy_version=version
        if isinstance(version, int) and not isinstance(version, bool)
        else None,
        key_id=key_id if isinstance(key_id, str) else None,
        signed=isinstance(signature, dict),
        verified=False,
    )


def policy_provenance(
    document: Mapping[str, Any],
    *,
    trust_document: Mapping[str, Any] | None = None,
    min_policy_version: int | None = None,
) -> PolicyProvenance:
    """Return the provenance of ``document``, verifying it when trust is given.

    Without a trust bundle the provenance is descriptive only: ``verified`` is
    false and no signature is checked. With one, verification failures raise,
    and so does a ``policy_version`` below ``min_policy_version``.

    Raises
    ------
    PolicySigningError
        When verification is requested and fails, or a floor is given without a
        trust bundle (an unverified floor would not be a control).
    """
    if min_policy_version is not None and trust_document is None:
        raise PolicySigningError("--min-policy-version needs --policy-trust-bundle")
    if trust_document is None:
        return _unchecked_provenance(document)
    key_id = verify_policy_document(document, trust_document)
    version = _policy_version(document)
    if min_policy_version is not None and version < min_policy_version:
        raise PolicySigningError(
            f"policy_version {version} is below the required minimum {min_policy_version}"
        )
    return PolicyProvenance(
        digest=policy_digest(document),
        policy_version=version,
        key_id=key_id,
        signed=True,
        verified=True,
    )
