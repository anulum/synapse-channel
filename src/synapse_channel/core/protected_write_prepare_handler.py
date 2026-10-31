# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — ordered protected-write proposal preflight
"""Check an authenticated proposal against current policy without reserving it."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import cast

from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.protected_write_effects import verify_protected_effects
from synapse_channel.core.protected_write_preparation import ProtectedPreparationPolicy
from synapse_channel.core.protected_write_request import ParsedProtectedWriteRequest
from synapse_channel.core.protected_write_result import parse_protected_write_result
from synapse_channel.core.protected_write_session_auth import (
    AuthenticatedProtectedRequest,
    ProtectedSessionEnrollment,
    recheck_authenticated_protected_request,
)
from synapse_channel.core.protected_write_transition_response import PROTECTED_RESULT_STATE_FIELDS
from synapse_channel.core.state import SynapseState


class ProtectedWritePrepareHandler:
    """Return an actor-ordered policy snapshot for one authenticated proposal.

    A successful result does not reserve custody, authenticate a writer, inspect
    files or permit a filesystem effect. Admission repeats current checks inside
    its own durable actor transaction.

    Parameters
    ----------
    hub:
        Durable authority with a complete retained journal.
    policy:
        Operator-pinned effect enrollment and representation bounds.
    current_enrollments:
        Session registry updated under the same authority actor.
    clock:
        Trusted server clock.
    sign_response:
        I/O-free authority signer.
    reason_codes:
        Immutable enrolled result vocabulary.
    """

    def __init__(
        self,
        *,
        hub: SynapseHub,
        policy: ProtectedPreparationPolicy,
        current_enrollments: Callable[[], Mapping[str, ProtectedSessionEnrollment]],
        clock: Callable[[], float],
        sign_response: Callable[[dict[str, object]], Mapping[str, object]],
        reason_codes: frozenset[str],
    ) -> None:
        """Retain the durable hub and exact policy for signed preparation replies.

        The caller owns the enrollment lookup, clock and signer; no proposal is
        prepared during construction. Raise ValueError if the hub has no journal.
        Each later request rechecks journal completeness and current enrollment
        inside the authority actor before producing its signed allowance.
        """
        if hub.journal is None:
            raise ValueError("preparation requires a durable authority")
        self._hub = hub
        self._policy = policy
        self._current_enrollments = current_enrollments
        self._clock = clock
        self._sign_response = sign_response
        self._reason_codes = reason_codes

    async def __call__(self, authenticated: AuthenticatedProtectedRequest) -> bytes:
        """Sign a current policy decision without creating journal custody.

        Parameters
        ----------
        authenticated:
            Server-retained ingress result, never a caller-supplied capability.

        Returns
        -------
        bytes
            Signed and request-bound ``allowed`` result with no reservation.

        Raises
        ------
        ValueError
            If session, claim, policy, effect or journal state is unavailable.
        """
        parsed = authenticated.parsed
        source = cast(dict[str, object], json.loads(parsed.canonical_bytes))
        if source["type"] != "protected_write_prepare" or parsed.proposal is None:
            raise ValueError("preparation requires a protected proposal")
        if source["enrollment_revision"] != self._policy.enrollment_revision:
            raise ValueError("preparation effect enrollment revision mismatch")
        proposal_bytes = parsed.proposal.canonical_bytes

        def read(state: SynapseState) -> bytes:
            """Recheck enrollment, effect scope and live author claims under the actor."""
            if self._hub.journal is None or self._hub.journal_corrupt_rows:
                raise ValueError("preparation authority journal unavailable or incomplete")
            now = float(self._clock())
            recheck_authenticated_protected_request(
                authenticated,
                enrollments=self._current_enrollments(),
                authenticated_principal=authenticated.enrollment.principal,
                now=now,
            )
            verify_protected_effects(
                proposal_bytes,
                limits=self._policy.limits,
                primary_claims=self._policy.primary_claims,
                auxiliary_opcodes=self._policy.auxiliary_opcodes,
                enrolled_parents=self._policy.enrolled_parents,
            )
            proposal = cast(dict[str, object], json.loads(proposal_bytes))
            for witness in cast(list[dict[str, object]], proposal["claims"]):
                claim = state.claims.get(cast(str, witness["task_id"]))
                if (
                    claim is None
                    or claim.owner != authenticated.enrollment.principal
                    or claim.owner != witness["owner"]
                    or claim.epoch != witness["epoch"]
                    or claim.version != witness["version"]
                    or claim.lease_expires_at != witness["lease_expires_at"]
                    or claim.lease_expires_at <= now
                ):
                    raise ValueError(
                        "preparation claim witness is missing, stale or not author-owned"
                    )
            return self._signed_allowance(source, parsed, now)

        return await self._hub.state_mutations.run(self._hub.state, read)

    def _signed_allowance(
        self, source: dict[str, object], parsed: ParsedProtectedWriteRequest, now: float
    ) -> bytes:
        """Sign a request-bound policy snapshot while the authority lock is held."""
        body: dict[str, object] = dict.fromkeys(PROTECTED_RESULT_STATE_FIELDS)
        body.update(
            request_type=source["type"],
            request_digest=parsed.request_digest,
            disposition="allowed",
        )
        response = {
            name: value for name, value in source.items() if name not in {"auth", "signature"}
        }
        response.update(
            type="protected_write_result",
            sender=source["target"],
            target=source["sender"],
            timestamp=now,
            body=body,
        )
        signed = self._sign_response(response)
        result = parse_protected_write_result(
            json.dumps(dict(signed), allow_nan=False),
            request=parsed.canonical_bytes,
            limits=self._policy.limits,
            reason_codes=self._reason_codes,
        )
        return result.canonical_bytes
