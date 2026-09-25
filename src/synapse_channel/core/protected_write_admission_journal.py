# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — admission reconstruction from the existing operation journal
"""Restore held custody only from a complete, policy-supported atomic operation."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from synapse_channel.core.persistence import EventStore, StoredEvent
from synapse_channel.core.protected_write_admission import (
    ProtectedAdmissionContext,
    ProtectedWriteAdmission,
    bind_protected_write_admission,
)
from synapse_channel.core.protected_write_journal import protected_operation_response
from synapse_channel.core.protected_write_proposal import ProtectedWriteProposalLimits
from synapse_channel.core.protected_write_request import (
    parse_protected_write_request,
    protected_write_operation_key,
)

if TYPE_CHECKING:
    from synapse_channel.core.protected_write_lifecycle import ProtectedWriteReservation
    from synapse_channel.core.state import SynapseState

ADMISSION_EVENT_KIND = "protected_write_admission"
_SCHEMA = "synapse-protected-write.admission-journal.v1"
_FIELDS = {
    "schema_version",
    "request",
    "enrollment_revision",
    "author_principal",
    "authority_id",
    "authority_continuity",
    "writer_principal",
    "writer_incarnation",
    "admitted_at",
}


@dataclass(frozen=True)
class ProtectedAdmissionReplayPolicy:
    """Trusted retained policy for one exact historical enrollment revision."""

    limits: ProtectedWriteProposalLimits
    reason_codes: frozenset[str]
    max_reservations: int
    verify_quiescence: Callable[[ProtectedWriteAdmission, bytes, bytes], bool] | None = None
    verify_recovery: Callable[[ProtectedWriteReservation, ProtectedWriteAdmission], bool] | None = (
        None
    )


def protected_admission_event_payload(
    request: str | bytes,
    *,
    context: ProtectedAdmissionContext,
    limits: ProtectedWriteProposalLimits,
) -> dict[str, object]:
    """Build internal journal metadata; no signed response or state is fabricated.

    Parameters
    ----------
    request:
        Original admit request already authenticated by the controller.
    context:
        Server-resolved admission context; sequence comes from the journal later.
    limits:
        Explicit enrollment representation limits.

    Returns
    -------
    dict[str, object]
        Closed internal payload to commit with the finalized operation response.

    Raises
    ------
    ValueError
        On malformed request or a verb other than ordinary admit.
    """
    parsed = parse_protected_write_request(request, limits=limits)
    source = json.loads(parsed.canonical_bytes)
    if source["type"] != "protected_write_admit":
        raise ValueError("admission journal requires ordinary admit")
    return {
        "schema_version": _SCHEMA,
        "request": request.decode("utf-8") if isinstance(request, bytes) else request,
        "enrollment_revision": source["enrollment_revision"],
        "author_principal": context.author_principal,
        "authority_id": context.authority_id,
        "authority_continuity": context.authority_continuity,
        "writer_principal": context.writer_principal,
        "writer_incarnation": context.writer_incarnation,
        "admitted_at": context.now,
    }


def restore_protected_admission(
    event: StoredEvent,
    *,
    store: EventStore,
    state: SynapseState,
    policies: Mapping[str, ProtectedAdmissionReplayPolicy],
    through_seq: int | None = None,
) -> None:
    """Restore metadata and custody before ordinary claim lease expiration.

    Parameters
    ----------
    event:
        Admission event from the authoritative journal.
    store:
        The same journal containing its completed operation and commit marker.
    state:
        Replay candidate containing the historical claim prefix.
    policies:
        Server-retained policies keyed by exact enrollment revision.
    through_seq:
        Optional historical prefix; cannot cut through an atomic admission.

    Raises
    ------
    ValueError
        On unsupported schema/policy, incomplete operation or inconsistent binding.

    Notes
    -----
    Journal custody/integrity is a prerequisite. This does not reauthenticate an
    expired socket or grant a new writer incarnation authority to execute.
    """
    payload = event.payload
    if event.kind != ADMISSION_EVENT_KIND or set(payload) != _FIELDS:
        raise ValueError("unsupported protected admission event fields")
    if payload["schema_version"] != _SCHEMA:
        raise ValueError("unsupported protected admission event schema")
    for field in _FIELDS - {"admitted_at"}:
        if not isinstance(payload[field], str):
            raise ValueError("invalid protected admission metadata")
    policy = policies.get(cast(str, payload["enrollment_revision"]))
    if policy is None:
        raise ValueError("protected admission enrollment policy is unavailable")
    context = ProtectedAdmissionContext(
        cast(str, payload["author_principal"]),
        cast(str, payload["authority_id"]),
        cast(str, payload["authority_continuity"]),
        cast(str, payload["writer_principal"]),
        cast(str, payload["writer_incarnation"]),
        event.seq,
        cast(float, payload["admitted_at"]),
    )
    raw = cast(str, payload["request"])
    parsed = parse_protected_write_request(raw, limits=policy.limits)
    source = json.loads(parsed.canonical_bytes)
    if source["enrollment_revision"] != payload["enrollment_revision"]:
        raise ValueError("protected admission enrollment binding mismatch")
    key = protected_write_operation_key(
        raw,
        limits=policy.limits,
        authenticated_principal=context.author_principal,
        authority_id=context.authority_id,
        authority_continuity=context.authority_continuity,
    )
    response = protected_operation_response(
        store,
        operation_key=key,
        request_digest=parsed.request_digest,
        mutation_sequence=event.seq,
        through_seq=through_seq,
    )
    admission = bind_protected_write_admission(
        raw,
        response,
        context=context,
        claims=state.claims,
        limits=policy.limits,
        reason_codes=policy.reason_codes,
    )
    state.install_protected_write_admission(admission, max_reservations=policy.max_reservations)
