# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — which departures the claim-holder presence records
"""A departure is recorded only for a name that some claim lives by.

The hub stamps a departure so that a claim's offline window runs from the moment its holder
left. Only the names claims live by are stamped: a claim's owner, or the peer hub that
forwarded it. Stamping every departure would keep an entry for every name that ever
disconnected, and a stale stamp would shorten the window of a claim that name gains later.
"""

from __future__ import annotations

from synapse_channel.core.claim_holder_presence import ClaimHolderPresence
from synapse_channel.core.state import SynapseState


def test_a_departure_is_stamped_only_for_a_name_a_claim_lives_by() -> None:
    """The holder and the forwarding peer are stamped; the claimant and a bystander are not."""
    state = SynapseState()
    assert state.claim("ALPHA/holder", "tH", paths=("held.py",))[0]
    assert state.claim("ALPHA/alice", "tF", paths=("forwarded.py",), via_peer="peer")[0]
    presence = ClaimHolderPresence(clock=lambda: 10.0, started_at=0.0, window=5.0)

    for name in ("ALPHA/holder", "peer", "ALPHA/alice", "bystander"):
        presence.left_if_holding(name, state.claims.values())

    # A stamped name counts from its departure, an unstamped one from the hub's start.
    assert presence.offline_seconds("ALPHA/holder", online=False, now=12.0) == 2.0
    assert presence.offline_seconds("peer", online=False, now=12.0) == 2.0
    assert presence.offline_seconds("ALPHA/alice", online=False, now=12.0) == 12.0
    assert presence.offline_seconds("bystander", online=False, now=12.0) == 12.0
