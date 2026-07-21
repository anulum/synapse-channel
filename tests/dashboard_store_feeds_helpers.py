# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — shared seed helper for the dashboard store-feed tests

from __future__ import annotations

from pathlib import Path

from synapse_channel.core.journal import (
    EventKind,
)
from synapse_channel.core.persistence import EventStore


def _seed_log(db: Path) -> None:
    """Five events across two tasks: A claimed→released, X claimed twice."""
    store = EventStore(db)
    store.append(
        EventKind.CLAIM,
        {"task_id": "A", "owner": "alice", "status": "claimed", "paths": [], "worktree": "w"},
        ts=1.0,
    )
    store.append(
        EventKind.TASK_UPDATE,
        {"task_id": "A", "owner": "alice", "status": "working", "paths": [], "worktree": "w"},
        ts=2.0,
    )
    store.append(EventKind.RELEASE, {"task_id": "A"}, ts=3.0)
    store.append(
        EventKind.CLAIM,
        {"task_id": "X", "owner": "bob", "status": "claimed", "paths": [], "worktree": "w"},
        ts=4.0,
    )
    store.append(
        EventKind.TASK_UPDATE,
        {"task_id": "X", "owner": "bob", "status": "working", "paths": [], "worktree": "w"},
        ts=5.0,
    )
    store.close()
