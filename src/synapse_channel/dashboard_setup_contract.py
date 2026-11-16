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

The dashboard serves the command line's setup profile, and a dashboard plan is
bound to one command-line plan by that plan's digest: what a person confirms is
what the command line's executor would apply. The dashboard plan keeps the
command-line effects' decision fields and leaves out everything the inspection
measured, so it holds no path, hub address or identity.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Final, Literal, NoReturn, cast, get_args
from uuid import UUID

from synapse_channel.setup_contract import (
    SetupEffectAuthority,
    SetupEffectDisposition,
    SetupEffectDisruption,
    document_digest,
)
from synapse_channel.setup_planner import setup_effect_ids
from synapse_channel.setup_profiles import SetupProfileId, available_setup_profiles

MAX_SETUP_REQUEST_BYTES = 4096
SETUP_CONTRACT_VERSION = 1

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
_DISPOSITIONS: Final = frozenset(get_args(SetupEffectDisposition))
_AUTHORITIES: Final = frozenset(get_args(SetupEffectAuthority))
_DISRUPTIONS: Final = frozenset(get_args(SetupEffectDisruption))
_RESTART_AUTHORITY: Final = "operator_restart_authority"
_MAX_PROCESS_ID: Final = 2_147_483_647


@dataclass(frozen=True)
class SetupContractRefusal:
    """One stable, non-reflective setup request refusal."""

    code: SetupErrorCode


@dataclass(frozen=True)
class SetupPlanEffect:
    """One command-line effect as a person confirms it: what it does, never where.

    Attributes
    ----------
    effect_id : str
        The command line's effect id.
    disposition, authority, disruption : str
        The command line's decision fields for the effect.
    reversible : bool
        Whether the command line can undo the effect.
    process_id : int or None
        The running hub's process id when the effect restarts it, else ``None``.
    """

    effect_id: str
    disposition: SetupEffectDisposition
    authority: SetupEffectAuthority
    disruption: SetupEffectDisruption
    reversible: bool
    process_id: int | None

    def as_dict(self) -> dict[str, object]:
        """Return the canonical projection, keyed as the command line keys its effects."""
        return {
            "id": self.effect_id,
            "disposition": self.disposition,
            "authority": self.authority,
            "disruption": self.disruption,
            "reversible": self.reversible,
            "process_id": self.process_id,
        }


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
    """Canonical effect plan; it contains neither a secret nor an executable.

    ``configuration_generation`` is the digest of the command-line plan's
    generation, whose executable paths stay on the server, and
    ``cli_plan_digest`` is that plan's own digest; both are covered by
    :meth:`canonical_bytes`.
    """

    request_id: str
    profile: SetupProfileId
    profile_version: int
    configuration_generation: str
    cli_plan_digest: str
    expires_at: int
    effects: tuple[SetupPlanEffect, ...]

    def as_dict(self) -> dict[str, object]:
        """Return the stable digest input."""
        return {
            "version": SETUP_CONTRACT_VERSION,
            "request_id": self.request_id,
            "profile": self.profile,
            "profile_version": self.profile_version,
            "configuration_generation": self.configuration_generation,
            "cli_plan_digest": self.cli_plan_digest,
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


def parse_setup_plan_request(body: bytes) -> SetupPlanRequest | SetupContractRefusal:
    """Parse an exact plan request without reflecting attacker-controlled text."""
    document = _strict_document(body)
    if isinstance(document, SetupContractRefusal):
        return document
    if frozenset(document) != _PLAN_FIELDS:
        return SetupContractRefusal("invalid_fields")
    if not _valid_version(document["version"]):
        return SetupContractRefusal("invalid_version")
    request_id = document["request_id"]
    if not _valid_request_id(request_id):
        return SetupContractRefusal("invalid_request_id")
    profile = document["profile"]
    if not isinstance(profile, str) or profile not in available_setup_profiles():
        return SetupContractRefusal("unknown_profile")
    return SetupPlanRequest(cast(str, request_id), profile)


def parse_setup_apply_request(body: bytes) -> SetupApplyRequest | SetupContractRefusal:
    """Parse an exact apply confirmation; confirmation must be literal true."""
    document = _strict_document(body)
    if isinstance(document, SetupContractRefusal):
        return document
    if frozenset(document) != _APPLY_FIELDS:
        return SetupContractRefusal("invalid_fields")
    if not _valid_version(document["version"]):
        return SetupContractRefusal("invalid_version")
    request_id = document["request_id"]
    if not _valid_request_id(request_id):
        return SetupContractRefusal("invalid_request_id")
    plan_id = document["plan_id"]
    if not isinstance(plan_id, str) or _OPAQUE_ID.fullmatch(plan_id) is None:
        return SetupContractRefusal("invalid_plan_id")
    plan_digest = document["plan_digest"]
    if not isinstance(plan_digest, str) or _LOWER_SHA256.fullmatch(plan_digest) is None:
        return SetupContractRefusal("invalid_plan_digest")
    nonce = document["confirmation_nonce"]
    if not isinstance(nonce, str) or _OPAQUE_ID.fullmatch(nonce) is None:
        return SetupContractRefusal("invalid_confirmation_nonce")
    if document["confirm"] is not True:
        return SetupContractRefusal("confirmation_required")
    return SetupApplyRequest(cast(str, request_id), plan_id, plan_digest, nonce)


def setup_generation_digest(cli_plan: Mapping[str, object]) -> str:
    """Return the digest of a command-line plan's generation, which never leaves the server.

    Raises
    ------
    ValueError
        If the plan carries no generation object.
    """
    generation = cli_plan.get("generation")
    if not isinstance(generation, dict):
        raise ValueError("the command-line plan has no generation")
    return document_digest(cast(dict[str, object], generation))


def build_setup_plan(
    request: SetupPlanRequest,
    *,
    cli_plan: Mapping[str, object],
    expires_at: int,
) -> SetupPlan:
    """Build one deterministic dashboard plan bound to one command-line plan.

    Parameters
    ----------
    request : SetupPlanRequest
        The browser's validated request.
    cli_plan : Mapping
        The document :func:`synapse_channel.setup_planner.build_setup_plan`
        returned for the request's profile.
    expires_at : int
        The plan's expiry, in seconds since the epoch.

    Raises
    ------
    ValueError
        If the expiry is not a positive integer, or the command-line plan is not
        of the request's profile, does not match its own digest, or names an
        effect outside the command line's vocabulary.
    """
    if isinstance(expires_at, bool) or not isinstance(expires_at, int) or expires_at <= 0:
        raise ValueError("plan expiry must be a positive integer timestamp")
    if cli_plan.get("profile") != request.profile:
        raise ValueError("the command-line plan is not of the requested profile")
    profile_version = cli_plan.get("profile_version")
    if type(profile_version) is not int or profile_version < 1:
        raise ValueError("the command-line plan's profile version is invalid")
    effects = cli_plan.get("effects")
    if not isinstance(effects, list):
        raise ValueError("the command-line plan has no effect list")
    return SetupPlan(
        request.request_id,
        request.profile,
        profile_version,
        setup_generation_digest(cli_plan),
        _verified_cli_digest(cli_plan),
        expires_at,
        tuple(_plan_effect(effect) for effect in effects),
    )


def _verified_cli_digest(cli_plan: Mapping[str, object]) -> str:
    """Return the command-line plan's digest after recomputing it from the plan."""
    digest = cli_plan.get("plan_digest")
    unsigned = {key: value for key, value in cli_plan.items() if key != "plan_digest"}
    if not isinstance(digest, str) or digest != document_digest(unsigned):
        raise ValueError("the command-line plan does not match its digest")
    return digest


def _plan_effect(value: object) -> SetupPlanEffect:
    """Return one command-line effect's decision fields; refuse any outside the vocabulary."""
    if not isinstance(value, Mapping):
        raise ValueError("a command-line plan effect is not an object")
    words = (value.get("id"), value.get("disposition"), value.get("authority"))
    effect_id, disposition, authority = (word if isinstance(word, str) else "" for word in words)
    disruption = value.get("disruption")
    reversible = value.get("reversible")
    process_id = value.get("process_id")
    restart = process_id is not None
    if (
        effect_id not in setup_effect_ids()
        or disposition not in _DISPOSITIONS
        or authority not in _AUTHORITIES
        or not isinstance(disruption, str)
        or disruption not in _DISRUPTIONS
        or not isinstance(reversible, bool)
        or restart != (authority == _RESTART_AUTHORITY)
        or (restart and not _valid_process_id(process_id))
    ):
        raise ValueError("a command-line plan effect is outside the setup vocabulary")
    return SetupPlanEffect(
        effect_id=effect_id,
        disposition=cast(SetupEffectDisposition, disposition),
        authority=cast(SetupEffectAuthority, authority),
        disruption=cast(SetupEffectDisruption, disruption),
        reversible=reversible,
        process_id=cast("int | None", process_id),
    )


def _valid_process_id(value: object) -> bool:
    return type(value) is int and 1 < value <= _MAX_PROCESS_ID


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


def _strict_document(body: bytes) -> dict[str, object] | SetupContractRefusal:
    if not body or len(body) > MAX_SETUP_REQUEST_BYTES:
        return SetupContractRefusal("body_size")
    try:
        text = body.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return SetupContractRefusal("invalid_json")
    if not isinstance(value, dict):
        return SetupContractRefusal("invalid_fields")
    return cast(dict[str, object], value)


__all__ = [
    "MAX_SETUP_REQUEST_BYTES",
    "SETUP_CONTRACT_VERSION",
    "SetupApplyRequest",
    "SetupContractRefusal",
    "SetupPlan",
    "SetupPlanEffect",
    "SetupPlanRequest",
    "SetupProfileId",
    "SetupPosture",
    "SetupPostureDecision",
    "build_setup_plan",
    "evaluate_setup_posture",
    "parse_setup_apply_request",
    "parse_setup_plan_request",
    "setup_generation_digest",
]
