# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — cross-repository dependency graph joined with live claims
"""Cross-repository dependency graph for organisation-wide claim coordination.

The graph composes the per-repository scans of
:mod:`~synapse_channel.core.repo_manifests` into typed edges between
repository nodes: a ``dependency`` edge where one repository's manifest names
a package another repository declares, and a ``shared_owner`` edge where two
repositories cite the same CODEOWNERS handle. Joined with the live claims of
a hub event log — a claim's ``worktree`` is the repository identity — it
answers the coordination question a single-repository view cannot: *who is
working right now in a repository mine depends on, or one that depends on
mine?*

Where two scanned repositories declare version constraints on the same
package that are **provably** disjoint (per
:mod:`~synapse_channel.core.version_constraints`), the graph adds a
``version_conflict`` edge between them — a standing declaration-level fact,
found before any resolver or CI run trips over it. A constraint the bounded
model cannot compare never claims a conflict.

The join is advisory evidence, exactly like the in-repository contention
surfaces: it names the live claims and the manifest lines that connect the
repositories, and decides nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore, StoredEvent
from synapse_channel.core.reliability import SNAPSHOT_KINDS
from synapse_channel.core.repo_manifests import (
    RepoManifest,
    discover_repositories,
    read_repo_manifest,
)
from synapse_channel.core.version_constraints import CONFLICT, compare_constraints

DEPENDENCY_EDGE = "dependency"
SHARED_OWNER_EDGE = "shared_owner"
VERSION_CONFLICT_EDGE = "version_conflict"

DEPENDS_ON_RELATION = "depends_on"
DEPENDENCY_OF_RELATION = "dependency_of"
SELF_RELATION = "self"


@dataclass(frozen=True)
class CrossRepoNode:
    """One repository in the organisation-wide graph.

    Attributes
    ----------
    repo : str
        Repository identity (checkout directory name, the claim ``worktree``).
    path : str
        Absolute checkout path the scan read.
    packages : tuple[str, ...]
        ``ecosystem:name`` package identities the repository provides.
    owners : tuple[str, ...]
        CODEOWNERS handles the repository cites.
    """

    repo: str
    path: str
    packages: tuple[str, ...]
    owners: tuple[str, ...]


@dataclass(frozen=True)
class CrossRepoEdge:
    """One typed relationship between two repositories.

    Attributes
    ----------
    source, target : str
        Repository names. A ``dependency`` edge points from the consuming
        repository to the providing one; ``shared_owner`` and
        ``version_conflict`` edges are undirected and stored with the pair
        sorted.
    kind : str
        ``dependency``, ``shared_owner``, or ``version_conflict``.
    detail : str
        Human-readable line naming the connecting evidence.
    evidence : dict[str, Any]
        Machine-readable fields: the dependency name, ecosystem, manifest,
        and declared constraint for a dependency edge; the shared handles
        for an owner edge; both sides' constraints and manifests for a
        version-conflict edge.
    """

    source: str
    target: str
    kind: str
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CrossRepoClaimSignal:
    """One live claim joined to the graph around a focus repository.

    Attributes
    ----------
    repo : str
        Repository the claim lives in (its ``worktree``).
    relation : str
        ``self`` (the focus repository itself), ``depends_on`` (the focus
        repository depends on ``repo``), or ``dependency_of`` (``repo``
        depends on the focus repository).
    task_id, owner : str
        The claim's task and holder.
    paths : tuple[str, ...]
        Declared file scope of the claim.
    seq : int
        Durable event-log sequence of the latest claim snapshot.
    ts : float
        Event timestamp.
    """

    repo: str
    relation: str
    task_id: str
    owner: str
    paths: tuple[str, ...]
    seq: int
    ts: float


@dataclass(frozen=True)
class CrossRepoGraph:
    """The scanned dependency graph, its problems, and any joined claims."""

    root: str
    nodes: tuple[CrossRepoNode, ...]
    edges: tuple[CrossRepoEdge, ...]
    problems: tuple[str, ...]
    claims: tuple[CrossRepoClaimSignal, ...] = ()
    focus: str | None = None


def scan_repositories(root: Path) -> tuple[RepoManifest, ...]:
    """Scan every repository under ``root`` into manifest records.

    Parameters
    ----------
    root : pathlib.Path
        A directory of repository checkouts (each immediate subdirectory
        holding a recognised manifest, CODEOWNERS, or ``.git`` qualifies).

    Raises
    ------
    ValueError
        If ``root`` is not an existing directory.
    """
    if not root.is_dir():
        msg = f"missing repository root: {root}"
        raise ValueError(msg)
    return tuple(read_repo_manifest(path) for path in discover_repositories(root))


def _version_conflict_edges(manifests: tuple[RepoManifest, ...]) -> list[CrossRepoEdge]:
    """Return one edge per repository pair whose constraints provably conflict.

    Every package consumed by two or more scanned repositories — provided by
    a scanned repository or external — is checked pairwise; an edge appears
    only for the :data:`~synapse_channel.core.version_constraints.CONFLICT`
    verdict, so a constraint the bounded model cannot compare stays silent.
    """
    consumers: dict[tuple[str, str], list[tuple[str, Any]]] = {}
    for manifest in manifests:
        for dependency in manifest.dependencies:
            consumers.setdefault((dependency.ecosystem, dependency.name), []).append(
                (manifest.repo, dependency)
            )
    edges: list[CrossRepoEdge] = []
    for (ecosystem, package), declarations in sorted(consumers.items()):
        for index, left_declaration in enumerate(declarations):
            for right_declaration in declarations[index + 1 :]:
                first, second = sorted(
                    (left_declaration, right_declaration), key=lambda item: item[0]
                )
                first_repo, first_dependency = first
                second_repo, second_dependency = second
                verdict = compare_constraints(
                    first_dependency.constraint, second_dependency.constraint, ecosystem
                )
                if verdict != CONFLICT:
                    continue
                edges.append(
                    CrossRepoEdge(
                        source=first_repo,
                        target=second_repo,
                        kind=VERSION_CONFLICT_EDGE,
                        detail=(
                            f"{first_repo} pins {package} '{first_dependency.constraint}' but "
                            f"{second_repo} pins '{second_dependency.constraint}' ({ecosystem})"
                        ),
                        evidence={
                            "package": package,
                            "ecosystem": ecosystem,
                            "left_repo": first_repo,
                            "left_constraint": first_dependency.constraint,
                            "left_manifest": first_dependency.manifest,
                            "right_repo": second_repo,
                            "right_constraint": second_dependency.constraint,
                            "right_manifest": second_dependency.manifest,
                        },
                    )
                )
    return edges


def build_cross_repo_graph(root: Path, manifests: tuple[RepoManifest, ...]) -> CrossRepoGraph:
    """Compose per-repository manifests into the typed dependency graph.

    Dependency edges connect a consuming repository to the repository that
    declares the consumed package name within the same ecosystem; a package
    name provided by no scanned repository creates no edge (external
    dependencies stay out of the graph). Shared-owner edges connect each
    sorted pair of repositories citing a common CODEOWNERS handle.
    Version-conflict edges connect each sorted pair of repositories whose
    declared constraints on the same package — external packages included —
    are provably disjoint.
    """
    providers: dict[tuple[str, str], str] = {}
    for manifest in manifests:
        for package in manifest.packages:
            providers.setdefault((package.ecosystem, package.name), manifest.repo)

    edges: list[CrossRepoEdge] = []
    for manifest in manifests:
        for dependency in manifest.dependencies:
            provider = providers.get((dependency.ecosystem, dependency.name))
            if provider is None or provider == manifest.repo:
                continue
            edges.append(
                CrossRepoEdge(
                    source=manifest.repo,
                    target=provider,
                    kind=DEPENDENCY_EDGE,
                    detail=(
                        f"{manifest.repo} depends on {dependency.name} "
                        f"({dependency.ecosystem}) provided by {provider}"
                    ),
                    evidence={
                        "dependency": dependency.name,
                        "ecosystem": dependency.ecosystem,
                        "manifest": dependency.manifest,
                        "constraint": dependency.constraint,
                    },
                )
            )
    edges.extend(_version_conflict_edges(manifests))

    owner_index: dict[str, list[str]] = {}
    for manifest in manifests:
        for owner in manifest.owners:
            owner_index.setdefault(owner, []).append(manifest.repo)
    shared: dict[tuple[str, str], list[str]] = {}
    for owner, repos in owner_index.items():
        for index, left in enumerate(repos):
            for right in repos[index + 1 :]:
                pair = (min(left, right), max(left, right))
                shared.setdefault(pair, []).append(owner)
    for (left, right), owners in sorted(shared.items()):
        edges.append(
            CrossRepoEdge(
                source=left,
                target=right,
                kind=SHARED_OWNER_EDGE,
                detail=f"{left} and {right} share owner(s) {', '.join(sorted(owners))}",
                evidence={"owners": sorted(owners)},
            )
        )

    nodes = tuple(
        CrossRepoNode(
            repo=manifest.repo,
            path=manifest.path,
            packages=tuple(f"{package.ecosystem}:{package.name}" for package in manifest.packages),
            owners=manifest.owners,
        )
        for manifest in manifests
    )
    problems = tuple(
        f"{manifest.repo}: {problem}" for manifest in manifests for problem in manifest.problems
    )
    edges.sort(key=lambda edge: (edge.kind, edge.source, edge.target, edge.detail))
    return CrossRepoGraph(root=str(root), nodes=nodes, edges=tuple(edges), problems=problems)


def live_claims(events: list[StoredEvent]) -> tuple[StoredEvent, ...]:
    """Return the latest unreleased claim snapshot per task, in log order.

    The reconstruction mirrors the reliability layer: events whose kind is a
    task-claim snapshot supersede earlier snapshots of the same task, and a
    ``release`` removes the task from the live set.
    """
    live: dict[str, StoredEvent] = {}
    for event in events:
        task_id = str(event.payload.get("task_id", "")).strip()
        if not task_id:
            continue
        if event.kind == EventKind.RELEASE:
            live.pop(task_id, None)
        elif event.kind in SNAPSHOT_KINDS:
            live[task_id] = event
    return tuple(sorted(live.values(), key=lambda event: event.seq))


def _neighbour_relations(graph: CrossRepoGraph, focus: str) -> dict[str, str]:
    """Return the dependency relation of every repository around ``focus``."""
    relations: dict[str, str] = {focus: SELF_RELATION}
    for edge in graph.edges:
        if edge.kind != DEPENDENCY_EDGE:
            continue
        if edge.source == focus:
            relations.setdefault(edge.target, DEPENDS_ON_RELATION)
        elif edge.target == focus:
            relations.setdefault(edge.source, DEPENDENCY_OF_RELATION)
    return relations


def join_claims(
    graph: CrossRepoGraph,
    events: list[StoredEvent],
    *,
    focus: str | None = None,
) -> CrossRepoGraph:
    """Join live claims from a hub event log onto the graph.

    Parameters
    ----------
    graph : CrossRepoGraph
        The scanned dependency graph.
    events : list[StoredEvent]
        Durable events read from an :class:`EventStore`.
    focus : str or None, optional
        With a focus repository, keep only claims in the focus itself and in
        repositories connected to it by a dependency edge, labelled by their
        relation. Without one, keep every claim whose ``worktree`` is a
        scanned repository, labelled ``self``.

    Raises
    ------
    ValueError
        If ``focus`` names a repository the scan did not find.
    """
    known = {node.repo for node in graph.nodes}
    if focus is not None and focus not in known:
        msg = f"unknown repository: {focus} (scanned: {', '.join(sorted(known)) or 'none'})"
        raise ValueError(msg)
    relations = _neighbour_relations(graph, focus) if focus is not None else None
    signals: list[CrossRepoClaimSignal] = []
    for event in live_claims(events):
        worktree = str(event.payload.get("worktree", "")).strip()
        if relations is None:
            if worktree not in known:
                continue
            relation = SELF_RELATION
        else:
            found = relations.get(worktree)
            if found is None:
                continue
            relation = found
        raw_paths = event.payload.get("paths")
        paths = tuple(str(item) for item in raw_paths) if isinstance(raw_paths, list) else ()
        signals.append(
            CrossRepoClaimSignal(
                repo=worktree,
                relation=relation,
                task_id=str(event.payload.get("task_id", "")),
                owner=str(event.payload.get("owner", "")),
                paths=paths,
                seq=event.seq,
                ts=event.ts,
            )
        )
    return replace(graph, claims=tuple(signals), focus=focus)


def run_cross_repo_graph(
    root: str | Path,
    *,
    db_path: str | Path | None = None,
    focus: str | None = None,
    key_file: str | Path | None = None,
) -> CrossRepoGraph:
    """Scan ``root``, build the graph, and join claims when a log is given.

    Parameters
    ----------
    root : str or pathlib.Path
        Directory of repository checkouts to scan.
    db_path : str or pathlib.Path or None, optional
        Hub event store used to join live claims.
    focus : str or None, optional
        Focus repository for claim-relation scoring.
    key_file : str or pathlib.Path or None, optional
        Owner-only SQLCipher key for an encrypted event store.

    Raises
    ------
    ValueError
        If the root directory or the event store is missing, or ``focus``
        names an unscanned repository.
    """
    root_path = Path(root)
    graph = build_cross_repo_graph(root_path, scan_repositories(root_path))
    if db_path is None:
        if focus is not None:
            return join_claims(graph, [], focus=focus)
        return graph
    store_path = Path(db_path)
    if not store_path.exists():
        msg = f"missing event store: {store_path}"
        raise ValueError(msg)
    store = EventStore(store_path, key_file=key_file)
    try:
        events = list(store.read_all())
    finally:
        store.close()
    return join_claims(graph, events, focus=focus)


def cross_repo_graph_to_json(graph: CrossRepoGraph) -> dict[str, object]:
    """Return a stable JSON-compatible representation of the graph."""
    return {
        "root": graph.root,
        "focus": graph.focus,
        "nodes": [
            {
                "repo": node.repo,
                "path": node.path,
                "packages": list(node.packages),
                "owners": list(node.owners),
            }
            for node in graph.nodes
        ],
        "edges": [
            {
                "source": edge.source,
                "target": edge.target,
                "kind": edge.kind,
                "detail": edge.detail,
                "evidence": edge.evidence,
            }
            for edge in graph.edges
        ],
        "claims": [
            {
                "repo": claim.repo,
                "relation": claim.relation,
                "task_id": claim.task_id,
                "owner": claim.owner,
                "paths": list(claim.paths),
                "seq": claim.seq,
                "ts": claim.ts,
            }
            for claim in graph.claims
        ],
        "problems": list(graph.problems),
        "note": "declaration-level dependency evidence; advisory, not enforcement",
    }


def _dot_quote(text: str) -> str:
    """Return ``text`` as a quoted DOT string literal."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render_cross_repo_dot(graph: CrossRepoGraph) -> str:
    """Render the graph as a Graphviz ``digraph``.

    Repositories are boxes (the focus, when set, double-bordered); a
    dependency edge points from the consumer to the provider; a shared-owner
    edge is dashed and undirected; a version-conflict edge is red and
    undirected. A repository holding live claims carries the claim count in
    its label.
    """
    claims_by_repo: dict[str, int] = {}
    for claim in graph.claims:
        claims_by_repo[claim.repo] = claims_by_repo.get(claim.repo, 0) + 1
    lines = ["digraph cross_repo {", "  rankdir=LR;"]
    for node in graph.nodes:
        label = node.repo
        count = claims_by_repo.get(node.repo, 0)
        if count:
            label = f"{node.repo} ({count} live claim{'s' if count != 1 else ''})"
        shape = "box"
        if node.repo == graph.focus:
            lines.append(
                f"  {_dot_quote(node.repo)} "
                f"[label={_dot_quote(label)}, shape={shape}, peripheries=2];"
            )
        else:
            lines.append(f"  {_dot_quote(node.repo)} [label={_dot_quote(label)}, shape={shape}];")
    for edge in graph.edges:
        attributes = f"label={_dot_quote(edge.kind)}"
        if edge.kind == SHARED_OWNER_EDGE:
            attributes += ", dir=none, style=dashed"
        elif edge.kind == VERSION_CONFLICT_EDGE:
            attributes += ", dir=none, color=red"
        lines.append(f"  {_dot_quote(edge.source)} -> {_dot_quote(edge.target)} [{attributes}];")
    lines.append("}")
    return "\n".join(lines)


def render_cross_repo_human(graph: CrossRepoGraph) -> str:
    """Render the graph as compact terminal text."""
    lines = [
        "Cross-repository dependency graph: declaration-level evidence, advisory only",
        f"root={graph.root} repositories={len(graph.nodes)} edges={len(graph.edges)}",
    ]
    if graph.focus is not None:
        lines[1] += f" focus={graph.focus}"
    if not graph.nodes:
        lines.append("")
        lines.append("No repositories with dependency manifests found.")
        return "\n".join(lines)
    if graph.edges:
        lines.append("")
        lines.extend(
            f"{edge.source} -[{edge.kind}]-> {edge.target}: {edge.detail}" for edge in graph.edges
        )
    if graph.claims:
        lines.append("")
        lines.append("Live claims")
        for claim in graph.claims:
            scope = f" paths={','.join(claim.paths)}" if claim.paths else ""
            lines.append(
                f"{claim.repo} [{claim.relation}] {claim.task_id}@{claim.owner} "
                f"seq={claim.seq}{scope}"
            )
    if graph.problems:
        lines.append("")
        lines.append("Problems")
        lines.extend(f"{problem}" for problem in graph.problems)
    return "\n".join(lines)
