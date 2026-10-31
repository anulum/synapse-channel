# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — authenticated reservation transition handler
"""Commit exact protected begin, settle and revoke through the authority actor."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from functools import partial
from typing import Literal, cast

from synapse_channel.core.atomic_operations import OperationDraft, OperationRecord
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.protected_write_admission_journal import ProtectedAdmissionReplayPolicy
from synapse_channel.core.protected_write_lifecycle import (
    ProtectedTransitionContext,
    ProtectedWriteReservation,
)
from synapse_channel.core.protected_write_result import parse_protected_write_result
from synapse_channel.core.protected_write_session_auth import (
    AuthenticatedProtectedRequest,
    ProtectedSessionEnrollment,
)
from synapse_channel.core.protected_write_transition_journal import (
    TRANSITION_EVENT_KIND,
    finalize_protected_transition,
    protected_transition_event_payload,
)
from synapse_channel.core.protected_write_transition_response import (
    make_protected_transition_response,
)
from synapse_channel.core.state import SynapseState

_TransitionMutation = tuple[
    SynapseState, ProtectedWriteReservation, ProtectedTransitionContext, float
]


class ProtectedWriteTransitionHandler:
    """Apply authenticated transitions without dispatching a writer.

    Parameters
    ----------
    hub:
        Durable authority using the same retained replay policy.
    policy:
        Exact historical enrollment and evidence-verification policy.
    enrollment_revision:
        Revision selected by the operator for this handler.
    current_enrollments:
        Current registry ordered with authority mutations.
    writer_principal:
        Pinned writer principal, separate from the author.
    operator_principals:
        Independently authenticated principals allowed to request revocation.
    clock:
        Trusted current server clock.
    sign_response:
        I/O-free authority signer for the durable response.

    Notes
    -----
    A prebegin revoke is effective because the authority has accepted no begin.
    Any later begin is refused by the durable predecessor transition. Runtime
    activation must prove that no writer can bypass this authority boundary.
    Known settlement requires ``policy.verify_quiescence`` to inspect real
    retained evidence; this handler does not invent that proof.
    """

    def __init__(
        self,
        *,
        hub: SynapseHub,
        policy: ProtectedAdmissionReplayPolicy,
        enrollment_revision: str,
        current_enrollments: Callable[[], Mapping[str, ProtectedSessionEnrollment]],
        writer_principal: str,
        operator_principals: frozenset[str],
        clock: Callable[[], float],
        sign_response: Callable[[dict[str, object]], Mapping[str, object]],
    ) -> None:
        """Bind transitions to durable policy and explicit enrolled principal roles.

        Retain the caller-owned hub, replay policy, enrollment lookup, trusted
        clock and response signer. Construction grants no transition. Raise
        ValueError for a missing durable journal, empty revision/writer principal,
        a mutable operator-principal collection or overlapping writer/operator roles.
        """
        if hub.journal is None:
            raise ValueError("transition requires a durable authority")
        if not enrollment_revision or not writer_principal:
            raise ValueError("transition requires exact enrollment and writer")
        if type(operator_principals) is not frozenset:
            raise ValueError("operator principals must be immutable")
        if writer_principal in operator_principals:
            raise ValueError("writer and operator principals must be separate")
        self._hub = hub
        self._policy = policy
        self._revision = enrollment_revision
        self._enrollments = current_enrollments
        self._writer = writer_principal
        self._operators = operator_principals
        self._clock = clock
        self._sign = sign_response

    async def __call__(self, authenticated: AuthenticatedProtectedRequest) -> bytes:
        """Commit or replay a checked transition through the durable authority actor.

        Parameters
        ----------
        authenticated:
            Server-retained parsed request and enrollment from real ingress.

        Returns
        -------
        bytes
            Signed request-bound result retained with its atomic transition commit.

        Raises
        ------
        ValueError
            On stale scope, unsupported verb, ineligible predecessor or caller role.

        Notes
        -----
        The transport principal is the immutable identity of this authenticated
        frame. Its callback does not re-observe a socket identity; the actor
        rechecks the current enrollment, key and scope against that retained
        binding before accepting the transition.
        """
        parsed = authenticated.parsed
        source = cast(dict[str, object], json.loads(parsed.canonical_bytes))
        verb = str(source["type"])
        if verb not in {
            "protected_write_begin",
            "protected_write_settle",
            "protected_write_revoke",
        }:
            raise ValueError("transition handler requires begin, settle or revoke")
        if source["enrollment_revision"] != self._revision:
            raise ValueError("transition enrollment revision mismatch")
        execution = await self._hub.run_authenticated_protected_write_operation(
            authenticated,
            partial(self._mutate, authenticated=authenticated, source=source),
            partial(self._draft, authenticated=authenticated, source=source),
            limits=self._policy.limits,
            current_enrollments=self._enrollments,
            current_principal=lambda: authenticated.enrollment.principal,
            clock=self._clock,
            conflict=partial(self._conflict, authenticated=authenticated, source=source),
        )
        return parse_protected_write_result(
            json.dumps(execution.response, allow_nan=False),
            request=parsed.canonical_bytes,
            limits=self._policy.limits,
            reason_codes=self._policy.reason_codes,
        ).canonical_bytes

    def _mutate(
        self,
        candidate: SynapseState,
        *,
        authenticated: AuthenticatedProtectedRequest,
        source: dict[str, object],
    ) -> _TransitionMutation:
        """Check current predecessor and caller role under the authority lock."""
        if self._hub.journal is None or self._hub.journal_corrupt_rows:
            raise ValueError("transition authority journal unavailable or incomplete")
        verb = str(source["type"])
        request_body = cast(dict[str, object], source["body"])
        reservation_id = cast(str, request_body["reservation_id"])
        previous = candidate.protected_write_reservations.get(reservation_id)
        if previous is None or reservation_id in candidate.protected_write_recoveries:
            raise ValueError("protected reservation is unavailable")
        old = cast(dict[str, object], json.loads(previous.result_bytes)["body"])
        origin = json.loads(previous.admission.request_bytes)
        principal = authenticated.enrollment.principal
        role: Literal["author", "writer", "operator"]
        if principal == self._writer and principal == old["writer_principal"]:
            role = "writer"
        elif principal == origin["sender"] and source["session_id"] == origin["session_id"]:
            role = "author"
        elif principal in self._operators:
            role = "operator"
        else:
            raise ValueError("transition principal is not enrolled for this reservation")
        if role == "operator" and verb != "protected_write_revoke":
            raise ValueError("operator may only revoke a reservation")
        if verb in {"protected_write_begin", "protected_write_settle"} and role != "writer":
            raise ValueError("only the enrolled writer may begin or settle")
        _check_transition_phase(verb, old, authenticated.enrollment.writer_incarnation)
        context = ProtectedTransitionContext(
            principal,
            authenticated.enrollment.session_id,
            role,
            0,
        )
        return candidate, previous, context, float(self._clock())

    def _draft(
        self,
        value: _TransitionMutation,
        *,
        authenticated: AuthenticatedProtectedRequest,
        source: dict[str, object],
    ) -> OperationDraft:
        """Bind a checked candidate to its durable event and response finalizer."""
        candidate, previous, context, now = value
        return OperationDraft(
            response=make_protected_transition_response(
                source,
                request_digest=authenticated.parsed.request_digest,
                disposition="accepted",
                now=now,
                previous=previous,
            ),
            events=(
                (
                    TRANSITION_EVENT_KIND,
                    protected_transition_event_payload(
                        previous,
                        authenticated.parsed.canonical_bytes,
                        context=context,
                        policy=self._policy,
                    ),
                ),
            ),
            intent={"family": "protected-transition"},
            finalize_response=lambda draft, sequences: finalize_protected_transition(
                draft,
                sequences,
                candidate=candidate,
                previous=previous,
                request=authenticated.parsed.canonical_bytes,
                context=context,
                policy=self._policy,
                sign_response=self._sign,
            ),
        )

    def _conflict(
        self,
        _existing: OperationRecord,
        *,
        authenticated: AuthenticatedProtectedRequest,
        source: dict[str, object],
    ) -> dict[str, object]:
        """Sign a bound conflict without publishing a second transition."""
        response = make_protected_transition_response(
            source,
            request_digest=authenticated.parsed.request_digest,
            disposition="conflict",
            now=float(self._clock()),
            previous=None,
        )
        return dict(self._sign(response))


def _check_transition_phase(
    verb: str, old: dict[str, object], writer_incarnation: str | None
) -> None:
    """Require eligible current phase and the exact admitted writer incarnation."""
    if verb == "protected_write_begin" and (
        old["operation_phase"] != "admitted" or old["revocation_phase"] != "open"
    ):
        raise ValueError("reservation cannot begin")
    if verb == "protected_write_settle" and old["operation_phase"] != "executing":
        raise ValueError("settlement requires the executing writer")
    if verb == "protected_write_revoke" and old["revocation_phase"] == "effective":
        raise ValueError("effective revocation is already terminal")
    if verb == "protected_write_revoke" and old["revocation_phase"] == "requested":
        raise ValueError("revocation is already requested")
    if verb in {"protected_write_begin", "protected_write_settle"} and (
        writer_incarnation != old["writer_incarnation"]
    ):
        raise ValueError("writer session does not own the admitted incarnation")
