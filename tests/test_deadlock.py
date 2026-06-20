# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for wait-for cycle detection

from __future__ import annotations

from synapse_channel.deadlock import would_create_cycle


def test_self_wait_is_a_cycle() -> None:
    assert would_create_cycle({}, "A", "A") is True


def test_empty_graph_has_no_cycle() -> None:
    assert would_create_cycle({}, "A", "B") is False


def test_direct_mutual_wait_is_a_cycle() -> None:
    # A already waits for B; B waiting for A closes the loop.
    assert would_create_cycle({"A": "B"}, "B", "A") is True


def test_independent_wait_is_safe() -> None:
    # A waits for B; C waiting for B is fine (no cycle).
    assert would_create_cycle({"A": "B"}, "C", "B") is False


def test_transitive_cycle_is_detected() -> None:
    # A -> B -> C already; C waiting for A closes a three-node cycle.
    assert would_create_cycle({"A": "B", "B": "C"}, "C", "A") is True


def test_transitive_chain_without_cycle_is_safe() -> None:
    # A -> B -> C already; D waiting for C extends the chain, no cycle.
    assert would_create_cycle({"A": "B", "B": "C"}, "D", "C") is False


def test_walk_terminates_on_preexisting_cycle() -> None:
    # A degenerate already-cyclic graph must not loop forever.
    assert would_create_cycle({"A": "B", "B": "A"}, "C", "A") is False
