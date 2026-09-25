# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — ordered protected reservation status
"""Read current authority reservation state without admitting or executing work."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import cast

from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.protected_write_proposal import ProtectedWriteProposalLimits
from synapse_channel.core.protected_write_result import parse_protected_write_result
from synapse_channel.core.protected_write_session_auth import (
    AuthenticatedProtectedRequest,
    ProtectedSessionEnrollment,
    recheck_authenticated_protected_request,
)
from synapse_channel.core.state import SynapseState

_BINDINGS = (
    "target",
    "authority_id",
    "authority_continuity",
    "transaction_id",
    "proposal_sha256",
    "enrollment_revision",
)
_STATE_FIELDS = (
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
)


class ProtectedWriteStatus:
    """Serve status from current actor-owned state, not caller-provided history.

    Parameters
    ----------
    hub:
        Durable authority hub with validated replay policy and state.
    limits:
        Enrolled wire bounds.
    current_enrollments:
        Current operator registry, updated under the same actor.
    clock:
        Trusted server clock.
    sign_response:
        Trusted I/O-free authority signer; clients independently verify replies.
    reason_codes:
        Enrolled result vocabulary.
    max_reservations:
        Explicit bound on state inspected for transaction-based lookup.
    """

    def __init__(
        self,
        *,
        hub: SynapseHub,
        limits: ProtectedWriteProposalLimits,
        current_enrollments: Callable[[], Mapping[str, ProtectedSessionEnrollment]],
        clock: Callable[[], float],
        sign_response: Callable[[dict[str, object]], Mapping[str, object]],
        reason_codes: frozenset[str],
        max_reservations: int,
    ) -> None:
        if hub.journal is None:
            raise ValueError("status requires a durable authority")
        if type(max_reservations) is not int or max_reservations <= 0:
            raise ValueError("status requires an explicit reservation bound")
        self._hub = hub
        self._limits = limits
        self._current_enrollments = current_enrollments
        self._clock = clock
        self._sign_response = sign_response
        self._reason_codes = reason_codes
        self._max_reservations = max_reservations

    async def __call__(self, authenticated: AuthenticatedProtectedRequest) -> bytes:
        """Read and sign one status snapshot under the authority mutation lock.

        Parameters
        ----------
        authenticated:
            Actual server-retained ingress result, never a supplied capability.

        Returns
        -------
        bytes
            Signed, request-bound known or unknown status; no activation receipt.

        Raises
        ------
        ValueError
            For stale authorization, foreign/ambiguous state or invalid response.
        """
        source = cast(dict[str, object], json.loads(authenticated.parsed.canonical_bytes))
        if source["type"] != "protected_write_status":
            raise ValueError("status handler cannot execute other operations")

        def read(state: SynapseState) -> bytes:
            if self._hub.journal is None or self._hub.journal_corrupt_rows:
                raise ValueError("status authority journal unavailable or incomplete")
            now = self._clock()
            recheck_authenticated_protected_request(
                authenticated,
                enrollments=self._current_enrollments(),
                authenticated_principal=authenticated.enrollment.principal,
                now=now,
            )
            reservations = state.protected_write_reservations
            if len(reservations) > self._max_reservations:
                raise ValueError("status reservation budget exceeded")
            requested = cast(dict[str, object], source["body"])["reservation_id"]
            matches: list[dict[str, object]] = []
            for reservation_id, reservation in reservations.items():
                if requested is not None and requested != reservation_id:
                    continue
                origin = json.loads(reservation.admission.request_bytes)
                if any(source[name] != origin[name] for name in _BINDINGS):
                    # Foreign and absent reservations have identical visibility.
                    continue
                historical = parse_protected_write_result(
                    reservation.result_bytes,
                    request=reservation.request_bytes,
                    limits=self._limits,
                    reason_codes=self._reason_codes,
                )
                previous = cast(dict[str, object], json.loads(historical.canonical_bytes)["body"])
                if previous["reservation_id"] != reservation_id:
                    raise ValueError("authority reservation identity inconsistent")
                matches.append(previous)
            if len(matches) > 1:
                raise ValueError("ambiguous transaction reservation")
            body: dict[str, object] = {name: None for name in _STATE_FIELDS}
            body.update(
                request_type=source["type"],
                request_digest=authenticated.parsed.request_digest,
                disposition="unknown",
                evidence_reference=None,
                reason_code=None,
            )
            if matches:
                body.update({name: matches[0][name] for name in _STATE_FIELDS})
                body.update(
                    disposition="known", evidence_reference=matches[0]["evidence_reference"]
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
                request=authenticated.parsed.canonical_bytes,
                limits=self._limits,
                reason_codes=self._reason_codes,
            )
            return result.canonical_bytes

        return await self._hub.state_mutations.run(self._hub.state, read)
