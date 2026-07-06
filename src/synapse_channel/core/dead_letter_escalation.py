# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — escalate a growing dead-letter blackhole past a threshold
"""Escalate a dead-letter blackhole that keeps growing, without re-delivering anything.

The dead-letter ledger (:mod:`synapse_channel.core.dead_letters`) records that directed messages
reached no live connection, and the state snapshot makes that visible. This adds the *follow-up*
step a reviewer asked for: when a target's undelivered count crosses an operator-set threshold, the
hub emits an escalation — a durable audit event and a one-line notice — so a blackhole that is
quietly filling becomes an active signal instead of something an operator must poll for.

The escalation is deliberately **not a retry**. The ledger keeps counts and names, never message
bodies (those live in the durable feed), so re-sending is impossible here by construction and would
in any case be a silent-redelivery hazard. Escalation is the honest action the ledger's data
supports: point a human or an orchestrator at a growing blackhole and let them decide.

To stay quiet on a busy hub, an escalation fires only when the count reaches an exact multiple of
the threshold — once at the threshold, then again each further threshold of accumulation — so a
persistent blackhole re-escalates as it worsens but a single undelivered message never does. A
threshold of zero (the default) disables escalation entirely, leaving the ledger's passive
visibility unchanged.
"""

from __future__ import annotations

DEFAULT_DEAD_LETTER_ESCALATION_THRESHOLD = 0
"""Escalate every this-many undelivered messages to one target; ``0`` disables escalation."""


def crosses_escalation_threshold(count: int, threshold: int) -> bool:
    """Return whether reaching ``count`` undelivered messages should escalate.

    An escalation fires when ``count`` is an exact positive multiple of ``threshold`` — at the
    threshold itself and at each further multiple — so a worsening blackhole re-escalates without a
    single message ever doing so. A non-positive ``threshold`` disables escalation.

    Parameters
    ----------
    count : int
        The target's undelivered-message count after the message that triggered this check.
    threshold : int
        The escalation interval; ``0`` or negative disables escalation.

    Returns
    -------
    bool
        Whether this ``count`` is an escalation point.
    """
    if threshold < 1 or count < 1:
        return False
    return count % threshold == 0


def escalation_notice(target: str, count: int, last_sender: str) -> str:
    """Return the one-line human-readable escalation message for a growing blackhole."""
    return (
        f"dead-letter escalation: {count} directed messages to {target!r} have reached no live "
        f"connection (most recent from {last_sender!r}); nobody is draining that name"
    )
