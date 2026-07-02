# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — cross-repository dependency graph regressions

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel.core.cross_repo_graph import (
    CrossRepoGraph,
    build_cross_repo_graph,
    cross_repo_graph_to_json,
    join_claims,
    live_claims,
    render_cross_repo_dot,
    render_cross_repo_human,
    run_cross_repo_graph,
    scan_repositories,
)
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.state import TaskClaim


def _write(repo: Path, relative: str, content: str) -> None:
    target = repo / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _org(tmp_path: Path) -> Path:
    """Create a three-repository organisation tree with real manifests."""
    root = tmp_path / "org"
    root.mkdir(parents=True)
    provider = root / "provider"
    provider.mkdir()
    _write(provider, "pyproject.toml", '[project]\nname = "provider-pkg"\ndependencies = []\n')
    _write(provider, ".github/CODEOWNERS", "* @org/platform\n")
    consumer = root / "consumer"
    consumer.mkdir()
    _write(
        consumer,
        "pyproject.toml",
        '[project]\nname = "consumer-pkg"\ndependencies = ["provider-pkg>=1", "requests"]\n',
    )
    _write(consumer, "CODEOWNERS", "* @org/platform @solo\n")
    island = root / "island"
    island.mkdir()
    _write(island, "go.mod", "module example.com/island\n")
    return root


def _graph(tmp_path: Path) -> CrossRepoGraph:
    root = _org(tmp_path)
    return build_cross_repo_graph(root, scan_repositories(root))


def _claim(task_id: str, worktree: str, *, owner: str = "agent-a") -> dict[str, object]:
    return TaskClaim(
        task_id=task_id,
        owner=owner,
        note="work",
        claimed_at=1.0,
        lease_expires_at=900.0,
        status="claimed",
        data_ref="",
        worktree=worktree,
        paths=("src/thing.py",),
        epoch=1,
        checkpoint="",
    ).as_dict()


def _seed_claims(db: Path) -> None:
    store = EventStore(db)
    store.append(EventKind.CLAIM, _claim("PROV-1", "provider"), ts=1.0, durable=True)
    store.append(
        EventKind.CLAIM, _claim("CONS-1", "consumer", owner="agent-b"), ts=2.0, durable=True
    )
    store.append(EventKind.CLAIM, _claim("GONE-1", "provider"), ts=3.0, durable=True)
    store.append(EventKind.RELEASE, {"task_id": "GONE-1"}, ts=4.0, durable=True)
    store.append(EventKind.CLAIM, _claim("ELSE-1", "unscanned-repo"), ts=5.0, durable=True)
    store.close()


def test_dependency_edges_connect_consumer_to_provider(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    dependency_edges = [edge for edge in graph.edges if edge.kind == "dependency"]
    assert len(dependency_edges) == 1
    (edge,) = dependency_edges
    assert (edge.source, edge.target) == ("consumer", "provider")
    assert edge.evidence == {
        "dependency": "provider-pkg",
        "ecosystem": "python",
        "manifest": "pyproject.toml",
    }
    # "requests" has no scanned provider: external dependencies create no edge.
    assert all(edge.evidence.get("dependency") != "requests" for edge in graph.edges)


def test_shared_owner_edges_pair_repositories_once(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    owner_edges = [edge for edge in graph.edges if edge.kind == "shared_owner"]
    assert len(owner_edges) == 1
    (edge,) = owner_edges
    assert (edge.source, edge.target) == ("consumer", "provider")
    assert edge.evidence == {"owners": ["@org/platform"]}


def test_self_dependency_creates_no_edge(tmp_path: Path) -> None:
    root = tmp_path / "org"
    root.mkdir()
    solo = root / "solo"
    solo.mkdir()
    _write(solo, "pyproject.toml", '[project]\nname = "solo"\ndependencies = ["solo"]\n')
    graph = build_cross_repo_graph(root, scan_repositories(root))
    assert graph.edges == ()


def test_nodes_carry_packages_owners_and_problems_aggregate(tmp_path: Path) -> None:
    root = _org(tmp_path)
    broken = root / "broken"
    broken.mkdir()
    _write(broken, "package.json", "{nope")
    graph = build_cross_repo_graph(root, scan_repositories(root))
    by_repo = {node.repo: node for node in graph.nodes}
    assert by_repo["provider"].packages == ("python:provider-pkg",)
    assert by_repo["island"].packages == ("go:example.com/island",)
    assert by_repo["consumer"].owners == ("@org/platform", "@solo")
    assert len(graph.problems) == 1
    assert graph.problems[0].startswith("broken: package.json:")


def test_scan_repositories_requires_an_existing_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing repository root"):
        scan_repositories(tmp_path / "absent")


def test_live_claims_supersede_and_release(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    _seed_claims(db)
    store = EventStore(db)
    try:
        events = list(store.read_all())
    finally:
        store.close()
    claims = live_claims(events)
    assert [str(event.payload["task_id"]) for event in claims] == ["PROV-1", "CONS-1", "ELSE-1"]


def test_join_without_focus_keeps_only_scanned_worktrees(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    _seed_claims(db)
    graph = run_cross_repo_graph(_org(tmp_path), db_path=db)
    assert [(claim.repo, claim.relation) for claim in graph.claims] == [
        ("provider", "self"),
        ("consumer", "self"),
    ]


def test_join_with_focus_labels_dependency_relations(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    _seed_claims(db)
    graph = run_cross_repo_graph(_org(tmp_path), db_path=db, focus="consumer")
    assert [(claim.repo, claim.relation) for claim in graph.claims] == [
        ("provider", "depends_on"),
        ("consumer", "self"),
    ]
    reverse = run_cross_repo_graph(_org(tmp_path / "again"), db_path=db, focus="provider")
    assert [(claim.repo, claim.relation) for claim in reverse.claims] == [
        ("provider", "self"),
        ("consumer", "dependency_of"),
    ]


def test_join_with_unknown_focus_is_refused(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    with pytest.raises(ValueError, match="unknown repository: nonesuch"):
        join_claims(graph, [], focus="nonesuch")


def test_run_without_db_and_with_focus_sets_focus_and_no_claims(tmp_path: Path) -> None:
    graph = run_cross_repo_graph(_org(tmp_path), focus="consumer")
    assert graph.focus == "consumer"
    assert graph.claims == ()
    plain = run_cross_repo_graph(_org(tmp_path / "plain"))
    assert plain.focus is None
    assert plain.claims == ()


def test_run_with_missing_store_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing event store"):
        run_cross_repo_graph(_org(tmp_path), db_path=tmp_path / "absent.db")


def test_claim_paths_survive_malformed_payloads(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    store = EventStore(db)
    payload = _claim("ODD-1", "provider")
    payload["paths"] = "not-a-list"
    store.append(EventKind.CLAIM, payload, ts=1.0, durable=True)
    store.close()
    graph = run_cross_repo_graph(_org(tmp_path), db_path=db)
    (claim,) = graph.claims
    assert claim.paths == ()


def test_json_projection_is_stable_and_labelled(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    _seed_claims(db)
    graph = run_cross_repo_graph(_org(tmp_path), db_path=db, focus="consumer")
    payload = cross_repo_graph_to_json(graph)
    assert payload["note"] == "declaration-level dependency evidence; advisory, not enforcement"
    assert payload["focus"] == "consumer"
    edges = payload["edges"]
    claims = payload["claims"]
    assert isinstance(edges, list) and isinstance(claims, list)
    assert len(edges) == len(graph.edges)
    first_claim = claims[0]
    assert set(first_claim) == {"repo", "relation", "task_id", "owner", "paths", "seq", "ts"}


def test_dot_marks_focus_claim_counts_and_owner_edges(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    _seed_claims(db)
    graph = run_cross_repo_graph(_org(tmp_path), db_path=db, focus="consumer")
    dot = render_cross_repo_dot(graph)
    assert dot.startswith("digraph cross_repo {")
    assert dot.endswith("}")
    assert '"consumer" [label="consumer (1 live claim)", shape=box, peripheries=2];' in dot
    assert '"provider" [label="provider (1 live claim)", shape=box];' in dot
    assert '"island" [label="island", shape=box];' in dot
    assert "dir=none, style=dashed" in dot


def test_human_rendering_covers_edges_claims_and_problems(tmp_path: Path) -> None:
    root = _org(tmp_path)
    broken = root / "broken"
    broken.mkdir()
    _write(broken, "package.json", "{nope")
    db = tmp_path / "events.db"
    _seed_claims(db)
    graph = run_cross_repo_graph(root, db_path=db, focus="consumer")
    text = render_cross_repo_human(graph)
    assert text.startswith("Cross-repository dependency graph: declaration-level evidence")
    assert "focus=consumer" in text
    assert "consumer -[dependency]-> provider:" in text
    assert "provider [depends_on] PROV-1@agent-a seq=1 paths=src/thing.py" in text
    assert "Problems" in text
    assert "broken: package.json:" in text


def test_human_rendering_states_an_empty_scan_honestly(tmp_path: Path) -> None:
    root = tmp_path / "empty"
    root.mkdir()
    graph = run_cross_repo_graph(root)
    text = render_cross_repo_human(graph)
    assert "No repositories with dependency manifests found." in text


def test_human_rendering_of_an_edgeless_tree_lists_only_the_header(tmp_path: Path) -> None:
    root = tmp_path / "org"
    root.mkdir()
    island = root / "island"
    island.mkdir()
    _write(island, "go.mod", "module example.com/island\n")
    text = render_cross_repo_human(run_cross_repo_graph(root))
    assert "repositories=1 edges=0" in text
    assert "-[dependency]->" not in text


def test_live_claims_skip_taskless_and_unrelated_events(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    store = EventStore(db)
    payload = _claim("", "provider")
    payload["task_id"] = ""
    store.append(EventKind.CLAIM, payload, ts=1.0, durable=True)
    store.append(
        EventKind.LEDGER_PROGRESS,
        {"task_id": "NOTE-1", "author": "agent-a", "kind": "note", "text": "hi"},
        ts=2.0,
    )
    store.close()
    store = EventStore(db)
    try:
        events = list(store.read_all())
    finally:
        store.close()
    assert live_claims(events) == ()
