# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — pure capability-bound setup request and plan contract
"""Validate setup documents and derive plans without exposing an effect seam.

This module deliberately has no HTTP, filesystem, subprocess, secret, or
service-manager dependency. Importing it cannot arm a route or mutate a host.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Literal, NoReturn, cast
from uuid import UUID

MAX_SETUP_REQUEST_BYTES = 4096
SETUP_CONTRACT_VERSION = 1

SetupProfileId = Literal["local-ephemeral", "local-durable-existing"]
SetupEffectKind = Literal["runtime_directory", "user_unit", "durable_store"]
SetupEffectChange = Literal["create_if_absent", "install_or_match", "verify_existing"]
SetupPostureReason = Literal[
    "ready",
    "unarmed",
    "non_loopback",
    "compatibility_access",
    "access_file_required",
    "receipt_store_unavailable",
]
SetupErrorCode = Literal[
    "body_size",
    "invalid_json",
    "invalid_fields",
    "invalid_version",
    "invalid_request_id",
    "unknown_profile",
    "invalid_plan_id",
    "invalid_plan_digest",
    "invalid_confirmation_nonce",
    "confirmation_required",
]

_PLAN_FIELDS = frozenset({"version", "profile", "request_id"})
_APPLY_FIELDS = frozenset(
    {
        "version",
        "request_id",
        "plan_id",
        "plan_digest",
        "confirmation_nonce",
        "confirm",
    }
)
_LOWER_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_OPAQUE_ID = re.compile(r"[A-Za-z0-9_-]{22,128}\Z")


@dataclass(frozen=True)
class SetupContractError:
    """One stable, non-reflective setup request refusal."""

    code: SetupErrorCode


@dataclass(frozen=True)
class SetupEffect:
    """One allow-listed effect named without a host filesystem path."""

    kind: SetupEffectKind
    target: str
    change: SetupEffectChange

    def as_dict(self) -> dict[str, str]:
        """Return the canonical token-only projection."""
        return {"kind": self.kind, "target": self.target, "change": self.change}


@dataclass(frozen=True)
class SetupProfile:
    """One immutable package-owned local setup profile."""

    profile_id: SetupProfileId
    version: int
    effects: tuple[SetupEffect, ...]


@dataclass(frozen=True)
class SetupPlanRequest:
    """Strict browser request for one server-owned setup profile."""

    request_id: str
    profile: SetupProfileId


@dataclass(frozen=True)
class SetupApplyRequest:
    """Strict explicit confirmation referencing an existing server-side plan."""

    request_id: str
    plan_id: str
    plan_digest: str
    confirmation_nonce: str


@dataclass(frozen=True)
class SetupPlan:
    """Canonical effect plan; it contains neither a secret nor an executable."""

    request_id: str
    profile: SetupProfileId
    profile_version: int
    configuration_generation: str
    expires_at: int
    effects: tuple[SetupEffect, ...]

    def as_dict(self) -> dict[str, object]:
        """Return the stable digest input."""
        return {
            "version": SETUP_CONTRACT_VERSION,
            "request_id": self.request_id,
            "profile": self.profile,
            "profile_version": self.profile_version,
            "configuration_generation": self.configuration_generation,
            "expires_at": self.expires_at,
            "effects": [effect.as_dict() for effect in self.effects],
        }

    def canonical_bytes(self) -> bytes:
        """Serialize deterministically for receipt and confirmation binding."""
        return json.dumps(
            self.as_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    @property
    def digest(self) -> str:
        """Return the lowercase SHA-256 canonical-plan digest."""
        return sha256(self.canonical_bytes()).hexdigest()


@dataclass(frozen=True)
class SetupPosture:
    """Startup facts required before setup capabilities may be advertised."""

    feature_armed: bool
    loopback_bind: bool
    compatibility_access: bool
    versioned_access_file: bool
    receipt_store_ready: bool


@dataclass(frozen=True)
class SetupPostureDecision:
    """Fail-closed setup capability-advertisement decision."""

    advertised: bool
    reason: SetupPostureReason


_USER_UNITS = (
    SetupEffect("user_unit", "synapse-hub.service", "install_or_match"),
    SetupEffect("user_unit", "synapse-presence@.service", "install_or_match"),
    SetupEffect("user_unit", "synapse-arm@.service", "install_or_match"),
)
_PROFILES: dict[SetupProfileId, SetupProfile] = {
    "local-ephemeral": SetupProfile(
        "local-ephemeral",
        1,
        (SetupEffect("runtime_directory", "synapse-user-runtime", "create_if_absent"),)
        + _USER_UNITS,
    ),
    "local-durable-existing": SetupProfile(
        "local-durable-existing",
        1,
        (
            SetupEffect("runtime_directory", "synapse-user-runtime", "create_if_absent"),
            SetupEffect("durable_store", "configured-existing-store", "verify_existing"),
        )
        + _USER_UNITS,
    ),
}


def available_setup_profiles() -> tuple[SetupProfileId, ...]:
    """Return the stable profile order without exposing the mutable registry."""
    return tuple(_PROFILES)


def evaluate_setup_posture(posture: SetupPosture) -> SetupPostureDecision:
    """Apply startup gates in non-disclosing, fail-closed order."""
    if not posture.feature_armed:
        return SetupPostureDecision(False, "unarmed")
    if not posture.loopback_bind:
        return SetupPostureDecision(False, "non_loopback")
    if posture.compatibility_access:
        return SetupPostureDecision(False, "compatibility_access")
    if not posture.versioned_access_file:
        return SetupPostureDecision(False, "access_file_required")
    if not posture.receipt_store_ready:
        return SetupPostureDecision(False, "receipt_store_unavailable")
    return SetupPostureDecision(True, "ready")


def parse_setup_plan_request(body: bytes) -> SetupPlanRequest | SetupContractError:
    """Parse an exact plan request without reflecting attacker-controlled text."""
    document = _strict_document(body)
    if isinstance(document, SetupContractError):
        return document
    if frozenset(document) != _PLAN_FIELDS:
        return SetupContractError("invalid_fields")
    if not _valid_version(document["version"]):
        return SetupContractError("invalid_version")
    request_id = document["request_id"]
    if not _valid_request_id(request_id):
        return SetupContractError("invalid_request_id")
    profile = document["profile"]
    if not isinstance(profile, str) or profile not in _PROFILES:
        return SetupContractError("unknown_profile")
    return SetupPlanRequest(cast(str, request_id), profile)


def parse_setup_apply_request(body: bytes) -> SetupApplyRequest | SetupContractError:
    """Parse an exact apply confirmation; confirmation must be literal true."""
    document = _strict_document(body)
    if isinstance(document, SetupContractError):
        return document
    if frozenset(document) != _APPLY_FIELDS:
        return SetupContractError("invalid_fields")
    if not _valid_version(document["version"]):
        return SetupContractError("invalid_version")
    request_id = document["request_id"]
    if not _valid_request_id(request_id):
        return SetupContractError("invalid_request_id")
    plan_id = document["plan_id"]
    if not isinstance(plan_id, str) or _OPAQUE_ID.fullmatch(plan_id) is None:
        return SetupContractError("invalid_plan_id")
    plan_digest = document["plan_digest"]
    if not isinstance(plan_digest, str) or _LOWER_SHA256.fullmatch(plan_digest) is None:
        return SetupContractError("invalid_plan_digest")
    nonce = document["confirmation_nonce"]
    if not isinstance(nonce, str) or _OPAQUE_ID.fullmatch(nonce) is None:
        return SetupContractError("invalid_confirmation_nonce")
    if document["confirm"] is not True:
        return SetupContractError("confirmation_required")
    return SetupApplyRequest(cast(str, request_id), plan_id, plan_digest, nonce)


def build_setup_plan(
    request: SetupPlanRequest,
    *,
    configuration_generation: str,
    expires_at: int,
) -> SetupPlan:
    """Build one deterministic plan from server-owned values and profile data."""
    if _LOWER_SHA256.fullmatch(configuration_generation) is None:
        raise ValueError("configuration generation must be a lowercase SHA-256 digest")
    if isinstance(expires_at, bool) or not isinstance(expires_at, int) or expires_at <= 0:
        raise ValueError("plan expiry must be a positive integer timestamp")
    profile = _PROFILES[request.profile]
    return SetupPlan(
        request.request_id,
        profile.profile_id,
        profile.version,
        configuration_generation,
        expires_at,
        profile.effects,
    )


def _valid_version(value: object) -> bool:
    return type(value) is int and value == SETUP_CONTRACT_VERSION


def _valid_request_id(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = UUID(value)
    except ValueError:
        return False
    return parsed.version == 4 and str(parsed) == value


def _reject_constant(_value: str) -> NoReturn:
    raise ValueError("non-finite JSON number")


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("duplicate JSON key")
        document[key] = cast(object, value)
    return document


def _strict_document(body: bytes) -> dict[str, object] | SetupContractError:
    if not body or len(body) > MAX_SETUP_REQUEST_BYTES:
        return SetupContractError("body_size")
    try:
        text = body.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return SetupContractError("invalid_json")
    if not isinstance(value, dict):
        return SetupContractError("invalid_fields")
    return cast(dict[str, object], value)


__all__ = [
    "MAX_SETUP_REQUEST_BYTES",
    "SETUP_CONTRACT_VERSION",
    "SetupApplyRequest",
    "SetupContractError",
    "SetupEffect",
    "SetupPlan",
    "SetupPlanRequest",
    "SetupProfileId",
    "SetupPosture",
    "SetupPostureDecision",
    "available_setup_profiles",
    "build_setup_plan",
    "evaluate_setup_posture",
    "parse_setup_apply_request",
    "parse_setup_plan_request",
]
