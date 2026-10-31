# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — indexed contention edges equal the exhaustive scan
"""The release index finds exactly the contention edges an exhaustive scan finds."""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Sequence

from synapse_channel.core.causality import CONTENTION, CausalNode, build_causal_graph
from synapse_channel.core.journal import EventKind
from synapse_channel.core.path_identity import (
    CanonicalPathIdentity,
    ClaimScopeIdentity,
    claim_scopes_conflict,
    claim_worktrees_match,
    comparison_path,
    comparison_worktree,
)
from synapse_channel.core.persistence import StoredEvent
from synapse_channel.core.replay import SNAPSHOT_KINDS

WORKTREES = (
    "repo",
    "Repo",
    "C:\\Work\\Repo",
    "c:/work/repo",
    "e\u0301t",
    "\u00e9t",
    "/srv/x",
    "/srv/X",
)
PATHS = ("src", "src/a.py", "SRC/a.py", "docs/r.md", "src/b.py")
OWNERS = ("alice", "bob", "carol")
TASKS = ("A", "B", "C", "D", "E", "F")


def _identity(rng: random.Random, worktree: str, paths: Sequence[str]) -> dict[str, object]:
    """Return a valid identity for the display scope, with random object keys."""
    case_sensitive = rng.random() < 0.5
    return ClaimScopeIdentity(
        worktree_path=comparison_worktree(worktree, case_sensitive=case_sensitive),
        case_sensitive=case_sensitive,
        worktree_object_id=rng.choice(("", "root:1", "root:2")),
        filesystem_namespace=rng.choice(("", "host:1")),
        paths=tuple(
            CanonicalPathIdentity(
                git_path=comparison_path(path, case_sensitive=case_sensitive),
                filesystem_path=comparison_path(path, case_sensitive=case_sensitive),
                object_id=rng.choice(("", "1:2", "1:3")),
            )
            for path in paths
        ),
    ).as_dict()


def _random_log(rng: random.Random, length: int) -> list[StoredEvent]:
    """Return a claim/release log mixing legacy and identity-bearing scopes."""
    events: list[StoredEvent] = []
    for seq in range(1, length + 1):
        task = rng.choice(TASKS)
        if rng.random() < 0.35:
            events.append(
                StoredEvent(
                    seq=seq, ts=float(seq), kind=EventKind.RELEASE, payload={"task_id": task}
                )
            )
            continue
        worktree = rng.choice(WORKTREES)
        paths = rng.sample(PATHS, rng.randint(0, 2))
        payload: dict[str, object] = {
            "task_id": task,
            "owner": rng.choice(OWNERS),
            "status": "claimed",
            "paths": paths,
            "worktree": worktree,
        }
        if rng.random() < 0.55:
            payload["path_identity"] = _identity(rng, worktree, paths)
        events.append(StoredEvent(seq=seq, ts=float(seq), kind=EventKind.CLAIM, payload=payload))
    return events


def _exhaustive_contention(nodes: Sequence[CausalNode]) -> dict[tuple[int, int], str]:
    """Return every contention pair by scanning all earlier releases, with its kind."""
    live: dict[str, CausalNode] = {}
    released: list[tuple[int, CausalNode]] = []
    pairs: dict[tuple[int, int], str] = {}
    for node in nodes:
        if node.kind == EventKind.RELEASE:
            held = live.pop(node.task_id, None)
            if held is not None:
                released.append((node.seq, held))
            continue
        if node.kind not in SNAPSHOT_KINDS:
            continue
        if node.task_id not in live:
            latest: tuple[int, CausalNode] | None = None
            for release_seq, freed in released:
                if freed.owner != node.owner and claim_scopes_conflict(
                    freed.worktree,
                    freed.paths,
                    freed.path_identity,
                    node.worktree,
                    node.paths,
                    node.path_identity,
                ):
                    latest = (release_seq, freed)
            if latest is not None:
                pairs[(latest[0], node.seq)] = _kind(latest[1], node)
        live[node.task_id] = node
    return pairs


def _kind(freed: CausalNode, claim: CausalNode) -> str:
    """Name the match that let two scopes contend."""
    legacy = (freed.path_identity is None, claim.path_identity is None)
    if legacy == (True, True):
        return "legacy"
    if legacy[0] != legacy[1]:
        return "mixed"
    if claim_worktrees_match(
        freed.worktree, freed.path_identity, claim.worktree, claim.path_identity
    ):
        return "identity"
    return "object"


def _both_contentions(
    events: list[StoredEvent],
) -> tuple[set[tuple[int, int]], dict[tuple[int, int], str]]:
    """Return the graph's contention pairs and the exhaustive scan's pairs."""
    graph = build_causal_graph(events)
    found = {(edge.src, edge.dst) for edge in graph.edges if edge.relation == CONTENTION}
    return found, _exhaustive_contention(graph.nodes)


def test_indexed_edges_equal_the_exhaustive_scan_on_mixed_logs() -> None:
    """Across mixed legacy and identity logs, every contention edge matches."""
    kinds: Counter[str] = Counter()
    for seed in range(300):
        found, expected = _both_contentions(_random_log(random.Random(seed), 60))
        assert found == set(expected), f"seed {seed}"
        kinds.update(expected.values())
    assert {"legacy", "mixed", "identity", "object"} <= set(kinds), kinds
    assert min(kinds.values()) >= 10, kinds


def _claim(
    seq: int, task: str, owner: str, worktree: str, identity: ClaimScopeIdentity | None
) -> StoredEvent:
    """Return one claim on ``src/a.py``, with or without an identity."""
    payload: dict[str, object] = {
        "task_id": task,
        "owner": owner,
        "status": "claimed",
        "paths": ["src/a.py"],
        "worktree": worktree,
    }
    if identity is not None:
        payload["path_identity"] = identity.as_dict()
    return StoredEvent(seq=seq, ts=float(seq), kind=EventKind.CLAIM, payload=payload)


def _release(seq: int, task: str) -> StoredEvent:
    """Return one release of ``task``."""
    return StoredEvent(seq=seq, ts=float(seq), kind=EventKind.RELEASE, payload={"task_id": task})


def _scope(
    worktree: str, *, case_sensitive: bool, root: str = "", host: str = ""
) -> ClaimScopeIdentity:
    """Return a valid identity for ``src/a.py`` in ``worktree``."""
    return ClaimScopeIdentity(
        worktree_path=comparison_worktree(worktree, case_sensitive=case_sensitive),
        case_sensitive=case_sensitive,
        worktree_object_id=root,
        filesystem_namespace=host,
        paths=(
            CanonicalPathIdentity(
                comparison_path("src/a.py", case_sensitive=case_sensitive),
                comparison_path("src/a.py", case_sensitive=case_sensitive),
            ),
        ),
    )


def _contention(events: list[StoredEvent]) -> set[tuple[int, int]]:
    """Return the contention pairs of the graph built from ``events``."""
    graph = build_causal_graph(events)
    return {(edge.src, edge.dst) for edge in graph.edges if edge.relation == CONTENTION}


def test_a_legacy_claim_finds_a_case_insensitive_identity_release() -> None:
    """A legacy display matches an identity path under the identity's case policy."""
    events = [
        _claim(1, "A", "alice", "Repo", _scope("Repo", case_sensitive=False)),
        _release(2, "A"),
        _claim(3, "B", "bob", "REPO", None),
    ]
    assert _contention(events) == {(2, 3)}


def test_an_identity_claim_finds_a_legacy_release() -> None:
    """An identity claim matches a legacy release labelled under its own policy."""
    events = [
        _claim(1, "A", "alice", "REPO", None),
        _release(2, "A"),
        _claim(3, "B", "bob", "repo", _scope("repo", case_sensitive=False)),
    ]
    assert _contention(events) == {(2, 3)}


def test_one_object_identity_contends_across_worktree_labels_once() -> None:
    """A release filed under its label and its object key yields one edge, the latest."""
    events = [
        _claim(1, "A", "alice", "one", _scope("one", case_sensitive=True, root="r:1", host="h:1")),
        _release(2, "A"),
        _claim(3, "C", "carol", "two", _scope("two", case_sensitive=True, root="r:1", host="h:1")),
        _release(4, "C"),
        _claim(5, "B", "bob", "two", _scope("two", case_sensitive=True, root="r:1", host="h:1")),
    ]
    assert _contention(events) == {(2, 3), (4, 5)}


def test_unrelated_worktrees_never_contend() -> None:
    """Releases in other worktrees and by the same owner are never chosen."""
    events = [
        _claim(1, "A", "alice", "one", None),
        _release(2, "A"),
        _claim(3, "C", "bob", "two", _scope("two", case_sensitive=True)),
        _release(4, "C"),
        _claim(5, "B", "bob", "two", None),
    ]
    assert _contention(events) == set()
