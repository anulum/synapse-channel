# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — module-owned coordination state scope-scan tests
"""Exercise deterministic file-scope conflict selection over live claims."""

from __future__ import annotations

from synapse_channel.core import state_scopes
from synapse_channel.core.state_models import TaskClaim


def _claim(
    task_id: str,
    owner: str,
    *,
    worktree: str = "",
    paths: tuple[str, ...] = (),
) -> TaskClaim:
    """Build the minimal live claim shape consumed by the scope scanner."""
    return TaskClaim(
        task_id=task_id,
        owner=owner,
        note="",
        claimed_at=1.0,
        lease_expires_at=2.0,
        worktree=worktree,
        paths=paths,
    )


def test_empty_claim_registry_has_no_scope_conflict() -> None:
    """A request cannot conflict when no other live claim exists."""
    assert (
        state_scopes.find_scope_conflict(
            {},
            task="NEW",
            agent="ALPHA",
            worktree="main",
            paths=("src",),
        )
        is None
    )


def test_scan_ignores_the_same_task_owner_and_disjoint_scopes() -> None:
    """Renewals, one owner's work, other worktrees, and disjoint paths stay free."""
    claims = {
        "NEW": _claim("NEW", "BRAVO", worktree="main", paths=("src",)),
        "OWN": _claim("OWN", "ALPHA", worktree="main", paths=("src/app.py",)),
        "OTHER-TREE": _claim("OTHER-TREE", "BRAVO", worktree="feature", paths=("src",)),
        "DISJOINT": _claim("DISJOINT", "CHARLIE", worktree="main", paths=("docs",)),
    }

    assert (
        state_scopes.find_scope_conflict(
            claims,
            task="NEW",
            agent="ALPHA",
            worktree="main",
            paths=("src",),
        )
        is None
    )


def test_scan_returns_the_first_conflicting_claim_in_mapping_order() -> None:
    """Conflict reporting is deterministic when more than one other claim overlaps."""
    claims = {
        "FIRST": _claim("FIRST", "BRAVO", worktree="main", paths=("src/app.py",)),
        "SECOND": _claim("SECOND", "CHARLIE", worktree="main", paths=("src/lib",)),
    }

    assert state_scopes.find_scope_conflict(
        claims,
        task="NEW",
        agent="ALPHA",
        worktree="main",
        paths=("src",),
    ) == ("FIRST", "BRAVO")


def test_whole_worktree_claim_conflicts_only_inside_its_worktree() -> None:
    """An empty path set is fail-closed for its tree but cannot cross tree labels."""
    claims = {"TREE": _claim("TREE", "BRAVO", worktree="main", paths=())}

    assert state_scopes.find_scope_conflict(
        claims,
        task="NEW",
        agent="ALPHA",
        worktree="main",
        paths=("unrelated/file.py",),
    ) == ("TREE", "BRAVO")
    assert (
        state_scopes.find_scope_conflict(
            claims,
            task="NEW",
            agent="ALPHA",
            worktree="feature",
            paths=(),
        )
        is None
    )
