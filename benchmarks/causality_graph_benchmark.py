# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — causality graph build time with indexed contention edges
"""Measure ``build_causal_graph`` against an exhaustive contention scan.

A seeded synthetic claim/release log spreads claims over several worktrees and
mixes legacy display scopes with canonical identities. The production graph
build files releases by worktree; the baseline compares every new claim with
every earlier release through the same exact predicate. Both must produce the
same contention edges or the run fails. Times are single-host wall-clock
measurements of this fixture, not a production throughput claim.
"""

from __future__ import annotations

import argparse
import json
import platform
import random
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from synapse_channel.core.causality import CONTENTION, CausalNode, build_causal_graph
from synapse_channel.core.journal import EventKind
from synapse_channel.core.path_identity import (
    CanonicalPathIdentity,
    ClaimScopeIdentity,
    claim_scopes_conflict,
    comparison_path,
    comparison_worktree,
)
from synapse_channel.core.persistence import StoredEvent
from synapse_channel.core.replay import SNAPSHOT_KINDS

PATHS = ("src", "src/a.py", "src/b.py", "docs/guide.md", "tests/test_a.py")


def synthetic_log(claims: int, worktrees: int, seed: int) -> list[StoredEvent]:
    """Return a seeded log with ``claims`` claims, each followed by its release.

    Parameters
    ----------
    claims : int
        Positive number of claims.
    worktrees : int
        Positive number of distinct worktrees the claims spread over.
    seed : int
        Seed of the deterministic generator.

    Returns
    -------
    list of StoredEvent
        Claim and release events in sequence order.

    Raises
    ------
    ValueError
        If ``claims`` or ``worktrees`` is not positive.
    """
    if claims < 1 or worktrees < 1:
        raise ValueError("claims and worktrees must be positive")
    rng = random.Random(seed)
    events: list[StoredEvent] = []
    seq = 0
    for index in range(claims):
        worktree = f"/work/repo-{rng.randrange(worktrees)}"
        paths = rng.sample(PATHS, rng.randint(0, 2))
        payload: dict[str, object] = {
            "task_id": f"T{index}",
            "owner": f"agent-{rng.randrange(8)}",
            "status": "claimed",
            "paths": paths,
            "worktree": worktree,
        }
        if rng.random() < 0.5:
            payload["path_identity"] = ClaimScopeIdentity(
                worktree_path=comparison_worktree(worktree, case_sensitive=True),
                case_sensitive=True,
                paths=tuple(
                    CanonicalPathIdentity(
                        comparison_path(path, case_sensitive=True),
                        comparison_path(path, case_sensitive=True),
                    )
                    for path in paths
                ),
            ).as_dict()
        seq += 1
        events.append(StoredEvent(seq=seq, ts=float(seq), kind=EventKind.CLAIM, payload=payload))
        seq += 1
        events.append(
            StoredEvent(
                seq=seq, ts=float(seq), kind=EventKind.RELEASE, payload={"task_id": f"T{index}"}
            )
        )
    return events


def exhaustive_contention(nodes: Sequence[CausalNode]) -> set[tuple[int, int]]:
    """Return contention pairs by comparing each fresh claim with every release.

    Parameters
    ----------
    nodes : Sequence[CausalNode]
        Graph nodes in sequence order.

    Returns
    -------
    set of tuple of int
        ``(release_seq, claim_seq)`` for the latest contending release.
    """
    live: dict[str, CausalNode] = {}
    released: list[tuple[int, CausalNode]] = []
    pairs: set[tuple[int, int]] = set()
    for node in nodes:
        if node.kind == EventKind.RELEASE:
            held = live.pop(node.task_id, None)
            if held is not None:
                released.append((node.seq, held))
            continue
        if node.kind not in SNAPSHOT_KINDS:
            continue
        if node.task_id not in live:
            latest = None
            for release_seq, freed in released:
                if freed.owner != node.owner and claim_scopes_conflict(
                    freed.worktree,
                    freed.paths,
                    freed.path_identity,
                    node.worktree,
                    node.paths,
                    node.path_identity,
                ):
                    latest = release_seq
            if latest is not None:
                pairs.add((latest, node.seq))
        live[node.task_id] = node
    return pairs


def measure(
    claims: int,
    worktrees: int,
    seed: int,
    reference: Callable[[Sequence[CausalNode]], set[tuple[int, int]]] = exhaustive_contention,
) -> dict[str, Any]:
    """Time the indexed graph build and the exhaustive scan on one fixture.

    Parameters
    ----------
    claims : int
        Number of claims in the synthetic log.
    worktrees : int
        Number of worktrees the claims spread over.
    seed : int
        Generator seed.
    reference : callable, optional
        The baseline that derives contention pairs from the graph nodes;
        defaults to :func:`exhaustive_contention`.

    Returns
    -------
    dict
        Fixture size, contention edge count and both wall-clock times.

    Raises
    ------
    RuntimeError
        If the indexed build and the exhaustive scan disagree on any edge.
    """
    events = synthetic_log(claims, worktrees, seed)
    start = time.perf_counter()
    graph = build_causal_graph(events)
    indexed_s = time.perf_counter() - start
    found = {(edge.src, edge.dst) for edge in graph.edges if edge.relation == CONTENTION}
    start = time.perf_counter()
    expected = reference(graph.nodes)
    exhaustive_s = time.perf_counter() - start
    if found != expected:
        raise RuntimeError("indexed contention edges differ from the exhaustive scan")
    return {
        "claims": claims,
        "worktrees": worktrees,
        "seed": seed,
        "events": len(events),
        "contention_edges": len(found),
        "indexed_build_s": round(indexed_s, 4),
        "exhaustive_scan_s": round(exhaustive_s, 4),
    }


def main(argv: list[str] | None = None) -> int:
    """Run the fixture sizes, print the JSON and optionally write it.

    Parameters
    ----------
    argv : list of str or None, optional
        Explicit CLI arguments, or the process arguments.

    Returns
    -------
    int
        Zero after every size matches the exhaustive scan.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--claims", type=int, nargs="+", default=[1000, 4000, 8000])
    parser.add_argument("--worktrees", type=int, default=40)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--results", type=Path, default=None)
    args = parser.parse_args(argv)
    runs = [measure(count, args.worktrees, args.seed) for count in args.claims]
    report = {
        "benchmark": "causality_graph",
        "method": (
            "seeded synthetic claim/release log; build_causal_graph with indexed "
            "contention edges versus an exhaustive scan through claim_scopes_conflict; "
            "single wall-clock run per size; edges must be identical"
        ),
        "python": platform.python_version(),
        "machine": platform.machine(),
        "runs": runs,
    }
    document = json.dumps(report, indent=2) + "\n"
    print(document, end="")
    if args.results is not None:
        args.results.write_text(document, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
