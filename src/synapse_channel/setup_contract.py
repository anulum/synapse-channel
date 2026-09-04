# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — versioned machine-readable host setup documents
"""Stable document primitives for read-only machine setup discovery.

The contract is intentionally narrower than the existing cockpit setup plan:
these documents describe an installed host and do not expose an apply effect.
The JSON Schema ships inside the wheel so an LLM agent can validate output
without a source checkout.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from importlib.resources import files
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
SetupCommand = Literal["spec", "inspect", "plan", "authorize"]
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
]

MAX_SETUP_URI_LENGTH = 2048
MAX_SETUP_PROJECT_LENGTH = 128
MAX_SETUP_IDENTITY_LENGTH = 256


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
        }


@dataclass(frozen=True, slots=True)
class SetupPlan:
    """Immutable non-executable plan bound to one inspection and profile."""

    profile: str
    profile_version: int
    inspection_digest: str
    profile_digest: str
    target: dict[str, str]
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
            "can_apply": False,
            "ready": self.ready,
            "inspection_digest": self.inspection_digest,
            "profile_digest": self.profile_digest,
            "target": dict(self.target),
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
    }
    return {
        "schema_version": SETUP_SCHEMA_VERSION,
        "document_kind": "error",
        "command": command,
        "profile": profile,
        "code": code,
        "message": messages[code],
    }
