# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — forward a dead-letter blackhole signal to the peer hub that owns the target
"""Forward a dead-letter blackhole signal to the peer hub whose domain owns the target.

The dead-letter escalation (:mod:`synapse_channel.core.dead_letter_escalation`) tells *this*
hub's operators that a directed target is a growing blackhole. When that target's namespace is
owned by a **peer** hub — resolved through the same namespace-ownership and relay-route roster the
operator relay already uses — the peer is the side that can actually reach the missing reader, and
it has no way to learn of the gap on its own. This module builds the signal that tells it.

The signal is a **pointer, never a payload**. The dead-letter ledger holds only counts and names
(the message bodies live in the durable feed and are never carried cross-domain), so
:func:`forwarding_notice` can only ever name the blackholed target, how many of its directed
messages went undelivered here, and which hubs the origin and owner are. Re-delivery is therefore
impossible by construction, exactly as it is for the local escalation: forwarding points the owning
hub at a gap it owns, and never moves a message across a trust boundary.

The actual transmission is a seam (:class:`DeadLetterForwarder`) an origin hub calls, so a
deployment wires it to a real cross-hub transport while the resolution, the honesty bound, and the
durable origin-side audit are exercised without a live peer.
"""

from __future__ import annotations

from typing import Any, Protocol


class DeadLetterForwardError(RuntimeError):
    """Raised when forwarding a dead-letter signal to the owning peer hub fails.

    A single type for every transmission failure — connection, protocol, or timeout — so an
    origin hub catches one error and treats the forward as best-effort: the durable origin-side
    audit is already written, so a failed hand-off degrades to "recorded but not yet delivered"
    rather than losing the signal.
    """


def forwarding_notice(
    target: str,
    count: int,
    *,
    origin_hub_id: str,
    owner_hub_id: str,
) -> dict[str, Any]:
    """Return the honesty-bound cross-hub dead-letter signal for one blackholed target.

    The notice names only what the ledger holds — the target, its undelivered count, and the
    origin and owning hub ids — and carries **no message body**, so it points the owning hub at a
    gap without ever moving a message across the trust boundary.

    Parameters
    ----------
    target : str
        The directed-message target whose messages are dead-lettering on the origin hub.
    count : int
        How many of the target's directed messages went undelivered on the origin hub.
    origin_hub_id : str
        The id of the hub reporting the blackhole (where the messages arrived).
    owner_hub_id : str
        The id of the peer hub whose domain owns the target's namespace.

    Returns
    -------
    dict[str, Any]
        The JSON-compatible signal: ``target``, ``count``, ``origin_hub_id``, ``owner_hub_id``.
    """
    return {
        "target": target,
        "count": count,
        "origin_hub_id": origin_hub_id,
        "owner_hub_id": owner_hub_id,
    }


class DeadLetterForwarder(Protocol):
    """Transmits a dead-letter forwarding notice to the owning peer hub.

    The seam an origin hub calls to hand the signal to the peer that owns the target. A
    deployment supplies a real cross-hub transport; a test injects a stand-in so the resolution
    and audit wiring is exercised without a live peer. It is fire-and-forget — the origin does
    not await a verdict — and raises :class:`DeadLetterForwardError` on any transmission failure,
    which the caller treats as best-effort over the already-durable audit.
    """

    async def __call__(
        self,
        notice: dict[str, Any],
        *,
        uri: str,
        local_id: str,
        token: str | None = None,
    ) -> None:  # pragma: no cover - structural
        """Transmit ``notice`` to the owning hub at ``uri``."""
        ...
