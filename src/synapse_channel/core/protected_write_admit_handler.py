# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — authenticated durable admission handler
"""Admit exact protected custody; never dispatch a writer or fabricate settlement."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from typing import cast

from synapse_channel.core.atomic_operations import OperationDraft, OperationRecord
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.protected_write_admission import (
    ProtectedAdmissionContext,
    finalize_protected_write_admission,
)
from synapse_channel.core.protected_write_admission_journal import (
    ADMISSION_EVENT_KIND,
    protected_admission_event_payload,
)
from synapse_channel.core.protected_write_effects import verify_protected_effects
from synapse_channel.core.protected_write_preparation import ProtectedPreparationPolicy
from synapse_channel.core.protected_write_request import protected_write_operation_key
from synapse_channel.core.protected_write_result import parse_protected_write_result
from synapse_channel.core.protected_write_session_auth import (
    AuthenticatedProtectedRequest,
    ProtectedSessionEnrollment,
)
from synapse_channel.core.state import SynapseState


class ProtectedWriteAdmitHandler:
    """Commit admission using exact operator effect policy and current claims.

    Parameters
    ----------
    hub:
        Durable authority with protected replay policy configured.
    policy:
        Trusted immutable preparation/effect enrollment, not client maps.
    current_enrollments:
        Current session registry updated through the authority actor.
    writer:
        Operator-pinned writer principal and incarnation, not an author field.
    clock:
        Trusted server clock.
    sign_response:
        I/O-free authority signer retained by the trusted service.
    reason_codes:
        Enrolled response vocabulary.
    max_reservations:
        Explicit retained custody bound.
    """

    def __init__(
        self,
        *,
        hub: SynapseHub,
        policy: ProtectedPreparationPolicy,
        current_enrollments: Callable[[], Mapping[str, ProtectedSessionEnrollment]],
        writer: tuple[str, str],
        clock: Callable[[], float],
        sign_response: Callable[[dict[str, object]], Mapping[str, object]],
        reason_codes: frozenset[str],
        max_reservations: int,
    ) -> None:
        if hub.journal is None:
            raise ValueError("admission requires a durable authority")
        if type(max_reservations) is not int or max_reservations <= 0:
            raise ValueError("admission requires an explicit reservation budget")
        self._hub, self._policy = hub, policy
        self._enrollments, self._writer = current_enrollments, writer
        self._clock, self._sign = clock, sign_response
        self._codes, self._maximum = reason_codes, max_reservations

    async def __call__(self, authenticated: AuthenticatedProtectedRequest) -> bytes:
        """Commit or replay an authenticated admission without executing effects.

        Parameters
        ----------
        authenticated:
            Actual retained ingress result; never accept a client-supplied DTO.

        Returns
        -------
        bytes
            Signed admitted or conflict response bound to the exact request.

        Raises
        ------
        ValueError
            On policy, claim, journal, freshness, overlap or response mismatch.
        """
        parsed = authenticated.parsed
        source = cast(dict[str, object], json.loads(parsed.canonical_bytes))
        if source["type"] != "protected_write_admit" or parsed.proposal is None:
            raise ValueError("ordinary admission handler requires an admit proposal")
        if source["enrollment_revision"] != self._policy.enrollment_revision:
            raise ValueError("admission effect enrollment revision mismatch")
        proposal = parsed.proposal
        operation_key = protected_write_operation_key(
            parsed.canonical_bytes,
            limits=self._policy.limits,
            authenticated_principal=authenticated.enrollment.principal,
            authority_id=authenticated.enrollment.authority_id,
            authority_continuity=authenticated.enrollment.authority_continuity,
        )
        reservation_id = "reservation-" + hashlib.sha256(operation_key.encode()).hexdigest()

        def current() -> Mapping[str, ProtectedSessionEnrollment]:
            if self._hub.journal is None or self._hub.journal_corrupt_rows:
                raise ValueError("admission authority journal unavailable or incomplete")
            return self._enrollments()

        def reply(disposition: str, now: float) -> dict[str, object]:
            body: dict[str, object] = dict.fromkeys(
                (
                    "reservation_id",
                    "operation_phase",
                    "revocation_phase",
                    "outcome",
                    "writer_principal",
                    "writer_incarnation",
                    "admission_sequence",
                    "begin_sequence",
                    "settlement_sequence",
                    "revocation_sequence",
                    "evidence_reference",
                    "reason_code",
                )
            )
            body.update(
                request_type=source["type"],
                request_digest=parsed.request_digest,
                disposition=disposition,
            )
            if disposition == "accepted":
                body.update(
                    reservation_id=reservation_id,
                    operation_phase="admitted",
                    revocation_phase="open",
                    writer_principal=self._writer[0],
                    writer_incarnation=self._writer[1],
                )
            envelope = {
                name: value for name, value in source.items() if name not in {"auth", "signature"}
            }
            envelope.update(
                type="protected_write_result",
                sender=source["target"],
                target=source["sender"],
                timestamp=now,
                body=body,
            )
            return envelope

        def mutate(candidate: SynapseState) -> tuple[SynapseState, ProtectedAdmissionContext]:
            verify_protected_effects(
                proposal.canonical_bytes,
                limits=self._policy.limits,
                primary_claims=self._policy.primary_claims,
                auxiliary_opcodes=self._policy.auxiliary_opcodes,
                enrolled_parents=self._policy.enrolled_parents,
            )
            context = ProtectedAdmissionContext(
                authenticated.enrollment.principal,
                authenticated.enrollment.authority_id,
                authenticated.enrollment.authority_continuity,
                self._writer[0],
                self._writer[1],
                0,
                float(self._clock()),
            )
            return candidate, context

        def prepare(value: tuple[SynapseState, ProtectedAdmissionContext]) -> OperationDraft:
            candidate, context = value
            return OperationDraft(
                response=reply("accepted", context.now),
                events=(
                    (
                        ADMISSION_EVENT_KIND,
                        protected_admission_event_payload(
                            parsed.canonical_bytes,
                            context=context,
                            limits=self._policy.limits,
                        ),
                    ),
                ),
                intent={"family": "protected-admission"},
                finalize_response=lambda draft, sequences: finalize_protected_write_admission(
                    draft,
                    sequences,
                    candidate=candidate,
                    request=parsed.canonical_bytes,
                    context=context,
                    limits=self._policy.limits,
                    reason_codes=self._codes,
                    max_reservations=self._maximum,
                    sign_response=self._sign,
                ),
            )

        def conflict(_existing: OperationRecord) -> dict[str, object]:
            return dict(self._sign(reply("conflict", float(self._clock()))))

        execution = await self._hub.run_authenticated_protected_write_operation(
            authenticated,
            mutate,
            prepare,
            limits=self._policy.limits,
            current_enrollments=current,
            current_principal=lambda: authenticated.enrollment.principal,
            clock=self._clock,
            conflict=conflict,
        )
        return parse_protected_write_result(
            json.dumps(execution.response, allow_nan=False),
            request=parsed.canonical_bytes,
            limits=self._policy.limits,
            reason_codes=self._codes,
        ).canonical_bytes
