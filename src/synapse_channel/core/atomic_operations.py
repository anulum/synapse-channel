# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — canonical identities and records for atomic keyed mutations
"""Narrow value objects and canonical hashing for durable keyed operations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from synapse_channel.core.protocol import SENDER_HUB

_DIGEST_EXCLUDED_FIELDS = frozenset({"timestamp", "client_timestamp", "auth", "signature"})


def canonical_request_digest(data: Mapping[str, Any]) -> str:
    """Return the strict SHA-256 digest of one authenticated semantic request."""
    semantic = {key: value for key, value in data.items() if key not in _DIGEST_EXCLUDED_FIELDS}
    encoded = json.dumps(
        semantic,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class OperationRecord:
    """One completed operation cached in memory or loaded from durable storage."""

    key: str
    request_digest: str | None
    response: dict[str, Any]


@dataclass(frozen=True)
class OperationDraft:
    """Complete material needed to commit one winning operation."""

    response: dict[str, Any]
    events: tuple[tuple[str, Mapping[str, Any]], ...]
    intent: Mapping[str, Any]
    response_event_seq_field: str | None = None


@dataclass(frozen=True)
class AtomicExecution:
    """Outcome returned by the serialized operation actor."""

    outcome: Literal["inserted", "replayed", "conflict", "uncommitted"]
    mutation: Any
    response: dict[str, Any] | None


def idempotency_conflict_response(
    *, sender: str, reference: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Return a stable value-free response for changed-payload key reuse."""
    response: dict[str, Any] = {
        "sender": SENDER_HUB,
        "target": sender,
        "type": "error",
        "payload": "Idempotency key was already used for a different request.",
        "error_code": "idempotency_conflict",
    }
    if reference is not None:
        for field in ("timestamp", "hub_id"):
            if field in reference:
                response[field] = reference[field]
    return response
