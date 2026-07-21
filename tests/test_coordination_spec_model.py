# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — machine-checkable claim/lease/fencing model
"""A machine-checkable state model for the coordination specification.

``docs/coordination-spec.md`` states the numbered invariants of the single-hub
claim/lease/fencing core. Example-based tests fix the cases an author thought of;
this Hypothesis :class:`~hypothesis.stateful.RuleBasedStateMachine` fixes the
*rules* and lets the engine search randomised claim/renew/release/handoff/update/
checkpoint/time sequences for a run that breaks one, shrinking any counterexample
to a minimal reproducer.

The machine drives the **real** :class:`~synapse_channel.core.state.SynapseState`
(no re-implemented shadow of its logic that could be wrong in the same way) and,
after every step, asserts the safety invariants the registry must always hold.
Each enforced invariant is listed in :data:`MODEL_INVARIANTS` by its
specification identifier; the drift guard
``tests/test_coordination_spec.py`` asserts every identifier here is documented
in the specification, so the model and the spec cannot silently diverge.

Lazy expiry note: ``SynapseState`` expires leases only when an operation observes
the clock, so the raw ``claims`` map may briefly retain a lapsed lease after time
advances. Every invariant therefore materialises the live view with
``snapshot(now)`` (which runs the same expiry the hub runs on a read) before
asserting, matching exactly what a reader of the hub would see.
"""

from __future__ import annotations

from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from synapse_channel.core.scoping import scopes_conflict
from synapse_channel.core.state import (
    MAX_CLAIMS_PER_AGENT,
    MAXIMUM_TTL_SECONDS,
    SynapseState,
)
from synapse_channel.core.state_models import TaskClaim

# Every specification invariant this model mechanically checks after each step
# (an @invariant) or at a transition (a rule-local assertion). The drift guard
# binds this set to the documented `INV-*` identifiers.
MODEL_INVARIANTS = frozenset(
    {
        "INV-ME-1",  # one owner per task
        "INV-ME-2",  # file-scope overlap refused (ancestry)
        "INV-ME-3",  # different worktrees never contend
        "INV-ME-4",  # whole-worktree claim excludes others
        "INV-ME-5",  # per-principal live-claim cap
        "INV-EF-1",  # epochs strictly increasing and bounded
        "INV-EF-2",  # a stale epoch is fenced out
        "INV-EF-3",  # optimistic-concurrency version guard
        "INV-LL-1",  # a live claim's lease is in the future
        "INV-LL-2",  # expiry frees the task
        "INV-LL-4",  # same-owner renewal is free
        "INV-CR-2",  # handoff is atomic
    }
)

_AGENTS = ["A", "B", "C"]
_TASKS = ["T1", "T2", "T3"]
# Shared worktrees so that distinct tasks can contend for the same files, which
# is what exercises the scope-conflict refusal (INV-ME-2/3/4).
_WORKTREES = ["wt-main", "wt-side"]
# "" is the whole-worktree claim; the rest overlap by directory ancestry.
_PATHS = ["", "src", "src/a.py", "src/b.py", "tests", "tests/x.py"]

_agents = st.sampled_from(_AGENTS)
_tasks = st.sampled_from(_TASKS)
_worktrees = st.sampled_from(_WORKTREES)
_path_sets = st.lists(st.sampled_from(_PATHS), max_size=3, unique=True)


class CoordinationModel(RuleBasedStateMachine):
    """Drive a real ``SynapseState`` and assert the spec invariants each step."""

    def __init__(self) -> None:
        """Start an empty registry with a monotonic logical clock."""
        super().__init__()
        # A generous default TTL keeps leases alive across ordinary steps, so the
        # expiry path is exercised deliberately by ``advance_time`` rather than by
        # accident on every claim.
        self.state = SynapseState(default_ttl_seconds=3600.0)
        self.now = 1_000.0
        # The greatest epoch the hub has issued so far, tracked independently so a
        # non-monotonic epoch would be caught (INV-EF-1).
        self._max_epoch = 0

    # -- helpers --------------------------------------------------------------

    def _live_claims(self) -> dict[str, TaskClaim]:
        """Materialise the live view the same way a hub reader would.

        ``snapshot`` runs lease expiry at ``self.now`` first, so the returned
        ``state.claims`` contains only genuinely-live leases.
        """
        self.state.snapshot(now=self.now)
        return dict(self.state.claims)

    def _owner_of(self, task: str) -> str | None:
        """Return the live owner of ``task``, or ``None`` when it is free."""
        claim = self._live_claims().get(task)
        return claim.owner if claim is not None else None

    def _assert_monotonic_epoch(self) -> None:
        """A successful mutation must stamp a strictly greater epoch (INV-EF-1)."""
        assert self.state._epoch_seq > self._max_epoch, "INV-EF-1: epoch did not advance"
        self._max_epoch = self.state._epoch_seq

    # -- rules ----------------------------------------------------------------

    @rule(agent=_agents, task=_tasks, worktree=_worktrees, paths=_path_sets)
    def claim(self, agent: str, task: str, worktree: str, paths: list[str]) -> None:
        """Attempt a scoped claim; on success the epoch must advance (INV-EF-1)."""
        ok, _ = self.state.claim(agent, task, now=self.now, worktree=worktree, paths=tuple(paths))
        if ok:
            self._assert_monotonic_epoch()
            assert self._owner_of(task) == agent

    @rule(task=_tasks)
    def renew(self, task: str) -> None:
        """A same-owner renewal of the same scope is free (INV-LL-4).

        The renewal reuses the claim's own worktree and paths: a renewal never
        self-conflicts, so refusing it could only ever come from the quota gate —
        which a same-principal renewal is exempt from. (Moving the scope into
        another agent's territory is a scope *change* and may still be refused;
        that is INV-ME-2, not a renewal.)
        """
        owner = self._owner_of(task)
        if owner is None:
            return
        existing = self.state.claims[task]
        before = existing.epoch
        ok, _ = self.state.claim(
            owner,
            task,
            now=self.now,
            worktree=existing.worktree,
            paths=tuple(existing.paths),
        )
        assert ok, "INV-LL-4: an owner must be able to renew its own live claim"
        self._assert_monotonic_epoch()
        # Renewal never creates a second entry and strictly advances the epoch.
        assert self.state.claims[task].owner == owner
        assert self.state.claims[task].epoch > before

    @rule(task=_tasks)
    def release(self, task: str) -> None:
        """Release by the current owner; the task must then be free."""
        owner = self._owner_of(task)
        if owner is None:
            return
        ok, _ = self.state.release(owner, task, now=self.now)
        assert ok
        assert self._owner_of(task) is None

    @rule(task=_tasks)
    def release_with_stale_epoch_is_fenced(self, task: str) -> None:
        """A release carrying a superseded epoch is refused (INV-EF-2)."""
        owner = self._owner_of(task)
        if owner is None:
            return
        claim = self.state.claims[task]
        ok, _ = self.state.release(owner, task, now=self.now, epoch=claim.epoch + 1000)
        assert not ok, "INV-EF-2: a stale epoch must not release the lease"
        assert self._owner_of(task) == owner

    @rule(task=_tasks, recipient=_agents)
    def handoff(self, task: str, recipient: str) -> None:
        """Hand off atomically: ownership moves whole to the recipient (INV-CR-2)."""
        owner = self._owner_of(task)
        if owner is None or owner == recipient:
            return
        ok, _ = self.state.handoff(owner, task, recipient, now=self.now)
        if ok:
            self._assert_monotonic_epoch()
            # Atomic: the task is owned by the recipient, never dropped to limbo,
            # and the version resets for the new owner.
            assert self.state.claims[task].owner == recipient, "INV-CR-2: handoff not atomic"
            assert self.state.claims[task].version == 0

    @rule(task=_tasks)
    def update_status(self, task: str) -> None:
        """A legal transition by the owner bumps the version (INV-EF-3)."""
        owner = self._owner_of(task)
        if owner is None:
            return
        before = self.state.claims[task].version
        ok, _ = self.state.update_task(owner, task, status="working", now=self.now)
        if ok:
            assert self.state.claims[task].version == before + 1

    @rule(task=_tasks)
    def update_with_stale_version_is_fenced(self, task: str) -> None:
        """A stale ``expected_version`` cannot clobber a newer value (INV-EF-3)."""
        owner = self._owner_of(task)
        if owner is None:
            return
        claim = self.state.claims[task]
        ok, _ = self.state.update_task(
            owner,
            task,
            status="working",
            now=self.now,
            expected_version=claim.version + 500,
        )
        assert not ok, "INV-EF-3: a stale version must be refused"

    @rule(task=_tasks, checkpoint=st.text(max_size=8))
    def save_checkpoint(self, task: str, checkpoint: str) -> None:
        """Only the owner may checkpoint an owned task."""
        owner = self._owner_of(task)
        if owner is None:
            return
        self.state.save_checkpoint(owner, task, checkpoint, now=self.now)

    @rule(delta=st.floats(min_value=1.0, max_value=7_200.0))
    def advance_time(self, delta: float) -> None:
        """Advance the monotonic clock and materialise expiry at the new time."""
        self.now += delta
        # Observe the clock so lazily-lapsed leases are swept, exactly as a hub
        # read would (keeps the raw registry aligned with what a reader sees).
        self.state.snapshot(now=self.now)

    @precondition(lambda self: bool(self.state.claims))
    @rule()
    def expire_everything_frees_all_tasks(self) -> None:
        """A far-future sweep clears every lease the run produced (INV-LL-2)."""
        far = self.now + MAXIMUM_TTL_SECONDS + 1.0
        self.now = far
        self.state.heartbeat("A", now=far)
        assert self.state.claims == {}, "INV-LL-2: leases must lapse under a far sweep"

    # -- invariants (checked after every step) --------------------------------

    @invariant()
    def one_owner_per_task(self) -> None:
        """INV-ME-1: each live task maps to exactly one claim keyed by its id."""
        for task_id, claim in self._live_claims().items():
            assert claim.task_id == task_id, "INV-ME-1"

    @invariant()
    def no_conflicting_live_pair(self) -> None:
        """INV-ME-2/3/4: no two different-owner live claims may scope-conflict."""
        live = list(self._live_claims().values())
        for i, first in enumerate(live):
            for second in live[i + 1 :]:
                if first.owner == second.owner:
                    continue  # an agent may hold overlapping scopes of its own
                assert not scopes_conflict(
                    first.worktree,
                    first.paths,
                    second.worktree,
                    second.paths,
                ), "INV-ME-2/3/4: two owners hold conflicting live scopes"

    @invariant()
    def principal_quota_and_lease_and_epoch(self) -> None:
        """INV-ME-5 / INV-LL-1 / INV-EF-1 / INV-EF-3 over the live registry."""
        counts: dict[str, int] = {}
        for claim in self._live_claims().values():
            principal = claim.quota_principal or claim.owner
            counts[principal] = counts.get(principal, 0) + 1
            # INV-LL-1: a live lease is always in the future.
            assert claim.lease_expires_at > self.now, "INV-LL-1"
            # INV-EF-1: every epoch is issued and bounded by the counter.
            assert 1 <= claim.epoch <= self.state._epoch_seq, "INV-EF-1"
            # INV-EF-3: the version counter is never negative.
            assert claim.version >= 0, "INV-EF-3"
        for principal, count in counts.items():
            assert count <= MAX_CLAIMS_PER_AGENT, f"INV-ME-5: {principal} over cap"


CoordinationModel.TestCase.settings = settings(
    max_examples=200,
    stateful_step_count=40,
    deadline=None,
)
TestCoordinationSpecModel = CoordinationModel.TestCase
