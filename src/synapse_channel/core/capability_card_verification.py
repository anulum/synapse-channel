# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — signed capability-card verification and diagnostics
"""Verify capability-card signatures, bindings, freshness, and lifecycle state."""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from synapse_channel.core.capability_card_signing import (
    CAPABILITY_CARD_SIGNATURE_ALGORITHM,
    CAPABILITY_CARD_SIGNATURE_VERSION,
    CapabilityCardSigningError,
    canonical_capability_card,
    capability_card_digest,
)
from synapse_channel.core.capability_card_trust import (
    CapabilityCardHistoryResult,
    CapabilityCardTrustBundle,
)


class CapabilityCardVerificationResult(str, Enum):
    """Stable signed-card verification and lifecycle results."""

    VALID = "valid"
    MISSING_SIGNATURE = "missing_signature"
    UNKNOWN_KEY = "unknown_key"
    REVOKED_KEY = "revoked_key"
    BAD_SIGNATURE = "bad_signature"
    EXPIRED = "expired"
    SEQUENCE_MISMATCH = "sequence_mismatch"
    CAPABILITY_DOWNGRADE = "capability_downgrade"
    AGENT_MISMATCH = "agent_mismatch"
    PROJECT_SCOPE_MISMATCH = "project_scope_mismatch"
    MANIFEST_MISMATCH = "manifest_mismatch"
    HISTORY_FULL = "history_full"
    HISTORY_UNAVAILABLE = "history_unavailable"


@dataclass(frozen=True)
class CapabilityCardVerification:
    """Explicit advisory result projected beside one capability card."""

    result: CapabilityCardVerificationResult
    detail: str
    key_id: str = ""
    sequence: int | None = None
    card_digest: str = ""
    signed_at: float | None = None
    expires_at: float | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a compact JSON-serialisable diagnostic object."""
        payload: dict[str, Any] = {"detail": self.detail, "result": self.result.value}
        if self.key_id:
            payload["key_id"] = self.key_id
        if self.sequence is not None:
            payload["sequence"] = self.sequence
        if self.card_digest:
            payload["card_digest"] = self.card_digest
        if self.signed_at is not None:
            payload["signed_at"] = self.signed_at
        if self.expires_at is not None:
            payload["expires_at"] = self.expires_at
        return payload


def verify_capability_card(
    card: Mapping[str, Any],
    *,
    trust_bundle: CapabilityCardTrustBundle,
    now: float,
    required_agent: str,
    required_project: str,
    required_manifest_digest: str = "",
    remember: bool = True,
) -> CapabilityCardVerification:
    """Verify signature, bindings, freshness, sequence, and downgrade policy."""
    signature = card.get("signature")
    if signature is None:
        return _result(
            CapabilityCardVerificationResult.MISSING_SIGNATURE,
            "card is unsigned and remains advisory discovery",
        )
    if not isinstance(signature, Mapping):
        return _result(
            CapabilityCardVerificationResult.BAD_SIGNATURE,
            "card signature is not an object",
        )
    key_id = str(signature.get("key_id") or "").strip()
    context = _signature_context(signature, key_id)
    key = trust_bundle.keys.get(key_id)
    if key is None:
        return _result(
            CapabilityCardVerificationResult.UNKNOWN_KEY,
            "signature key is absent from the capability-card trust bundle",
            **context,
        )
    if key.revoked:
        return _result(
            CapabilityCardVerificationResult.REVOKED_KEY,
            "signature key is revoked",
            **context,
        )
    current = float(now)
    if not _finite(current):
        return _result(
            CapabilityCardVerificationResult.BAD_SIGNATURE,
            "verification time is not finite",
            **context,
        )
    if key.expires_at is not None and key.expires_at < current:
        return _result(
            CapabilityCardVerificationResult.EXPIRED,
            "signature key has expired",
            **context,
        )
    agent = str(card.get("agent") or "").strip()
    if agent != required_agent or required_agent not in key.senders:
        return _result(
            CapabilityCardVerificationResult.AGENT_MISMATCH,
            "card agent does not match the hub-resolved sender and key binding",
            **context,
        )
    project = str(card.get("project") or "").strip()
    if project != required_project or required_project not in key.projects:
        return _result(
            CapabilityCardVerificationResult.PROJECT_SCOPE_MISMATCH,
            "card project does not match the required namespace and key scope",
            **context,
        )
    if required_manifest_digest and str(card.get("manifest_digest") or "") != str(
        required_manifest_digest
    ):
        return _result(
            CapabilityCardVerificationResult.MANIFEST_MISMATCH,
            "card manifest digest does not match the required snapshot",
            **context,
        )
    parsed = _parse_signature_metadata(signature, context)
    if isinstance(parsed, CapabilityCardVerification):
        return parsed
    sequence, signed_at, expires_at, claimed_digest = parsed
    skew = max(float(trust_bundle.clock_skew_seconds), 0.0)
    complete = _context(context, sequence, claimed_digest, signed_at, expires_at)
    if signed_at > current + skew or expires_at < current - skew or expires_at <= signed_at:
        return _result(
            CapabilityCardVerificationResult.EXPIRED,
            "card is outside its signed validity window",
            **complete,
        )
    try:
        computed_digest = capability_card_digest(card)
    except CapabilityCardSigningError:
        return _result(
            CapabilityCardVerificationResult.BAD_SIGNATURE,
            "card contains values outside the strict canonical JSON profile",
            **complete,
        )
    complete = _context(context, sequence, computed_digest, signed_at, expires_at)
    if claimed_digest != computed_digest:
        return _result(
            CapabilityCardVerificationResult.BAD_SIGNATURE,
            "card digest does not match its stable advertisement fields",
            **complete,
        )
    if not _signature_verifies(card, signature, key):
        return _result(
            CapabilityCardVerificationResult.BAD_SIGNATURE,
            "Ed25519 signature does not verify over the canonical card",
            **complete,
        )
    if remember:
        lifecycle = _history_verification(
            trust_bundle.history.assess_and_remember(
                agent=required_agent,
                key_id=key_id,
                sequence=sequence,
                route_capabilities=_route_capabilities(card),
                card_digest=computed_digest,
                expires_at=expires_at,
                now=current,
            ),
            context=complete,
        )
        if lifecycle is not None:
            return lifecycle
    return _result(
        CapabilityCardVerificationResult.VALID,
        "signature, bindings, expiry, and lifecycle checks passed",
        **complete,
    )


def _parse_signature_metadata(
    signature: Mapping[str, Any], context: dict[str, Any]
) -> tuple[int, float, float, str] | CapabilityCardVerification:
    """Parse structural signature metadata or return a failure result."""
    if (
        signature.get("version") != CAPABILITY_CARD_SIGNATURE_VERSION
        or signature.get("algorithm") != CAPABILITY_CARD_SIGNATURE_ALGORITHM
    ):
        return _result(
            CapabilityCardVerificationResult.BAD_SIGNATURE,
            "card signature names an unsupported version or algorithm",
            **context,
        )
    sequence = signature.get("sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        return _result(
            CapabilityCardVerificationResult.SEQUENCE_MISMATCH,
            "card signature sequence must be a positive integer",
            **context,
        )
    raw_signed_at = signature.get("signed_at")
    raw_expires_at = signature.get("expires_at")
    if (
        isinstance(raw_signed_at, bool)
        or not isinstance(raw_signed_at, int | float)
        or isinstance(raw_expires_at, bool)
        or not isinstance(raw_expires_at, int | float)
    ):
        return _result(
            CapabilityCardVerificationResult.BAD_SIGNATURE,
            "card signature timestamps are missing or malformed",
            **context,
        )
    signed_at = float(raw_signed_at)
    expires_at = float(raw_expires_at)
    claimed_digest = str(signature.get("card_digest") or "").strip()
    if not claimed_digest or not _finite(signed_at) or not _finite(expires_at):
        return _result(
            CapabilityCardVerificationResult.BAD_SIGNATURE,
            "card signature digest or timestamps are malformed",
            **context,
        )
    return sequence, signed_at, expires_at, claimed_digest


def _signature_verifies(card: Mapping[str, Any], signature: Mapping[str, Any], key: Any) -> bool:
    """Return whether the supplied Ed25519 signature verifies."""
    supplied = str(signature.get("value") or "")
    if not supplied:
        return False
    from cryptography.exceptions import InvalidSignature

    try:
        decoded = base64.b64decode(supplied, validate=True)
        key.verifier().verify(decoded, canonical_capability_card(card))
    except (ImportError, InvalidSignature, TypeError, ValueError):
        return False
    return True


def _route_capabilities(card: Mapping[str, Any]) -> frozenset[str]:
    """Return the signed route-relevant capability set used for downgrade flags."""
    values: set[str] = set()
    for field_name in ("skills", "task_classes"):
        raw = card.get(field_name)
        if isinstance(raw, list | tuple):
            values.update(f"{field_name}:{str(item).strip()}" for item in raw if str(item).strip())
    contracts = card.get("contracts")
    if isinstance(contracts, list | tuple):
        for contract in contracts:
            if isinstance(contract, Mapping):
                values.add(
                    "contract:"
                    + json.dumps(
                        contract,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                        allow_nan=False,
                    )
                )
    return frozenset(values)


def _history_verification(
    history_result: CapabilityCardHistoryResult,
    *,
    context: dict[str, Any],
) -> CapabilityCardVerification | None:
    """Map lifecycle history outcomes to public verification results."""
    if history_result is CapabilityCardHistoryResult.ACCEPTED:
        return None
    mapping = {
        CapabilityCardHistoryResult.SEQUENCE_MISMATCH: (
            CapabilityCardVerificationResult.SEQUENCE_MISMATCH,
            "card sequence did not increase for this agent and key",
        ),
        CapabilityCardHistoryResult.CAPABILITY_DOWNGRADE: (
            CapabilityCardVerificationResult.CAPABILITY_DOWNGRADE,
            "new signed card removed a route-relevant capability; review required",
        ),
        CapabilityCardHistoryResult.HISTORY_FULL: (
            CapabilityCardVerificationResult.HISTORY_FULL,
            "bounded card history is full; no replay-safe admission state was recorded",
        ),
        CapabilityCardHistoryResult.HISTORY_UNAVAILABLE: (
            CapabilityCardVerificationResult.HISTORY_UNAVAILABLE,
            "card history could not durably record lifecycle state",
        ),
    }
    result, detail = mapping[history_result]
    return _result(result, detail, **context)


def _signature_context(signature: Mapping[str, Any], key_id: str) -> dict[str, Any]:
    """Return best-effort envelope context safe to project on failures."""
    sequence = signature.get("sequence")
    return {
        "key_id": key_id,
        "sequence": (
            sequence if isinstance(sequence, int) and not isinstance(sequence, bool) else None
        ),
    }


def _context(
    base: dict[str, Any],
    sequence: int,
    card_digest: str,
    signed_at: float,
    expires_at: float,
) -> dict[str, Any]:
    """Return complete verification context for a structurally valid envelope."""
    return {
        **base,
        "sequence": sequence,
        "card_digest": card_digest,
        "signed_at": signed_at,
        "expires_at": expires_at,
    }


def _result(
    result: CapabilityCardVerificationResult,
    detail: str,
    **context: Any,
) -> CapabilityCardVerification:
    """Build one verification result while normalising optional context."""
    return CapabilityCardVerification(result=result, detail=detail, **context)


def _finite(value: float) -> bool:
    """Return whether ``value`` is neither NaN nor an infinity."""
    return value == value and value not in (float("inf"), float("-inf"))
