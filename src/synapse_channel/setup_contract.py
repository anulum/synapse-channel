# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — versioned machine-readable host setup documents
"""Stable document primitives for machine-readable host setup.

Plans and authorizations remain inert JSON documents. Only the separately
invoked, allow-listed executor may consume one exact authorization. The JSON
Schema ships inside the wheel so an LLM agent can validate every document
without a source checkout.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from importlib.resources import files
from pathlib import Path
from typing import Literal, cast
from urllib.parse import urlsplit

SETUP_SCHEMA_VERSION = "synapse-setup.v1"
SETUP_SCHEMA_RESOURCE = "schemas/synapse-setup-v1.schema.json"

SetupCheckStatus = Literal["pass", "warn", "fail", "unavailable"]
SetupEffectDisposition = Literal["planned", "blocked"]
SetupEffectAuthority = Literal["operator_confirmation", "operator_restart_authority", "unsupported"]
SetupEffectDisruption = Literal[
    "configuration_change", "environment_change", "service_start", "host_migration"
]
SetupCommand = Literal[
    "spec",
    "inspect",
    "plan",
    "authorize",
    "apply",
    "verification-plan",
    "authorize-verification",
    "verify",
]
SetupErrorCode = Literal[
    "unknown_profile",
    "invalid_uri",
    "inspection_failed",
    "planning_failed",
    "invalid_plan",
    "digest_mismatch",
    "invalid_nonce",
    "invalid_expiry",
    "plan_blocked",
    "restart_authority_required",
    "unexpected_restart_authority",
    "authorization_failed",
    "invalid_authorization",
    "authorization_expired",
    "authorization_mismatch",
    "authorization_replayed",
    "authorization_ledger_unavailable",
    "authorization_transition_invalid",
    "application_target_changed",
    "application_platform_unsupported",
    "application_lock_unavailable",
    "application_protected_process_missing",
    "application_effect_failed",
    "application_recovery_failed",
    "application_receipt_unavailable",
    "invalid_application_receipt",
    "verification_planning_failed",
    "invalid_verification_plan",
    "verification_authorization_failed",
    "invalid_verification_authorization",
    "verification_authorization_replayed",
    "verification_ledger_unavailable",
    "verification_target_changed",
    "verification_lock_unavailable",
    "verification_protected_process_missing",
    "verification_canary_failed",
    "verification_restart_failed",
    "verification_replay_failed",
    "verification_receipt_unavailable",
]

MAX_SETUP_URI_LENGTH = 2048
MAX_SETUP_PROJECT_LENGTH = 128
MAX_SETUP_IDENTITY_LENGTH = 256
MAX_SETUP_PATH_LENGTH = 4096
MAX_SETUP_VERSION_LENGTH = 128
LOCAL_SINGLE_USER_URIS = frozenset({"ws://localhost:8876", "ws://127.0.0.1:8876"})


@dataclass(frozen=True, slots=True)
class SetupRequirement:
    """One profile requirement and the evidence used to assess it."""

    requirement_id: str
    description: str
    required: bool
    evidence_source: str
    remedy: str

    def as_dict(self) -> dict[str, object]:
        """Return the JSON-compatible requirement projection."""
        return {
            "id": self.requirement_id,
            "description": self.description,
            "required": self.required,
            "evidence_source": self.evidence_source,
            "remedy": self.remedy,
        }


@dataclass(frozen=True, slots=True)
class SetupCheck:
    """One observed setup fact with a stable verdict and remedy."""

    check_id: str
    status: SetupCheckStatus
    required: bool
    value: object
    detail: str
    remedy: str = ""

    def as_dict(self) -> dict[str, object]:
        """Return the JSON-compatible check projection."""
        return {
            "id": self.check_id,
            "status": self.status,
            "required": self.required,
            "value": self.value,
            "detail": self.detail,
            "remedy": self.remedy,
        }


@dataclass(frozen=True, slots=True)
class SetupPlannedEffect:
    """One allow-listed future effect proposed by a read-only plan."""

    effect_id: str
    trigger_check: str
    observed_status: SetupCheckStatus
    disposition: SetupEffectDisposition
    authority: SetupEffectAuthority
    disruption: SetupEffectDisruption
    reversible: bool
    verification_check: str
    process_id: int | None = None

    def as_dict(self) -> dict[str, object]:
        """Return the stable, value-free effect projection."""
        return {
            "id": self.effect_id,
            "trigger_check": self.trigger_check,
            "observed_status": self.observed_status,
            "disposition": self.disposition,
            "authority": self.authority,
            "disruption": self.disruption,
            "reversible": self.reversible,
            "verification_check": self.verification_check,
            "process_id": self.process_id,
        }


@dataclass(frozen=True, slots=True)
class SetupPlan:
    """Immutable non-executable plan bound to one inspection and profile."""

    profile: str
    profile_version: int
    inspection_digest: str
    profile_digest: str
    target: dict[str, str]
    generation: dict[str, str]
    ready: bool
    effects: tuple[SetupPlannedEffect, ...]
    warnings: tuple[str, ...]

    def unsigned_dict(self) -> dict[str, object]:
        """Return the canonical digest input, excluding its own digest."""
        authorities = list(
            dict.fromkeys(
                effect.authority
                for effect in self.effects
                if effect.disposition == "planned" and effect.authority != "unsupported"
            )
        )
        return {
            "schema_version": SETUP_SCHEMA_VERSION,
            "document_kind": "plan",
            "profile": self.profile,
            "profile_version": self.profile_version,
            "read_only": True,
            "can_apply": bool(self.effects)
            and all(effect.disposition == "planned" for effect in self.effects),
            "ready": self.ready,
            "inspection_digest": self.inspection_digest,
            "profile_digest": self.profile_digest,
            "target": dict(self.target),
            "generation": dict(self.generation),
            "authority_required": authorities,
            "effects": [effect.as_dict() for effect in self.effects],
            "warnings": list(self.warnings),
        }

    @property
    def digest(self) -> str:
        """Return the lowercase SHA-256 digest of the canonical plan input."""
        return document_digest(self.unsigned_dict())

    def as_dict(self) -> dict[str, object]:
        """Return the complete plan document including its digest."""
        document = self.unsigned_dict()
        document["plan_digest"] = self.digest
        return document


def canonical_json(document: dict[str, object]) -> str:
    """Serialize a setup document deterministically on one line."""
    return json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def document_digest(document: dict[str, object]) -> str:
    """Return the canonical lowercase SHA-256 digest of a setup document."""
    return sha256(canonical_json(document).encode("utf-8")).hexdigest()


def validated_setup_target(value: object) -> dict[str, str]:
    """Return a bounded credential-free target or reject it."""
    if not isinstance(value, dict) or set(value) != {"uri", "project", "identity"}:
        raise ValueError("setup target must contain exactly uri, project, and identity")
    uri = value.get("uri")
    project = value.get("project")
    identity = value.get("identity")
    if not isinstance(uri, str) or not 1 <= len(uri) <= MAX_SETUP_URI_LENGTH:
        raise ValueError("setup target URI is invalid")
    if not _bounded_plain_text(project, MAX_SETUP_PROJECT_LENGTH):
        raise ValueError("setup target project is invalid")
    if not _bounded_plain_text(identity, MAX_SETUP_IDENTITY_LENGTH):
        raise ValueError("setup target identity is invalid")
    try:
        parsed = urlsplit(uri)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("setup target URI is invalid") from exc
    if not (
        parsed.scheme in {"ws", "wss"}
        and parsed.hostname
        and parsed.username is None
        and parsed.password is None
        and parsed.query == ""
        and parsed.fragment == ""
        and (port is None or 0 < port <= 65535)
        and not any(character.isspace() or ord(character) < 32 for character in uri)
    ):
        raise ValueError("setup target URI is invalid")
    return {"uri": uri, "project": cast(str, project), "identity": cast(str, identity)}


def validated_setup_generation(value: object) -> dict[str, str]:
    """Return immutable executable and platform facts or reject them.

    The executor compares this plan-bound generation with a fresh inspection
    before reserving authority. Absolute executable paths prevent a changed
    ``PATH`` from redirecting either the generated unit or service-manager
    command after operator review.
    """
    expected = {
        "package_version",
        "python_executable",
        "synapse_executable",
        "platform_system",
        "service_manager_executable",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("setup generation has unexpected fields")
    generation: dict[str, str] = {}
    for key in expected:
        item = value.get(key)
        maximum = MAX_SETUP_VERSION_LENGTH if key == "package_version" else MAX_SETUP_PATH_LENGTH
        allow_absent = key in {"synapse_executable", "service_manager_executable"}
        if not isinstance(item, str) or len(item) > maximum or (not item and not allow_absent):
            raise ValueError("setup generation field is invalid")
        if any(ord(character) < 32 for character in item):
            raise ValueError("setup generation field is invalid")
        generation[key] = item
    for key in ("python_executable", "synapse_executable", "service_manager_executable"):
        if generation[key] and not Path(generation[key]).is_absolute():
            raise ValueError("setup generation executable is not absolute")
    return generation


def _bounded_plain_text(value: object, maximum: int) -> bool:
    return bool(
        isinstance(value, str)
        and 1 <= len(value) <= maximum
        and not any(character.isspace() or ord(character) < 32 for character in value)
    )


def setup_schema() -> dict[str, object]:
    """Load the packaged JSON Schema for setup documents."""
    resource = files("synapse_channel").joinpath("schemas").joinpath("synapse-setup-v1.schema.json")
    return cast(dict[str, object], json.loads(resource.read_text(encoding="utf-8")))


def setup_error_document(
    *,
    command: SetupCommand,
    profile: str,
    code: SetupErrorCode,
) -> dict[str, object]:
    """Return a bounded error document without reflecting arbitrary details."""
    messages = {
        "unknown_profile": "The requested setup profile is not supported.",
        "invalid_uri": "The hub URI must be a credential-free ws:// or wss:// endpoint.",
        "inspection_failed": "The environment could not be inspected safely.",
        "planning_failed": "A safe plan could not be derived from the inspection.",
        "invalid_plan": "The supplied setup plan is not a valid regular plan document.",
        "digest_mismatch": "The confirmed digest does not match the supplied setup plan.",
        "invalid_nonce": "The confirmation nonce does not satisfy the setup contract.",
        "invalid_expiry": "The authorization lifetime does not satisfy the setup contract.",
        "plan_blocked": "The setup plan contains a blocked effect and cannot be authorized.",
        "restart_authority_required": "The setup plan requires authority for one exact process ID.",
        "unexpected_restart_authority": "Restart authority would exceed the setup plan's scope.",
        "authorization_failed": "A safe setup authorization could not be produced.",
        "invalid_authorization": "The supplied setup authorization is not valid.",
        "authorization_expired": "The supplied setup authorization has expired.",
        "authorization_mismatch": "The setup authorization does not match its exact plan.",
        "authorization_replayed": "The setup authorization was already reserved for execution.",
        "authorization_ledger_unavailable": "The setup authorization ledger is unavailable.",
        "authorization_transition_invalid": "The setup authorization state transition is invalid.",
        "application_target_changed": "The inspected setup target changed after authorization.",
        "application_platform_unsupported": "This setup effect has no supported host adapter.",
        "application_lock_unavailable": "Another setup executor owns the host mutation lock.",
        "application_protected_process_missing": "A protected process is not alive.",
        "application_effect_failed": "An allow-listed setup effect failed.",
        "application_recovery_failed": "A failed setup effect could not be fully recovered.",
        "application_receipt_unavailable": "The setup receipt could not be written safely.",
        "invalid_application_receipt": "The application receipt is not a valid successful result.",
        "verification_planning_failed": "A strict verification plan could not be derived safely.",
        "invalid_verification_plan": "The strict verification plan is invalid.",
        "verification_authorization_failed": (
            "A strict verification authorization could not be produced."
        ),
        "invalid_verification_authorization": "The strict verification authorization is invalid.",
        "verification_authorization_replayed": (
            "The strict verification authorization was already reserved."
        ),
        "verification_ledger_unavailable": "The strict verification ledger is unavailable.",
        "verification_target_changed": "The strict verification target changed or is not ready.",
        "verification_lock_unavailable": (
            "Another strict verification transaction owns the host lock."
        ),
        "verification_protected_process_missing": "A protected process did not remain alive.",
        "verification_canary_failed": "Directed canary delivery and consumption were not proven.",
        "verification_restart_failed": "The exact authorised hub restart was not proven.",
        "verification_replay_failed": "The canary event was not proven unchanged after restart.",
        "verification_receipt_unavailable": (
            "The strict verification receipt could not be written safely."
        ),
    }
    return {
        "schema_version": SETUP_SCHEMA_VERSION,
        "document_kind": "error",
        "command": command,
        "profile": profile,
        "code": code,
        "message": messages[code],
    }
