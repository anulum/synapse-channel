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
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protected_write_journal import protected_operation_response
from synapse_channel.core.protected_write_lifecycle import ProtectedWriteReservation
from synapse_channel.core.protected_write_lineage import read_protected_lineage
from synapse_channel.core.protected_write_proposal import ProtectedWriteProposalLimits
from synapse_channel.core.protected_write_request import (
    parse_protected_write_request,
    protected_write_operation_key,
)
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

    Notes
    -----
    Visibility belongs to the enrolled transaction scope, shared by its authorized
    author, writer, operator and recovery participants with the status verb.
    It is not restricted to the original request sender. All authority, proposal
    and enrollment bindings must match; other transactions return unknown.
    A historical parent's status preserves its own outcome after custody transfers
    to a recovery descendant. Complete current lineage custody is still checked.
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
        """Bind signed status reads to a durable hub and positive reservation bound.

        Retain the caller-owned wire limits, current enrollment lookup, clock,
        signer and reason codes. Construction neither reads a remote authority nor
        mutates a reservation. Raise ValueError for a missing journal or a bound
        that is not a positive native integer.
        """
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
            """Authenticate current scope and sign its journal-backed status under the actor."""
            if self._hub.journal is None or self._hub.journal_corrupt_rows:
                raise ValueError("status authority journal unavailable or incomplete")
            now = self._clock()
            recheck_authenticated_protected_request(
                authenticated,
                enrollments=self._current_enrollments(),
                authenticated_principal=authenticated.enrollment.principal,
                now=now,
            )
            known = self._matching_reservation_body(state, source, self._hub.journal)
            return self._signed_status(source, authenticated, now, known)

        return await self._hub.state_mutations.run(self._hub.state, read)

    def _verified_reservation_body(
        self,
        state: SynapseState,
        reservation_id: str,
        reservation: ProtectedWriteReservation,
        journal: EventStore,
    ) -> dict[str, object]:
        """Verify one retained reservation against custody and its committed operation.

        The caller must hold the authority mutation lock throughout this read.
        """
        historical = parse_protected_write_result(
            reservation.result_bytes,
            request=reservation.request_bytes,
            limits=self._limits,
            reason_codes=self._reason_codes,
        )
        previous = cast(dict[str, object], json.loads(historical.canonical_bytes)["body"])
        if previous["reservation_id"] != reservation_id:
            raise ValueError("authority reservation identity inconsistent")
        if state.protected_write_admissions.get(reservation_id) != reservation.admission:
            raise ValueError("authority reservation admission inconsistent")
        read_protected_lineage(state, reservation_id, max_reservations=self._max_reservations)
        mutation = parse_protected_write_request(reservation.request_bytes, limits=self._limits)
        mutation_source = cast(dict[str, object], json.loads(mutation.canonical_bytes))
        operation_key = protected_write_operation_key(
            mutation.canonical_bytes,
            limits=self._limits,
            authenticated_principal=cast(str, mutation_source["sender"]),
            authority_id=cast(str, mutation_source["authority_id"]),
            authority_continuity=cast(str, mutation_source["authority_continuity"]),
        )
        if (
            protected_operation_response(
                journal,
                operation_key=operation_key,
                request_digest=mutation.request_digest,
                mutation_sequence=reservation.transition_sequence,
            ).encode("ascii")
            != reservation.result_bytes
        ):
            raise ValueError("authority reservation differs from durable operation")
        return previous

    def _matching_reservation_body(
        self,
        state: SynapseState,
        source: dict[str, object],
        journal: EventStore,
    ) -> dict[str, object] | None:
        """Select at most one visible reservation while holding the authority lock."""
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
            matches.append(
                self._verified_reservation_body(state, reservation_id, reservation, journal)
            )
        if len(matches) > 1:
            raise ValueError("ambiguous transaction reservation")
        return matches[0] if matches else None

    def _signed_status(
        self,
        source: dict[str, object],
        authenticated: AuthenticatedProtectedRequest,
        now: float,
        known: dict[str, object] | None,
    ) -> bytes:
        """Sign and reparse one request-bound snapshot inside the authority lock."""
        body: dict[str, object] = {name: None for name in _STATE_FIELDS}
        body.update(
            request_type=source["type"],
            request_digest=authenticated.parsed.request_digest,
            disposition="unknown",
            evidence_reference=None,
            reason_code=None,
        )
        if known is not None:
            body.update({name: known[name] for name in _STATE_FIELDS})
            body.update(disposition="known", evidence_reference=known["evidence_reference"])
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
