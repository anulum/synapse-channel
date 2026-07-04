# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — property-based tests for coordination invariants
"""Property-based coverage for the coordination invariants the bus must hold.

Example-based tests fix the cases an author thought of; these fix the *rules*
those cases are instances of and let Hypothesis search for a counterexample. The
invariants under test are the ones a correctness bug would break silently: two
claim scopes must agree on whether they contend for a file, a retried mutation
must replay the same response its key first produced, and a task must never take a
lifecycle transition the table forbids — least of all out of a terminal state.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from synapse_channel.core.idempotency import IdempotencyCache
from synapse_channel.core.lifecycle import (
    ALL_STATUSES,
    TERMINAL_STATUSES,
    can_transition,
    is_terminal,
)
from synapse_channel.core.scoping import paths_overlap, scopes_conflict

# --- strategies --------------------------------------------------------------

_SEGMENTS = st.sampled_from(["src", "core", "tests", "a", "b", "c", "..", ".", ""])
_paths = st.builds(lambda parts: "/".join(parts), st.lists(_SEGMENTS, max_size=4))
_path_sets = st.lists(_paths, max_size=4)
_worktrees = st.sampled_from(["main", "wt-a", "wt-b", "feature"])
_statuses = st.sampled_from(sorted(ALL_STATUSES))
_keys = st.text(min_size=1, max_size=6)
_responses = st.dictionaries(st.text(max_size=4), st.integers(), max_size=4)


# --- claim-overlap invariants ------------------------------------------------


@given(a=_paths, b=_paths)
def test_paths_overlap_is_symmetric(a: str, b: str) -> None:
    assert paths_overlap(a, b) == paths_overlap(b, a)


@given(a=_paths)
def test_paths_overlap_is_reflexive(a: str) -> None:
    assert paths_overlap(a, a) is True


@given(a=_paths)
def test_root_path_overlaps_everything(a: str) -> None:
    assert paths_overlap("", a) is True
    assert paths_overlap(a, "") is True


@given(wt_a=_worktrees, pa=_path_sets, wt_b=_worktrees, pb=_path_sets)
def test_scopes_conflict_is_symmetric(wt_a: str, pa: list[str], wt_b: str, pb: list[str]) -> None:
    assert scopes_conflict(wt_a, pa, wt_b, pb) == scopes_conflict(wt_b, pb, wt_a, pa)


@given(wt_a=_worktrees, pa=_path_sets, wt_b=_worktrees, pb=_path_sets)
def test_scopes_conflict_only_across_the_same_worktree(
    wt_a: str, pa: list[str], wt_b: str, pb: list[str]
) -> None:
    if wt_a != wt_b:
        assert scopes_conflict(wt_a, pa, wt_b, pb) is False


@given(
    wt=_worktrees,
    pa=st.lists(_paths, min_size=1, max_size=4),
    pb=st.lists(_paths, min_size=1, max_size=4),
)
def test_non_conflicting_scopes_share_no_overlapping_path_pair(
    wt: str, pa: list[str], pb: list[str]
) -> None:
    # scopes_conflict and paths_overlap must agree: when the same-worktree scopes
    # do not conflict, no declared path of one may overlap any declared path of
    # the other. A disagreement would let a real file collision slip a claim.
    if not scopes_conflict(wt, pa, wt, pb):
        assert not any(paths_overlap(a, b) for a in pa for b in pb)


@given(wt=_worktrees, pb=_path_sets)
def test_whole_worktree_claim_conflicts_with_any_other(wt: str, pb: list[str]) -> None:
    # An empty path set owns the whole worktree, so it must conflict with any
    # non-empty claim there (and with another whole-worktree claim).
    assert scopes_conflict(wt, [], wt, pb) is True


# --- idempotency invariants --------------------------------------------------


@given(key=_keys, response=_responses)
def test_put_then_get_replays_the_same_response(key: str, response: dict[str, int]) -> None:
    cache = IdempotencyCache()
    cache.put(key, response)
    assert key in cache
    assert cache.get(key) == response


@given(
    key=_keys,
    first=_responses,
    second=_responses,
    others=st.lists(st.tuples(_keys, _responses), max_size=8),
)
def test_get_returns_the_most_recent_put_for_a_key(
    key: str,
    first: dict[str, int],
    second: dict[str, int],
    others: list[tuple[str, dict[str, int]]],
) -> None:
    # A repeated key replays the response its last put produced — never a stale one.
    cache = IdempotencyCache(max_keys=1024)
    cache.put(key, first)
    for other_key, other_response in others:
        if other_key != key:
            cache.put(other_key, other_response)
    cache.put(key, second)
    assert cache.get(key) == second


@given(
    entries=st.lists(st.tuples(_keys, _responses), max_size=40),
    max_keys=st.integers(min_value=1, max_value=8),
)
def test_cache_never_exceeds_its_bound(
    entries: list[tuple[str, dict[str, int]]], max_keys: int
) -> None:
    cache = IdempotencyCache(max_keys=max_keys)
    for key, response in entries:
        cache.put(key, response)
        assert len(cache) <= max_keys


# --- lifecycle invariants ----------------------------------------------------


@given(current=_statuses, target=_statuses)
def test_terminal_states_never_transition_out(current: str, target: str) -> None:
    if is_terminal(current) and current != target:
        assert can_transition(current, target) is False


@given(current=_statuses, target=st.text(max_size=8))
def test_transition_to_unknown_status_is_refused(current: str, target: str) -> None:
    if target not in ALL_STATUSES:
        assert can_transition(current, target) is False


@given(current=_statuses)
def test_reaffirming_a_known_status_is_allowed(current: str) -> None:
    assert can_transition(current, current) is True


@given(current=_statuses)
def test_every_non_terminal_status_can_reach_a_terminal(current: str) -> None:
    if not is_terminal(current):
        assert any(can_transition(current, terminal) for terminal in TERMINAL_STATUSES)
