# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for wait-for cycle detection

from __future__ import annotations

from types import SimpleNamespace

from synapse_channel.core.deadlock import prune_waits, resolve_wait_edges, would_create_cycle


def _claims(**owner_by_task: str) -> dict[str, SimpleNamespace]:
    return {task: SimpleNamespace(owner=owner) for task, owner in owner_by_task.items()}


def test_self_wait_is_a_cycle() -> None:
    assert would_create_cycle({}, _claims(), "A", "A") is True


def test_empty_graph_has_no_cycle() -> None:
    assert would_create_cycle({}, _claims(T1="B"), "A", "B") is False


def test_direct_mutual_wait_is_a_cycle() -> None:
    # A already waits on B's task; B waiting on A closes the loop.
    waits = {"A": {"T1"}}
    assert would_create_cycle(waits, _claims(T1="B"), "B", "A") is True


def test_independent_wait_is_safe() -> None:
    # A waits on B's task; C waiting on B is fine (no cycle).
    waits = {"A": {"T1"}}
    assert would_create_cycle(waits, _claims(T1="B"), "C", "B") is False


def test_transitive_cycle_is_detected() -> None:
    # A -> B -> C already; C waiting on A closes a three-node cycle.
    waits = {"A": {"T1"}, "B": {"T2"}}
    assert would_create_cycle(waits, _claims(T1="B", T2="C"), "C", "A") is True


def test_transitive_chain_without_cycle_is_safe() -> None:
    # A -> B -> C already; D waiting on C extends the chain, no cycle.
    waits = {"A": {"T1"}, "B": {"T2"}}
    assert would_create_cycle(waits, _claims(T1="B", T2="C"), "D", "C") is False


def test_walk_terminates_on_preexisting_cycle() -> None:
    # A degenerate already-cyclic graph must not loop forever.
    waits = {"A": {"T1"}, "B": {"T2"}}
    assert would_create_cycle(waits, _claims(T1="B", T2="A"), "C", "A") is False


def test_edge_to_an_unclaimed_task_cannot_form_a_cycle() -> None:
    # A waits on a task nobody holds: the stale edge is invisible at check time.
    waits = {"A": {"T-GONE"}}
    assert would_create_cycle(waits, _claims(), "B", "A") is False


def test_handoff_repoints_the_wait_at_the_new_holder() -> None:
    # A waits on T1; T1 moves B -> C, so C waiting on A now closes the cycle.
    waits = {"A": {"T1"}}
    assert would_create_cycle(waits, _claims(T1="C"), "C", "A") is True
    assert would_create_cycle(waits, _claims(T1="C"), "B", "A") is False


def test_resolve_wait_edges_skips_free_tasks_and_empty_owners() -> None:
    waits = {"A": {"T1", "T-GONE", "T2"}}
    claims = _claims(T1="B", T2="")
    assert resolve_wait_edges(waits, claims) == {"A": {"B"}}


def test_prune_waits_drops_stale_tasks_and_empty_waiters() -> None:
    waits = {"A": {"T1", "T-GONE"}, "B": {"T-GONE"}}
    assert prune_waits(waits, _claims(T1="C")) == {"A": {"T1"}}


def test_claim_mapping_forms_are_accepted() -> None:
    waits = {"A": {"T1"}}
    claims = {"T1": {"owner": "B"}}
    assert would_create_cycle(waits, claims, "B", "A") is True
