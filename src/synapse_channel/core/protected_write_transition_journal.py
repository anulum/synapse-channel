# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable reservation transitions
"""Finalize and restore exact predecessor-bound reservation transitions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Literal, cast

from synapse_channel.core.persistence import EventStore, StoredEvent
from synapse_channel.core.protected_write_admission_journal import ProtectedAdmissionReplayPolicy
from synapse_channel.core.protected_write_journal import protected_operation_response
from synapse_channel.core.protected_write_lifecycle import (
    ProtectedTransitionContext,
    ProtectedWriteReservation,
    advance_protected_write_reservation,
)
from synapse_channel.core.protected_write_request import (
    parse_protected_write_request,
    protected_write_operation_key,
)

if TYPE_CHECKING:
    from synapse_channel.core.state import SynapseState

TRANSITION_EVENT_KIND = "protected_write_transition"
_SCHEMA = "synapse-protected-write.transition-journal.v1"
_FIELDS = {
    "schema_version",
    "request",
    "enrollment_revision",
    "principal",
    "session_id",
    "role",
    "predecessor_sequence",
    "predecessor_result_sha256",
}


def protected_transition_event_payload(
    previous: ProtectedWriteReservation,
    request: str | bytes,
    *,
    context: ProtectedTransitionContext,
    policy: ProtectedAdmissionReplayPolicy,
) -> dict[str, object]:
    """Build closed internal metadata for a transition's existing journal commit.

    Parameters
    ----------
    previous:
        Exact immutable predecessor.
    request:
        Original authenticated transition request.
    context:
        Server-resolved principal/session/role; sequence comes from the journal.
    policy:
        Explicit enrollment policy.

    Returns
    -------
    dict[str, object]
        Metadata binding the event to its predecessor bytes.

    Raises
    ------
    ValueError
        On a malformed request or a non-transition verb.
    """
    parsed = parse_protected_write_request(request, limits=policy.limits)
    source = json.loads(parsed.canonical_bytes)
    if source["type"] not in (
        "protected_write_begin",
        "protected_write_settle",
        "protected_write_revoke",
    ):
        raise ValueError("journal requires a reservation transition")
    return {
        "schema_version": _SCHEMA,
        "request": request.decode("utf-8") if isinstance(request, bytes) else request,
        "enrollment_revision": source["enrollment_revision"],
        "principal": context.principal,
        "session_id": context.session_id,
        "role": context.role,
        "predecessor_sequence": previous.transition_sequence,
        "predecessor_result_sha256": hashlib.sha256(previous.result_bytes).hexdigest(),
    }


def finalize_protected_transition(
    response: dict[str, object],
    sequences: tuple[int, ...],
    *,
    candidate: SynapseState,
    previous: ProtectedWriteReservation,
    request: str | bytes,
    context: ProtectedTransitionContext,
    policy: ProtectedAdmissionReplayPolicy,
    sign_response: Callable[[dict[str, object]], Mapping[str, object]],
) -> Mapping[str, object]:
    """Bind actual sequences, sign and CAS a transition in the private candidate.

    Parameters
    ----------
    response:
        Private draft provided by EventStore.
    sequences:
        Actual mutation sequences; one transition event is required.
    candidate:
        Private actor-owned candidate, published only after commit.
    previous:
        Exact predecessor used by the controller.
    request:
        Original authenticated transition request.
    context:
        Server context; its sequence is replaced with the actual event sequence.
    policy:
        Enrollment and local retained-evidence verifier.
    sign_response:
        Trusted local signer without external I/O or live-state mutation.

    Returns
    -------
    Mapping[str, object]
        Signed response persisted with the state transition.

    Raises
    ------
    ValueError
        On malformed shape, identity, evidence or state transition.
    """
    if len(sequences) != 1:
        raise ValueError("transition requires exactly one mutation event")
    parsed = parse_protected_write_request(request, limits=policy.limits)
    verb = json.loads(parsed.canonical_bytes)["type"]
    body = response.get("body")
    if not isinstance(body, dict):
        raise ValueError("transition response body must be an object")
    old = json.loads(previous.result_bytes)["body"]
    seq = sequences[0]
    if verb == "protected_write_begin":
        body["begin_sequence"] = seq
    elif verb == "protected_write_settle":
        body["settlement_sequence"] = seq
        if old["revocation_phase"] == "requested" and body.get("outcome") in (
            "committed",
            "no_write",
        ):
            body["revocation_sequence"] = seq
    elif verb == "protected_write_revoke":
        if old["operation_phase"] == "admitted":
            body["settlement_sequence"] = seq
        if old["revocation_phase"] != "requested":
            body["revocation_sequence"] = seq
    else:
        raise ValueError("unsupported transition finalizer verb")
    signed = dict(sign_response(response))
    updated = advance_protected_write_reservation(
        previous,
        request,
        json.dumps(signed, allow_nan=False),
        context=replace(context, event_sequence=seq),
        limits=policy.limits,
        reason_codes=policy.reason_codes,
        verify_quiescence=policy.verify_quiescence,
    )
    candidate.apply_protected_write_transition(previous, updated)
    return signed


def restore_protected_transition(
    event: StoredEvent,
    *,
    store: EventStore,
    state: SynapseState,
    policies: Mapping[str, ProtectedAdmissionReplayPolicy],
    through_seq: int | None = None,
) -> None:
    """Restore a transition from complete commit proof and retained local evidence.

    Parameters
    ----------
    event:
        Protected transition event from the journal.
    store:
        The same authoritative operation journal.
    state:
        Replay candidate with the exact predecessor already reconstructed.
    policies:
        Retained enrollment policies and local evidence verifiers.
    through_seq:
        Optional complete historical prefix.

    Raises
    ------
    ValueError
        On any missing, unsupported or inconsistent replay binding.
    """
    payload = event.payload
    if event.kind != TRANSITION_EVENT_KIND or set(payload) != _FIELDS:
        raise ValueError("unsupported protected transition event fields")
    if payload["schema_version"] != _SCHEMA:
        raise ValueError("unsupported protected transition event schema")
    for field in _FIELDS - {"predecessor_sequence"}:
        if not isinstance(payload[field], str):
            raise ValueError("invalid protected transition metadata")
    if type(payload["predecessor_sequence"]) is not int:
        raise ValueError("invalid protected predecessor sequence")
    policy = policies.get(payload["enrollment_revision"])
    if policy is None:
        raise ValueError("protected transition enrollment policy is unavailable")
    raw = cast(str, payload["request"])
    parsed = parse_protected_write_request(raw, limits=policy.limits)
    source = json.loads(parsed.canonical_bytes)
    if source["enrollment_revision"] != payload["enrollment_revision"]:
        raise ValueError("transition enrollment binding mismatch")
    previous = state.protected_write_reservations.get(source["body"].get("reservation_id"))
    if (
        previous is None
        or previous.transition_sequence != payload["predecessor_sequence"]
        or hashlib.sha256(previous.result_bytes).hexdigest() != payload["predecessor_result_sha256"]
    ):
        raise ValueError("protected transition predecessor mismatch")
    origin = json.loads(previous.admission.request_bytes)
    key = protected_write_operation_key(
        raw,
        limits=policy.limits,
        authenticated_principal=payload["principal"],
        authority_id=origin["authority_id"],
        authority_continuity=origin["authority_continuity"],
    )
    response = protected_operation_response(
        store,
        operation_key=key,
        request_digest=parsed.request_digest,
        mutation_sequence=event.seq,
        through_seq=through_seq,
    )
    context = ProtectedTransitionContext(
        payload["principal"],
        payload["session_id"],
        cast(Literal["author", "writer", "operator"], payload["role"]),
        event.seq,
    )
    updated = advance_protected_write_reservation(
        previous,
        raw,
        response,
        context=context,
        limits=policy.limits,
        reason_codes=policy.reason_codes,
        verify_quiescence=policy.verify_quiescence,
    )
    state.apply_protected_write_transition(previous, updated)
