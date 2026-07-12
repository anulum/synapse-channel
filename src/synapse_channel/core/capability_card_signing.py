# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — canonical signing for capability cards
"""Domain-separated Ed25519 signing for advisory capability advertisements.

The signature covers stable card fields plus its own non-secret metadata.  Hub
timestamps and verification diagnostics are excluded because the advertiser
cannot know them before admission.  A card verification result is evidence for
discovery surfaces only: it never grants execution or bypasses ACL policy.
"""

from __future__ import annotations

import base64
import copy
import json
import time
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any

from synapse_channel.core.errors import SynapseError

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

CAPABILITY_CARD_SIGNATURE_ALGORITHM = "ed25519"
"""Algorithm identifier carried in a signed-card envelope."""

CAPABILITY_CARD_SIGNATURE_VERSION = 1
"""Current signed-card envelope schema version."""

DEFAULT_CAPABILITY_CARD_LIFETIME_SECONDS = 300.0
"""Default signed-card lifetime, aligned with the live capability TTL."""

_SIGNATURE_DOMAIN = b"SYNAPSE-CAPABILITY-CARD-SIGNATURE-V1\x00"
_DIGEST_DOMAIN = b"SYNAPSE-CAPABILITY-CARD-DIGEST-V1\x00"


class CapabilityCardSigningError(SynapseError, ValueError):
    """Raised when a card cannot be canonicalised or signed safely."""

    code = "capability_card_signing"


def _stable_card(card: Mapping[str, Any], *, keep_signature: bool) -> dict[str, Any]:
    """Detach stable advertiser-controlled fields from a projected card."""
    stable = copy.deepcopy(dict(card))
    stable.pop("advertised_at", None)
    stable.pop("verification", None)
    if keep_signature:
        signature = stable.get("signature")
        if isinstance(signature, dict):
            signature.pop("value", None)
    else:
        stable.pop("signature", None)
    return stable


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    """Encode a mapping as strict canonical JSON bytes."""
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise CapabilityCardSigningError(f"capability card is not strict JSON: {exc}") from exc
    return rendered.encode("utf-8")


def canonical_capability_card(card: Mapping[str, Any]) -> bytes:
    """Return domain-separated canonical bytes covered by the signature."""
    return _SIGNATURE_DOMAIN + _canonical_json(_stable_card(card, keep_signature=True))


def capability_card_digest(card: Mapping[str, Any]) -> str:
    """Return the stable content digest recorded inside a signature envelope."""
    digest_input = _DIGEST_DOMAIN + _canonical_json(_stable_card(card, keep_signature=False))
    return sha256(digest_input).hexdigest()


def sign_capability_card(
    card: Mapping[str, Any],
    *,
    key_id: str,
    private_key: Ed25519PrivateKey,
    sequence: int,
    signed_at: float | None = None,
    expires_at: float | None = None,
    lifetime_seconds: float = DEFAULT_CAPABILITY_CARD_LIFETIME_SECONDS,
) -> dict[str, Any]:
    """Return a detached card with a domain-separated Ed25519 signature."""
    signed = _stable_card(card, keep_signature=False)
    agent = str(signed.get("agent") or "").strip()
    project = str(signed.get("project") or "").strip()
    public_key_id = str(key_id).strip()
    if not agent or not project or not public_key_id:
        raise CapabilityCardSigningError(
            "a signed capability card requires non-empty agent, project, and key_id"
        )
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise CapabilityCardSigningError("capability-card sequence must be a positive integer")
    issued = time.time() if signed_at is None else float(signed_at)
    lifetime = float(lifetime_seconds)
    expiry = issued + lifetime if expires_at is None else float(expires_at)
    if not _finite(issued) or not _finite(expiry) or not _finite(lifetime):
        raise CapabilityCardSigningError("capability-card timestamps must be finite")
    if expiry <= issued or lifetime <= 0.0:
        raise CapabilityCardSigningError("capability-card expiry must be after its signing time")
    signed["signature"] = {
        "algorithm": CAPABILITY_CARD_SIGNATURE_ALGORITHM,
        "card_digest": capability_card_digest(signed),
        "expires_at": expiry,
        "key_id": public_key_id,
        "sequence": sequence,
        "signed_at": issued,
        "version": CAPABILITY_CARD_SIGNATURE_VERSION,
    }
    value = private_key.sign(canonical_capability_card(signed))
    signed["signature"]["value"] = base64.b64encode(value).decode("ascii")
    return signed


def _finite(value: float) -> bool:
    """Return whether ``value`` is neither NaN nor an infinity."""
    return value == value and value not in (float("inf"), float("-inf"))


def load_capability_card_json(path: str | Path) -> dict[str, Any]:
    """Load a card JSON object while rejecting duplicate object keys."""
    file = Path(path).expanduser()

    def object_from_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise CapabilityCardSigningError(
                    f"duplicate JSON key {key!r} in capability card {file}"
                )
            result[key] = value
        return result

    try:
        loaded = json.loads(file.read_text(encoding="utf-8"), object_pairs_hook=object_from_pairs)
    except FileNotFoundError as exc:
        raise CapabilityCardSigningError(f"capability card does not exist: {file}") from exc
    except OSError as exc:
        raise CapabilityCardSigningError(f"cannot read capability card {file}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CapabilityCardSigningError(f"invalid capability-card JSON: {exc}") from exc
    if not isinstance(loaded, dict):
        raise CapabilityCardSigningError("capability card must be a JSON object")
    _canonical_json(loaded)
    return loaded
