# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — classify directed delivery by consume liveness, not socket presence
"""Classify recipient matches by consume liveness.

A socket match proves that bytes can be queued to a transport. It does not prove
that the agent behind the socket is still reacting or that an independent waiter
can wake it. This module keeps that distinction as a small pure value: callers
supply the online matches and the subset whose reaction evidence is stale, then
receive stable live/stale partitions and a machine-readable negative reason.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from synapse_channel.core.agent_liveness import waiter_sidecar_names

NO_ONLINE_RECIPIENT = "no_online_recipient"
"""Negative receipt reason when no socket matched the directed target."""

NO_LIVE_RECIPIENT = "no_live_recipient"
"""Negative receipt reason when sockets matched but none had consume-liveness proof."""


@dataclass(frozen=True)
class DeliveryLiveness:
    """A stable partition of matched recipients into live and stale sets.

    Attributes
    ----------
    matched_recipients : tuple[str, ...]
        Unique logical recipients whose online socket or waiter matched the target.
    live_recipients : tuple[str, ...]
        Matched recipients with a recent reaction or a live waiter.
    stale_recipients : tuple[str, ...]
        Matched recipients lacking both consume-liveness proofs.
    reason : str
        Empty for a positive verdict, otherwise ``no_online_recipient`` or
        ``no_live_recipient`` or ``recipient_transport_unavailable``.
    """

    matched_recipients: tuple[str, ...]
    live_recipients: tuple[str, ...]
    stale_recipients: tuple[str, ...]
    reason: str

    @property
    def delivered(self) -> bool:
        """Return whether at least one matched recipient is consume-live."""
        return bool(self.live_recipients)


def classify_delivery_liveness(
    matched_recipients: Iterable[str],
    stale_recipients: Iterable[str],
) -> DeliveryLiveness:
    """Return an ordered, de-duplicated live/stale delivery partition.

    Parameters
    ----------
    matched_recipients : Iterable[str]
        Logical recipients selected from the current online roster.
    stale_recipients : Iterable[str]
        Members of that roster with neither a recent reaction nor a live waiter.
        Unknown names are ignored instead of being allowed to fabricate a match.

    Returns
    -------
    DeliveryLiveness
        The stable partition and its negative reason, if any.
    """
    matched = tuple(dict.fromkeys(name for name in matched_recipients if name))
    stale_names = set(stale_recipients)
    stale = tuple(name for name in matched if name in stale_names)
    live = tuple(name for name in matched if name not in stale_names)
    if live:
        reason = ""
    elif matched:
        reason = NO_LIVE_RECIPIENT
    else:
        reason = NO_ONLINE_RECIPIENT
    return DeliveryLiveness(
        matched_recipients=matched,
        live_recipients=live,
        stale_recipients=stale,
        reason=reason,
    )


RECIPIENT_TRANSPORT_UNAVAILABLE = "recipient_transport_unavailable"
"""Negative reason when no consume-live recipient completed an outbound write."""


def classify_completed_delivery(
    decision: DeliveryLiveness, successful_names: Iterable[str], *, include_waiters: bool = True
) -> DeliveryLiveness:
    """Keep only consume-live recipients whose own or waiter write completed.

    Observer and sender echoes cannot manufacture a successful recipient. A
    Set ``include_waiters=False`` for channels, whose audience is exact membership. A
    stale recipient remains stale even if its transport accepted bytes. Preserve
    the original matched and stale partitions for receipt audit evidence.
    """
    if not decision.delivered:
        return decision
    successful = set(successful_names)
    live = tuple(
        name
        for name in decision.live_recipients
        if name in successful
        or (include_waiters and successful.intersection(waiter_sidecar_names(name)))
    )
    return DeliveryLiveness(
        matched_recipients=decision.matched_recipients,
        live_recipients=live,
        stale_recipients=decision.stale_recipients,
        reason="" if live else RECIPIENT_TRANSPORT_UNAVAILABLE,
    )
