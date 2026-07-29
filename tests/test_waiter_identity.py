# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the waiter-sidecar naming convention as a unit

"""Unit tests for the single definition of the ``-rx`` waiter convention."""

from __future__ import annotations

import synapse_channel.core.waiter_identity as canonical
import synapse_channel.waiter_identity as compatibility
from synapse_channel.waiter_identity import (
    PANE_WAITER_SUFFIX,
    WAITER_SUFFIX,
    is_waiter,
    legacy_project_scoped_terminal_sidecar,
    pane_waiter_name,
    split_roster,
    waiter_name,
    waiter_owner,
)


def test_top_level_module_reexports_the_canonical_kernel_objects() -> None:
    assert compatibility.__all__ == canonical.__all__
    for name in canonical.__all__:
        assert getattr(compatibility, name) is getattr(canonical, name)


def test_is_waiter_recognises_the_sidecar_suffix() -> None:
    assert is_waiter("SYNAPSE-CHANNEL/claude-2759-rx")
    assert is_waiter("user/terminal-12345-rx")
    assert not is_waiter("SYNAPSE-CHANNEL/claude-2759")
    assert not is_waiter("agent-rx-primary")  # suffix must terminate the name


def test_a_bare_suffix_names_nobody() -> None:
    """``"-rx"`` alone has no owner, so it is not a waiter of anything."""
    assert not is_waiter(WAITER_SUFFIX)
    assert waiter_owner(WAITER_SUFFIX) == WAITER_SUFFIX


def test_owner_and_name_round_trip() -> None:
    owner = "quantum/codex-2b40"
    assert waiter_owner(waiter_name(owner)) == owner
    assert waiter_name(owner) == f"{owner}{WAITER_SUFFIX}"


def test_pane_bridge_sidecar_has_a_distinct_name_and_the_same_owner() -> None:
    owner = "quantum/codex-2b40"
    pane = pane_waiter_name(owner)

    assert pane == f"{owner}{PANE_WAITER_SUFFIX}"
    assert pane != waiter_name(owner)
    assert is_waiter(pane)
    assert waiter_owner(pane) == owner


def test_waiter_owner_leaves_a_plain_identity_unchanged() -> None:
    assert waiter_owner("USER") == "USER"
    assert waiter_owner("") == ""


def test_split_roster_sorts_agents_and_waiters_apart() -> None:
    roster = [
        "b/agent",
        "a/agent",
        "b/agent-rx",
        "a/agent-rx",
        "a/agent-pane-rx",
        "USER",
    ]
    agents, waiters = split_roster(roster)
    assert agents == ["USER", "a/agent", "b/agent"]
    assert waiters == ["a/agent-pane-rx", "a/agent-rx", "b/agent-rx"]


def test_split_roster_of_nothing_is_two_empty_lists() -> None:
    assert split_roster([]) == ([], [])


def test_legacy_broad_project_arm_names_the_exact_terminal_to_use() -> None:
    """A ``<project>/terminal-<id>-rx`` sidecar waking for the bare project is legacy."""
    assert (
        legacy_project_scoped_terminal_sidecar("quantum/terminal-14753-rx", "quantum")
        == "quantum/terminal-14753"
    )


def test_an_exact_identity_arm_is_not_flagged_as_legacy() -> None:
    """The replacement shape — sidecar of the exact identity it wakes — passes."""
    assert (
        legacy_project_scoped_terminal_sidecar(
            "quantum/terminal-14753-rx", "quantum/terminal-14753"
        )
        is None
    )
    assert (
        legacy_project_scoped_terminal_sidecar(
            "SYNAPSE-CHANNEL/claude-a7c2-rx", "SYNAPSE-CHANNEL/claude-a7c2"
        )
        is None
    )


def test_a_non_sidecar_connect_name_is_never_legacy() -> None:
    """Without the ``-rx`` suffix the owner equals the connect name, so no match."""
    assert legacy_project_scoped_terminal_sidecar("quantum/terminal-14753", "quantum") is None


def test_another_projects_terminal_sidecar_is_not_this_projects_legacy_arm() -> None:
    assert legacy_project_scoped_terminal_sidecar("fluctara/terminal-9-rx", "quantum") is None
