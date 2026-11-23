# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — hub-level dead-letter blackhole escalation handler
"""Hub-level dead-letter escalation: audit, broadcast and forward blackhole signals."""

from __future__ import annotations

from typing import TYPE_CHECKING

from synapse_channel.core.dead_letter_escalation import escalation_notice
from synapse_channel.core.handlers.dead_letter_forwarding import forward_dead_letter_to_peer
from synapse_channel.core.journal import record_dead_letter_escalation
from synapse_channel.core.protocol import MessageType

if TYPE_CHECKING:
    from synapse_channel.core.handlers.messaging import MessagingContext


async def escalate_dead_letter(
    hub: MessagingContext, *, target: str, count: int, sender: str
) -> None:
    """Escalate a dead-letter blackhole that has crossed its threshold.

    The escalation is an active signal, never a re-delivery (the ledger holds no message bodies):
    the hub broadcasts a one-line notice to every connected socket — so an operator sees a growing
    blackhole live — and journals an audit-only event when a durable log is attached, so the
    escalation is also reviewable after the fact. The named target is almost certainly not
    connected (that is why its messages are dead-lettering), so the notice reaches the operators and
    peers who can act, not the missing reader.
    """
    # Persist before notifying, so a reader that sees the broadcast can trust the audit is written.
    if hub.journal is not None:
        record_dead_letter_escalation(
            hub.journal,
            {
                "target": target,
                "count": count,
                "last_sender": sender,
                "threshold": hub.dead_letter_escalation_threshold,
            },
        )
    notice = escalation_notice(target, count, sender)
    await hub.broadcast(
        hub.system(
            notice,
            msg_type=MessageType.DEAD_LETTER_ESCALATION,
            escalation_target=target,
            escalation_count=count,
            last_sender=sender,
        )
    )
    await forward_dead_letter_to_peer(hub, target=target, count=count)
