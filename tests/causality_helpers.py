# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — coordination-causality CLI regressions

"""Shared helpers for the causality CLI test suite."""

from __future__ import annotations

from pathlib import Path

from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore

REPO_ROOT = Path(__file__).resolve().parents[1]


def _seed(path: Path) -> None:
    """B done & released; A depends on B and is claimed after; C contends A's paths."""
    store = EventStore(path)
    store.append(EventKind.LEDGER_TASK, {"task_id": "B", "title": "B", "depends_on": []}, ts=1.0)
    store.append(
        EventKind.CLAIM,
        {
            "task_id": "B",
            "owner": "alice",
            "status": "claimed",
            "paths": ["src/x"],
            "worktree": "w",
        },
        ts=2.0,
    )
    store.append(
        EventKind.TASK_UPDATE,
        {"task_id": "B", "owner": "alice", "status": "done", "paths": ["src/x"], "worktree": "w"},
        ts=3.0,
    )
    store.append(EventKind.RELEASE, {"task_id": "B"}, ts=4.0)
    store.append(EventKind.LEDGER_TASK, {"task_id": "A", "title": "A", "depends_on": ["B"]}, ts=5.0)
    store.append(
        EventKind.CLAIM,
        {"task_id": "A", "owner": "bob", "status": "claimed", "paths": ["src/y"], "worktree": "w"},
        ts=6.0,
    )
    store.append(EventKind.RELEASE, {"task_id": "A"}, ts=7.0)
    store.append(
        EventKind.CLAIM,
        {
            "task_id": "C",
            "owner": "carol",
            "status": "claimed",
            "paths": ["src/y"],
            "worktree": "w",
        },
        ts=8.0,
    )
    store.close()


def _seed_contention(path: Path) -> None:
    """Two live claims by different owners overlap on src/y in one worktree."""
    store = EventStore(path)
    store.append(
        EventKind.CLAIM,
        {"task_id": "A", "owner": "bob", "status": "claimed", "paths": ["src/y"], "worktree": "w"},
        ts=1.0,
    )
    store.append(
        EventKind.CLAIM,
        {
            "task_id": "C",
            "owner": "carol",
            "status": "claimed",
            "paths": ["src/y"],
            "worktree": "w",
        },
        ts=2.0,
    )
    store.close()


def _seed_peer(path: Path) -> None:
    """P depends on B (completed on the primary hub) and is claimed here."""
    store = EventStore(path)
    store.append(EventKind.LEDGER_TASK, {"task_id": "P", "title": "P", "depends_on": ["B"]}, ts=5.0)
    store.append(
        EventKind.CLAIM,
        {"task_id": "P", "owner": "pete", "status": "claimed", "paths": ["src/z"], "worktree": "w"},
        ts=6.0,
    )
    store.close()


def _federated_pair(tmp_path: Path) -> tuple[Path, Path]:
    """Seed the primary hub (stem ``hub``) and a peer whose claim depends on it."""
    db = tmp_path / "hub.db"
    peer = tmp_path / "peer.db"
    _seed(db)
    _seed_peer(peer)
    return db, peer


def _seed_clean_lifecycle(db: Path) -> None:
    """One task claimed and released — a healthy log."""
    store = EventStore(db)
    store.append(
        EventKind.CLAIM,
        {"task_id": "B", "owner": "alice", "status": "claimed", "paths": [], "worktree": "w"},
        ts=1.0,
    )
    store.append(EventKind.RELEASE, {"task_id": "B"}, ts=2.0)
    store.close()
