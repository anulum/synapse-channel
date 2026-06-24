# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — property-based invariants for the lease/epoch/heap core

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from synapse_channel.core.state import SynapseState

_AGENTS = st.sampled_from(["A", "B", "C"])
_TASKS = st.sampled_from(["T1", "T2", "T3"])
_OP = st.tuples(
    st.sampled_from(["claim", "release", "update", "handoff"]), _AGENTS, _TASKS, _AGENTS
)


@given(ops=st.lists(_OP, max_size=40))
@settings(max_examples=200, deadline=None)
def test_epoch_and_lease_invariants_hold_under_random_ops(
    ops: list[tuple[str, str, str, str]],
) -> None:
    # Each task lives in its own worktree, so claims on different tasks never
    # scope-conflict and the sequence exercises the claim/epoch/lease logic itself.
    state = SynapseState(default_ttl_seconds=1000.0)
    for index, (kind, agent, task, other) in enumerate(ops):
        now = float(index)
        before_epoch = state._epoch_seq
        succeeded = False
        if kind == "claim":
            succeeded = state.claim(agent, task, now=now, worktree=task)[0]
        elif kind == "release":
            state.release(agent, task, now=now)
        elif kind == "update":
            state.update_task(agent, task, status="in_progress", now=now)
        elif kind == "handoff":
            succeeded = state.handoff(agent, task, other, now=now)[0]

        # A successful (re)claim or handoff stamps a strictly greater epoch.
        if succeeded:
            assert state._epoch_seq > before_epoch
        # Every live claim carries a valid epoch and a lease still in the future
        # (nothing should have lapsed yet, since the TTL dwarfs the step count).
        for claim in state.claims.values():
            assert 1 <= claim.epoch <= state._epoch_seq
            assert claim.lease_expires_at > now
            assert claim.version >= 0

    # An expiry pass far in the future clears every lease — proving the lease heap
    # accounts for every claim the random sequence produced.
    state.heartbeat("A", now=1e9)
    assert state.claims == {}


@given(ops=st.lists(_OP, max_size=30))
@settings(max_examples=150, deadline=None)
def test_handoff_transfers_ownership_atomically(
    ops: list[tuple[str, str, str, str]],
) -> None:
    state = SynapseState(default_ttl_seconds=1000.0)
    for index, (kind, agent, task, other) in enumerate(ops):
        now = float(index)
        if kind == "claim":
            state.claim(agent, task, now=now, worktree=task)
        elif kind == "handoff":
            ok, _ = state.handoff(agent, task, other, now=now)
            # A successful handoff leaves the task owned by the target, never the
            # giver, and never drops it into an unowned limbo.
            if ok:
                assert state.claims[task].owner == other
        elif kind == "release":
            state.release(agent, task, now=now)
        # Each task is owned by exactly one agent at a time (a structural single-writer).
        assert all(claim.task_id == name for name, claim in state.claims.items())
