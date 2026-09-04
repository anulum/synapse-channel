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
from importlib.resources import files
from typing import Literal, cast

SETUP_SCHEMA_VERSION = "synapse-setup.v1"
SETUP_SCHEMA_RESOURCE = "schemas/synapse-setup-v1.schema.json"

SetupCheckStatus = Literal["pass", "warn", "fail", "unavailable"]
SetupErrorCode = Literal["unknown_profile", "invalid_uri", "inspection_failed"]


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


def canonical_json(document: dict[str, object]) -> str:
    """Serialize a setup document deterministically on one line."""
    return json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def setup_schema() -> dict[str, object]:
    """Load the packaged JSON Schema for setup documents."""
    resource = files("synapse_channel").joinpath("schemas").joinpath("synapse-setup-v1.schema.json")
    return cast(dict[str, object], json.loads(resource.read_text(encoding="utf-8")))


def setup_error_document(
    *,
    command: Literal["spec", "inspect"],
    profile: str,
    code: SetupErrorCode,
) -> dict[str, object]:
    """Return a bounded error document without reflecting arbitrary details."""
    messages = {
        "unknown_profile": "The requested setup profile is not supported.",
        "invalid_uri": "The hub URI must be a credential-free ws:// or wss:// endpoint.",
        "inspection_failed": "The environment could not be inspected safely.",
    }
    return {
        "schema_version": SETUP_SCHEMA_VERSION,
        "document_kind": "error",
        "command": command,
        "profile": profile,
        "code": code,
        "message": messages[code],
    }
