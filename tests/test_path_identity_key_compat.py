# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — worktree keys stay comparable across client versions
"""Claims from clients before and after the inode generation still cover each other."""

from __future__ import annotations

from synapse_channel.core.causality import CONTENTION, build_causal_graph
from synapse_channel.core.journal import EventKind
from synapse_channel.core.path_identity import (
    CanonicalPathIdentity,
    ClaimScopeIdentity,
    claim_object_ids_comparable,
    claim_scope_covers_path,
    claim_scopes_conflict,
    claim_worktrees_match,
    object_key_base,
)
from synapse_channel.core.persistence import StoredEvent

LEGACY_ROOT = "811:34c0020"
CURRENT_ROOT = "811:34c0020:4a64fbef"


def _identity(root_key: str, object_id: str = "811:1") -> ClaimScopeIdentity:
    return ClaimScopeIdentity(
        worktree_path="/repo",
        worktree_object_id=root_key,
        filesystem_namespace="sha256:host",
        case_sensitive=True,
        paths=(CanonicalPathIdentity("CHANGELOG.md", "CHANGELOG.md", object_id),),
    )


def test_a_legacy_root_key_matches_the_same_root_with_a_generation() -> None:
    """A claim made before the generation existed still names the same worktree."""
    legacy, current = _identity(LEGACY_ROOT), _identity(CURRENT_ROOT)
    assert object_key_base(CURRENT_ROOT) == LEGACY_ROOT
    assert claim_worktrees_match("/repo", legacy, "/repo", current)
    assert claim_object_ids_comparable(legacy, current)


def test_two_generation_keys_must_agree_exactly() -> None:
    """Two keys that both carry a generation name one root only when equal."""
    first, other = _identity(CURRENT_ROOT), _identity("811:34c0020:0bad0bad")
    assert claim_worktrees_match("/repo", first, "/repo", _identity(CURRENT_ROOT))
    assert not claim_worktrees_match("/repo", first, "/repo", other)
    assert not claim_object_ids_comparable(first, other)
    assert not claim_worktrees_match("/repo", _identity("811:1"), "/repo", _identity("811:2"))


def test_a_legacy_claim_still_covers_and_conflicts_by_path() -> None:
    """Path object ids from two versions differ, so coverage and conflicts use the path."""
    legacy = _identity(LEGACY_ROOT, object_id="811:1")
    current = _identity(CURRENT_ROOT, object_id="811:1:77")
    assert claim_scopes_conflict(
        "/repo", ("CHANGELOG.md",), legacy, "/repo", ("CHANGELOG.md",), current
    )
    assert claim_scope_covers_path(
        "CHANGELOG.md",
        legacy.paths[0],
        "CHANGELOG.md",
        current.paths[0],
        case_sensitive=True,
        object_identity_safe=claim_object_ids_comparable(legacy, current),
    )


def _claim(seq: int, task: str, owner: str, identity: ClaimScopeIdentity) -> StoredEvent:
    return StoredEvent(
        seq=seq,
        ts=float(seq),
        kind=EventKind.CLAIM,
        payload={
            "task_id": task,
            "owner": owner,
            "status": "claimed",
            "paths": ["CHANGELOG.md"],
            "worktree": "/repo",
            "path_identity": identity.as_dict(),
        },
    )


def test_the_release_index_links_a_legacy_release_to_a_current_claim() -> None:
    """A release made by an older client is found for a newer client's claim."""
    release = StoredEvent(seq=2, ts=2.0, kind=EventKind.RELEASE, payload={"task_id": "A"})
    graph = build_causal_graph(
        (
            _claim(1, "A", "alice", _identity(LEGACY_ROOT, "811:1")),
            release,
            _claim(3, "B", "bob", _identity(CURRENT_ROOT, "811:1:77")),
        )
    )
    contention = [edge for edge in graph.edges if edge.relation == CONTENTION]
    assert [(edge.src, edge.dst) for edge in contention] == [(2, 3)]
