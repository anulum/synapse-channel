# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — actor-ordered protected begin verification
"""Recheck one authenticated begin and its durable reservation under the actor."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import cast

from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.protected_write_lifecycle import ProtectedWriteReservation
from synapse_channel.core.protected_write_preparation import (
    PreparedProtectedWrite,
    verify_prepared_begin,
)
from synapse_channel.core.protected_write_session_auth import (
    AuthenticatedProtectedRequest,
    ProtectedSessionEnrollment,
    recheck_authenticated_protected_request,
)
from synapse_channel.core.state import SynapseState


class ProtectedWriteCurrent:
    """Read a current execution permit under the same actor as revocation.

    A successful read is valid only at its actor position. The isolated writer
    must retain descriptors and recheck before each effect; this class neither
    executes an effect nor grants a reusable capability.

    Parameters
    ----------
    hub:
        Durable authority whose actor orders begin checks and revocation.
    current_enrollments:
        Current session registry retained by that authority.
    clock:
        Trusted server clock used to recheck session freshness.
    authorize_current:
        Trusted, I/O-free check of the prepared proposal and current reservation.
    """

    def __init__(
        self,
        *,
        hub: SynapseHub,
        current_enrollments: Callable[[], Mapping[str, ProtectedSessionEnrollment]],
        clock: Callable[[], float],
        authorize_current: Callable[[PreparedProtectedWrite, ProtectedWriteReservation], bool],
    ) -> None:
        """Bind current-begin checks to a complete durable authority.

        The caller retains the hub, enrollment lookup, clock and authorization
        callback. Construction performs no begin or enrollment mutation. Raise
        ValueError when the journal is missing or has corrupt replay rows.
        """
        if hub.journal is None or hub.journal_corrupt_rows:
            raise ValueError("current begin requires a complete durable journal")
        self._hub = hub
        self._enrollments = current_enrollments
        self._clock = clock
        self._authorize_current = authorize_current

    async def __call__(
        self,
        prepared: PreparedProtectedWrite,
        authenticated_begin: AuthenticatedProtectedRequest,
    ) -> ProtectedWriteReservation:
        """Return the exact begun reservation after an ordered current check.

        Parameters
        ----------
        prepared:
            Retained proposal, admission and descriptors selected for this execution.
        authenticated_begin:
            Actual ingress-authenticated begin bound to its enrolled writer incarnation.

        Returns
        -------
        ProtectedWriteReservation
            Exact currently executing reservation at this actor position.

        Raises
        ------
        ValueError
            If begin identity, enrollment, journal, custody or current policy differ.
        """
        request = cast(dict[str, object], json.loads(authenticated_begin.parsed.canonical_bytes))
        if request["type"] != "protected_write_begin":
            raise ValueError("current execution requires an authenticated begin")
        body = cast(dict[str, object], request["body"])
        if body["reservation_id"] != prepared.admission.reservation_id:
            raise ValueError("current begin reservation differs from prepared input")

        def read(state: SynapseState) -> ProtectedWriteReservation:
            """Recheck enrollment and prepared-begin custody under the authority actor."""
            journal = self._hub.journal
            if journal is None or self._hub.journal_corrupt_rows:
                raise ValueError("current begin authority journal unavailable")
            recheck_authenticated_protected_request(
                authenticated_begin,
                enrollments=self._enrollments(),
                authenticated_principal=authenticated_begin.enrollment.principal,
                now=self._clock(),
            )
            incarnation = authenticated_begin.enrollment.writer_incarnation
            return verify_prepared_begin(
                prepared,
                state=state,
                store=journal,
                authenticated_writer=(
                    authenticated_begin.enrollment.principal,
                    cast(str, incarnation),
                ),
                authorize_current=self._authorize_current,
            )

        return await self._hub.state_mutations.run(self._hub.state, read)
