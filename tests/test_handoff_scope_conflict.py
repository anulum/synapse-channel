# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — handoff honours file-scope mutual exclusion
"""A handoff must not hand a recipient files another agent still holds live.

Direct :meth:`SynapseState.claim` refuses a scope that overlaps another agent's
live claim — that refusal is the whole file-scope mutual-exclusion guarantee.
Before this regression a :meth:`SynapseState.handoff` skipped the check, so an
owner holding two overlapping claims could hand one away and leave two different
agents holding the same files — exactly the collision ``claim`` prevents. These
tests pin the parity: handoff enforces the same scope conflict, and only the same.
"""

from __future__ import annotations

from synapse_channel.core.state import SynapseState


def test_handoff_into_another_agents_live_scope_is_refused() -> None:
    """The move is refused when it would collide with a third party's live claim."""
    state = SynapseState(default_ttl_seconds=1000.0)
    # A holds two overlapping whole-worktree claims (self-conflict is allowed).
    assert state.claim("A", "T2", now=0.0, worktree="wt", paths=())[0]
    assert state.claim("A", "T1", now=0.0, worktree="wt", paths=())[0]

    ok, reason = state.handoff("A", "T1", "B", now=0.0)

    assert ok is False
    assert "file scope conflicts" in reason
    assert "T2" in reason
    # The lease is untouched: A still owns T1, and B gained nothing.
    assert state.claims["T1"].owner == "A"
    assert not any(claim.owner == "B" for claim in state.claims.values())


def test_handoff_matches_the_claim_path_refusal() -> None:
    """A handoff and a direct claim refuse the very same colliding scope."""
    # Direct claim: B cannot claim T1's scope while A holds an overlapping T2.
    claimed = SynapseState(default_ttl_seconds=1000.0)
    claimed.claim("A", "T2", now=0.0, worktree="wt", paths=("src",))
    claim_ok, claim_reason = claimed.claim("B", "T1", now=0.0, worktree="wt", paths=("src/a.py",))

    # Handoff: the same post-state is refused the same way.
    handed = SynapseState(default_ttl_seconds=1000.0)
    handed.claim("A", "T2", now=0.0, worktree="wt", paths=("src",))
    handed.claim("A", "T1", now=0.0, worktree="wt", paths=("src/a.py",))
    hand_ok, hand_reason = handed.handoff("A", "T1", "B", now=0.0)

    assert claim_ok is False and hand_ok is False
    assert "file scope conflicts" in claim_reason
    assert "file scope conflicts" in hand_reason


def test_handoff_to_an_agent_holding_its_own_overlapping_scope_is_allowed() -> None:
    """The recipient's own overlapping claim never blocks the move (self-conflict)."""
    state = SynapseState(default_ttl_seconds=1000.0)
    # B already holds src in wt; A holds a disjoint file and hands B a task whose
    # scope overlaps B's own claim. The recipient may overlap itself.
    assert state.claim("B", "T2", now=0.0, worktree="wt", paths=("src",))[0]
    assert state.claim("A", "T1", now=0.0, worktree="wt", paths=("docs",))[0]
    # Move T1 to B; T1's scope (docs) does not collide with anyone, so it succeeds.
    assert state.handoff("A", "T1", "B", now=0.0)[0]
    assert state.claims["T1"].owner == "B"


def test_handoff_recipient_may_receive_a_scope_overlapping_only_its_own_claims() -> None:
    """A recipient can receive a scope that overlaps only claims it already owns."""
    state = SynapseState(default_ttl_seconds=1000.0)
    # B holds the whole worktree; A holds a task in a different worktree.
    assert state.claim("B", "T2", now=0.0, worktree="wt", paths=())[0]
    assert state.claim("A", "T1", now=0.0, worktree="wt-other", paths=())[0]
    # Re-scope is not what handoff does; T1 keeps wt-other, disjoint from B's wt,
    # so the move is clean.
    assert state.handoff("A", "T1", "B", now=0.0)[0]
    assert state.claims["T1"].owner == "B"


def test_non_conflicting_handoff_still_succeeds() -> None:
    """A handoff with no scope collision keeps working exactly as before."""
    state = SynapseState(default_ttl_seconds=1000.0)
    assert state.claim("A", "T1", now=0.0, worktree="wt-a", paths=("src/x.py",))[0]
    ok, _ = state.handoff("A", "T1", "B", now=0.0)
    assert ok is True
    assert state.claims["T1"].owner == "B"
    assert state.claims["T1"].version == 0
