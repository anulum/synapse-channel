# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — parent-bound recovery admission
"""Commit and reconstruct recovery lineage in the existing authority journal."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import TYPE_CHECKING

from synapse_channel.core.persistence import EventStore, StoredEvent
from synapse_channel.core.protected_write_admission import ProtectedAdmissionContext
from synapse_channel.core.protected_write_admission_journal import ProtectedAdmissionReplayPolicy
from synapse_channel.core.protected_write_journal import protected_operation_response
from synapse_channel.core.protected_write_lifecycle import ProtectedWriteReservation
from synapse_channel.core.protected_write_recovery import bind_protected_recovery
from synapse_channel.core.protected_write_request import (
    parse_protected_write_request,
    protected_write_operation_key,
)

if TYPE_CHECKING:
    from synapse_channel.core.state import SynapseState

RECOVERY_EVENT_KIND = "protected_write_recovery"
_SCHEMA = "synapse-protected-write.recovery-journal.v1"
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
    "predecessor_sequence",
    "predecessor_result_sha256",
}


def protected_recovery_event_payload(
    parent: ProtectedWriteReservation,
    request: str | bytes,
    *,
    context: ProtectedAdmissionContext,
    policy: ProtectedAdmissionReplayPolicy,
) -> dict[str, object]:
    """Bind internal recovery metadata without authorizing or mutating state.

    Parameters
    ----------
    parent:
        Exact current predecessor.
    request:
        Original authenticated service request.
    context:
        Server-resolved identities and time; sequence is assigned at commit.
    policy:
        Explicit retained enrollment policy.

    Returns
    -------
    dict[str, object]
        Closed predecessor-bound journal event payload.

    Raises
    ------
    ValueError
        If the request is malformed or is not recovery.
    """
    source = json.loads(
        parse_protected_write_request(request, limits=policy.limits).canonical_bytes
    )
    if source["type"] != "protected_write_recover":
        raise ValueError("recovery journal requires recover")
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
        "predecessor_sequence": parent.transition_sequence,
        "predecessor_result_sha256": hashlib.sha256(parent.result_bytes).hexdigest(),
    }


def finalize_protected_recovery(
    response: dict[str, object],
    sequences: tuple[int, ...],
    *,
    candidate: SynapseState,
    parent: ProtectedWriteReservation,
    request: str | bytes,
    context: ProtectedAdmissionContext,
    policy: ProtectedAdmissionReplayPolicy,
    sign_response: Callable[[dict[str, object]], Mapping[str, object]],
) -> Mapping[str, object]:
    """Sign a sequenced child and transfer custody inside the private candidate.

    Parameters
    ----------
    response:
        Private journal response draft.
    sequences:
        Actual mutation sequences; exactly one recovery event.
    candidate:
        Actor-private state, published only after successful commit.
    parent:
        Exact predecessor verified before commit.
    request:
        Original authenticated recovery request.
    context:
        Server context; the journal supplies the admission sequence.
    policy:
        Explicit enrollment and immutable recovery-evidence verifier.
    sign_response:
        Trusted local signer, with no I/O or live-state mutation.

    Returns
    -------
    Mapping[str, object]
        Signed response stored with the same atomic operation.

    Raises
    ------
    ValueError
        On malformed sequence/body, absent verifier or failed binding/transfer.
    """
    body = response.get("body")
    if len(sequences) != 1 or not isinstance(body, dict):
        raise ValueError("recovery requires one event and a response body")
    if policy.verify_recovery is None:
        raise ValueError("recovery evidence policy is unavailable")
    body["admission_sequence"] = sequences[0]
    signed = dict(sign_response(response))
    recovery = bind_protected_recovery(
        parent,
        request,
        json.dumps(signed, allow_nan=False),
        context=replace(context, admission_sequence=sequences[0]),
        claims=candidate.claims,
        limits=policy.limits,
        reason_codes=policy.reason_codes,
        verify_recovery=policy.verify_recovery,
    )
    candidate.transfer_protected_write_custody(recovery, max_reservations=policy.max_reservations)
    return signed


def restore_protected_recovery(
    event: StoredEvent,
    *,
    store: EventStore,
    state: SynapseState,
    policies: Mapping[str, ProtectedAdmissionReplayPolicy],
    through_seq: int | None = None,
) -> None:
    """Restore the exact child, lineage and whole domain from complete commit proof.

    Parameters
    ----------
    event:
        Recovery event from the authoritative journal.
    store:
        Same operation journal.
    state:
        Private replay candidate with historical parent and service claims.
    policies:
        Retained enrollment policies with local immutable-evidence verifiers.
    through_seq:
        Optional complete historical prefix.

    Raises
    ------
    ValueError
        If metadata, policy, parent, commit evidence or recovery binding is invalid.
    """
    payload = event.payload
    if event.kind != RECOVERY_EVENT_KIND or set(payload) != _FIELDS:
        raise ValueError("unsupported recovery event fields")
    if payload["schema_version"] != _SCHEMA:
        raise ValueError("unsupported recovery event schema")
    if type(payload["predecessor_sequence"]) is not int:
        raise ValueError("invalid recovery predecessor sequence")
    for field in _FIELDS - {"predecessor_sequence", "admitted_at"}:
        if not isinstance(payload[field], str):
            raise ValueError("invalid recovery event metadata")
    policy = policies.get(payload["enrollment_revision"])
    if policy is None or policy.verify_recovery is None:
        raise ValueError("recovery evidence policy is unavailable")
    raw = payload["request"]
    parsed = parse_protected_write_request(raw, limits=policy.limits)
    source = json.loads(parsed.canonical_bytes)
    if (
        source["type"] != "protected_write_recover"
        or source["enrollment_revision"] != payload["enrollment_revision"]
    ):
        raise ValueError("recovery event request binding mismatch")
    parent = state.protected_write_reservations.get(source["body"]["parent_reservation_id"])
    if (
        parent is None
        or parent.transition_sequence != payload["predecessor_sequence"]
        or hashlib.sha256(parent.result_bytes).hexdigest() != payload["predecessor_result_sha256"]
    ):
        raise ValueError("recovery event predecessor mismatch")
    context = ProtectedAdmissionContext(
        payload["author_principal"],
        payload["authority_id"],
        payload["authority_continuity"],
        payload["writer_principal"],
        payload["writer_incarnation"],
        event.seq,
        payload["admitted_at"],
    )
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
    recovery = bind_protected_recovery(
        parent,
        raw,
        response,
        context=context,
        claims=state.claims,
        limits=policy.limits,
        reason_codes=policy.reason_codes,
        verify_recovery=policy.verify_recovery,
    )
    state.transfer_protected_write_custody(recovery, max_reservations=policy.max_reservations)
