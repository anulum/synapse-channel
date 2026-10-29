# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — authenticated protected recovery transfer
"""Transfer uncertain protected custody to a distinct recovery service."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from functools import partial
from typing import cast

from synapse_channel.core.atomic_operations import OperationDraft, OperationRecord
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.protected_write_admission import ProtectedAdmissionContext
from synapse_channel.core.protected_write_admission_journal import ProtectedAdmissionReplayPolicy
from synapse_channel.core.protected_write_effects import verify_protected_effects
from synapse_channel.core.protected_write_lifecycle import ProtectedWriteReservation
from synapse_channel.core.protected_write_preparation import ProtectedPreparationPolicy
from synapse_channel.core.protected_write_recovery_journal import (
    RECOVERY_EVENT_KIND,
    finalize_protected_recovery,
    protected_recovery_event_payload,
)
from synapse_channel.core.protected_write_request import protected_write_operation_key
from synapse_channel.core.protected_write_result import parse_protected_write_result
from synapse_channel.core.protected_write_session_auth import (
    AuthenticatedProtectedRequest,
    ProtectedSessionEnrollment,
)
from synapse_channel.core.state import SynapseState

_RecoveryMutation = tuple[SynapseState, ProtectedWriteReservation, ProtectedAdmissionContext]


class ProtectedWriteRecoverHandler:
    """Commit parent-bound recovery through the sole durable authority actor.

    Parameters
    ----------
    hub:
        Durable authority with complete retained admission/transition history.
    policy:
        Retained replay policy including a real immutable recovery-evidence
        verifier. The verifier must refuse unknown bytes and old-writer activity.
    effects:
        Current operator-enrolled scope for the child proposal.
    current_enrollments:
        Actor-ordered current service and writer session registry.
    recovery_principals:
        Independently enrolled service principals permitted to recover.
    writer:
        New, distinct writer principal and incarnation selected by the operator.
    clock:
        Trusted server time.
    sign_response:
        I/O-free trusted authority signer.

    Notes
    -----
    This handler transfers journal custody, not physical execution authority.
    The supervised writer must separately obtain a fresh begin and recheck the
    actor before each effect. A verifier supplied by a test is not deployment
    proof of quiescence or filesystem custody.
    """

    def __init__(
        self,
        *,
        hub: SynapseHub,
        policy: ProtectedAdmissionReplayPolicy,
        effects: ProtectedPreparationPolicy,
        current_enrollments: Callable[[], Mapping[str, ProtectedSessionEnrollment]],
        recovery_principals: frozenset[str],
        writer: tuple[str, str],
        clock: Callable[[], float],
        sign_response: Callable[[dict[str, object]], Mapping[str, object]],
    ) -> None:
        if hub.journal is None:
            raise ValueError("recovery requires a durable authority")
        if policy.verify_recovery is None:
            raise ValueError("recovery requires a retained evidence verifier")
        if effects.limits != policy.limits:
            raise ValueError("recovery effect and replay limits differ")
        if type(recovery_principals) is not frozenset or not recovery_principals:
            raise ValueError("recovery requires explicit service principals")
        if (
            type(writer) is not tuple
            or len(writer) != 2
            or any(type(value) is not str or not value for value in writer)
            or writer[0] in recovery_principals
        ):
            raise ValueError("recovery requires a distinct enrolled writer")
        self._hub = hub
        self._policy = policy
        self._effects = effects
        self._enrollments = current_enrollments
        self._services = recovery_principals
        self._writer = writer
        self._clock = clock
        self._sign = sign_response

    async def __call__(self, authenticated: AuthenticatedProtectedRequest) -> bytes:
        """Commit or replay a signed recovery transfer for exact parent custody.

        Parameters
        ----------
        authenticated:
            Server-retained ingress result, never deserialized from service IPC.

        Returns
        -------
        bytes
            Signed response bound to this recovery request and journal event.

        Raises
        ------
        ValueError
            On untrusted service, stale parent, absent writer, invalid effects,
            journal damage, missing evidence or custody-transfer conflict.
        """
        parsed = authenticated.parsed
        source = cast(dict[str, object], json.loads(parsed.canonical_bytes))
        if source["type"] != "protected_write_recover" or parsed.proposal is None:
            raise ValueError("recovery handler requires a recover proposal")
        if source["enrollment_revision"] != self._effects.enrollment_revision:
            raise ValueError("recovery effect enrollment revision mismatch")
        principal = authenticated.enrollment.principal
        if principal not in self._services:
            raise ValueError("recovery requires an enrolled service principal")
        proposal_bytes = parsed.proposal.canonical_bytes
        operation_key = protected_write_operation_key(
            parsed.canonical_bytes,
            limits=self._policy.limits,
            authenticated_principal=principal,
            authority_id=authenticated.enrollment.authority_id,
            authority_continuity=authenticated.enrollment.authority_continuity,
        )
        reservation_id = "reservation-" + hashlib.sha256(operation_key.encode()).hexdigest()
        execution = await self._hub.run_authenticated_protected_write_operation(
            authenticated,
            partial(
                self._mutate,
                authenticated=authenticated,
                source=source,
                proposal_bytes=proposal_bytes,
            ),
            partial(
                self._draft,
                authenticated=authenticated,
                source=source,
                reservation_id=reservation_id,
            ),
            limits=self._policy.limits,
            current_enrollments=self._current_enrollments,
            current_principal=lambda: principal,
            clock=self._clock,
            conflict=partial(
                self._conflict,
                authenticated=authenticated,
                source=source,
                reservation_id=reservation_id,
            ),
        )
        return parse_protected_write_result(
            json.dumps(execution.response, allow_nan=False),
            request=parsed.canonical_bytes,
            limits=self._policy.limits,
            reason_codes=self._policy.reason_codes,
        ).canonical_bytes

    def _require_journal(self) -> None:
        """Require the retained authority journal at each ordered recovery boundary."""
        if self._hub.journal is None or self._hub.journal_corrupt_rows:
            raise ValueError("recovery authority journal unavailable or incomplete")

    def _current_enrollments(self) -> Mapping[str, ProtectedSessionEnrollment]:
        """Read current registry only while the retained authority journal is available."""
        self._require_journal()
        return self._enrollments()

    def _response(
        self,
        source: dict[str, object],
        *,
        request_digest: str,
        reservation_id: str,
        disposition: str,
        now: float,
    ) -> dict[str, object]:
        """Build an unsigned empty conflict or new admitted recovery reservation."""
        result: dict[str, object] = dict.fromkeys(
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
        result.update(
            request_type=source["type"],
            request_digest=request_digest,
            disposition=disposition,
        )
        if disposition == "accepted":
            result.update(
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
            body=result,
        )
        return envelope

    def _mutate(
        self,
        candidate: SynapseState,
        *,
        authenticated: AuthenticatedProtectedRequest,
        source: dict[str, object],
        proposal_bytes: bytes,
    ) -> _RecoveryMutation:
        """Check current parent custody, child effects and enrolled recovery writer."""
        # Repeat the journal check at mutation time after actor authorization.
        self._require_journal()
        body = cast(dict[str, object], source["body"])
        parent_id = cast(str, body["parent_reservation_id"])
        parent = candidate.protected_write_reservations.get(parent_id)
        # The recovery map also enforces the state transfer's one-recovery invariant.
        if (
            parent is None
            or parent_id in candidate.protected_write_recoveries
            or not parent.holds_custody
            or json.loads(parent.result_bytes)["body"]["operation_phase"] != "recovery_required"
        ):
            raise ValueError("recovery predecessor is unavailable")
        verify_protected_effects(
            proposal_bytes,
            limits=self._effects.limits,
            primary_claims=self._effects.primary_claims,
            auxiliary_opcodes=self._effects.auxiliary_opcodes,
            enrolled_parents=self._effects.enrolled_parents,
        )
        now = float(self._clock())
        if not any(
            _recovery_writer_matches(grant, source, self._writer, now)
            for grant in self._enrollments().values()
        ):
            raise ValueError("recovery writer incarnation is not currently enrolled")
        principal = authenticated.enrollment.principal
        context = ProtectedAdmissionContext(
            principal,
            authenticated.enrollment.authority_id,
            authenticated.enrollment.authority_continuity,
            self._writer[0],
            self._writer[1],
            0,
            now,
        )
        return candidate, parent, context

    def _draft(
        self,
        value: _RecoveryMutation,
        *,
        authenticated: AuthenticatedProtectedRequest,
        source: dict[str, object],
        reservation_id: str,
    ) -> OperationDraft:
        """Bind exact checked parent custody to its atomic transfer finalizer."""
        candidate, parent, context = value
        return OperationDraft(
            response=self._response(
                source,
                request_digest=authenticated.parsed.request_digest,
                reservation_id=reservation_id,
                disposition="accepted",
                now=context.now,
            ),
            events=(
                (
                    RECOVERY_EVENT_KIND,
                    protected_recovery_event_payload(
                        parent,
                        authenticated.parsed.canonical_bytes,
                        context=context,
                        policy=self._policy,
                    ),
                ),
            ),
            intent={"family": "protected-recovery"},
            finalize_response=lambda draft, sequences: finalize_protected_recovery(
                draft,
                sequences,
                candidate=candidate,
                parent=parent,
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
        reservation_id: str,
    ) -> dict[str, object]:
        """Sign a request-bound identity conflict without transferring custody."""
        response = self._response(
            source,
            request_digest=authenticated.parsed.request_digest,
            reservation_id=reservation_id,
            disposition="conflict",
            now=float(self._clock()),
        )
        return dict(self._sign(response))


def _recovery_writer_matches(
    grant: ProtectedSessionEnrollment,
    source: Mapping[str, object],
    writer: tuple[str, str],
    now: float,
) -> bool:
    """Match the exact live writer scope before creating a recovery admission context."""
    return (
        grant.principal == writer[0]
        and grant.writer_incarnation == writer[1]
        and (grant.target == source["target"])
        and (grant.authority_id == source["authority_id"])
        and (grant.authority_continuity == source["authority_continuity"])
        and (grant.enrollment_revision == source["enrollment_revision"])
        and (grant.transaction_id == source["transaction_id"])
        and (grant.proposal_sha256 == source["proposal_sha256"])
        and ({"begin", "settle"} <= grant.verbs)
        and (not grant.revoked)
        and (not grant.key.revoked)
        and (now < grant.expires_at)
    )
