# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — `synapse multihub` CLI: observe a peer hub's coordination read-only
"""``synapse multihub observe`` — read a peer hub's coordination state, read-only.

The multi-hub read-side CRDT layer is a library
(:mod:`synapse_channel.core.multihub_follower`); this command exposes it so an operator
can observe a peer hub without writing Python. ``observe`` opens the peer hub's event
store, folds its log through the follower, and prints the *observed* board, progress
count, and claim view — advisory only, since claims are never granted across hubs.

It is read-only by construction: it opens the peer store through the same
``read_since`` seam the follower uses (SQLite WAL allows a concurrent reader beside the
live peer hub), folds, and exits. The store factory is injectable so the command is
testable against a temporary event store.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path

from synapse_channel.core.multihub_fold import ObservedState
from synapse_channel.core.multihub_follower import MultiHubFollower, store_fetcher
from synapse_channel.core.persistence import EventStore

StoreFactory = Callable[[str], EventStore]


def _render(state: ObservedState, peer_id: str, *, json_out: bool) -> None:
    """Print the observed coordination state of a peer, as JSON or readable lines."""
    if json_out:
        print(json.dumps({"peer_id": peer_id, **state.to_dict()}, indent=2))
        return
    print(
        f"observing peer '{peer_id}' — {len(state.board)} tasks, "
        f"{len(state.progress)} progress notes, {len(state.observed_claims)} observed claims"
    )
    if state.board:
        print("board:")
        for task_id in sorted(state.board):
            task = state.board[task_id]
            status = task.get("status", "?")
            title = task.get("title", "")
            print(f"  [{status}] {task_id} — {title}")
    if state.observed_claims:
        print("observed claims (advisory — not granted):")
        for task_id in sorted(state.observed_claims):
            observed = state.observed_claims[task_id]
            owner = observed.claim.get("owner", "?")
            print(f"  {task_id} -> {owner} @ {observed.hub_id}")


def _cmd_observe(args: argparse.Namespace, *, store_factory: StoreFactory = EventStore) -> int:
    """Open a peer hub's event store, fold its log, and print the observed state."""
    if not Path(args.peer_db).is_file():
        print(f"peer database not found: {args.peer_db}", file=sys.stderr)
        return 2
    peer_id = args.peer_id or Path(args.peer_db).stem
    try:
        store = store_factory(args.peer_db)
    except sqlite3.Error as exc:
        print(f"could not read peer event store: {exc}", file=sys.stderr)
        return 2
    try:
        state = asyncio.run(MultiHubFollower().poll(peer_id, store_fetcher(store)))
    except sqlite3.Error as exc:
        print(f"could not read peer event store: {exc}", file=sys.stderr)
        return 2
    finally:
        store.close()
    _render(state, peer_id, json_out=args.json)
    return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``multihub`` command group."""
    parser = subparsers.add_parser(
        "multihub",
        help="Observe a peer hub's coordination state read-only (multi-hub read side).",
    )
    group = parser.add_subparsers(dest="multihub_command", required=True)

    observe = group.add_parser(
        "observe", help="Fold a peer hub's event log and print its observed board and claims."
    )
    observe.add_argument("--peer-db", required=True, help="Path to the peer hub's event-store db.")
    observe.add_argument(
        "--peer-id", default=None, help="Id to tag the peer's events with; defaults to the db name."
    )
    observe.add_argument("--json", action="store_true", help="Emit the observed state as JSON.")
    observe.set_defaults(func=_cmd_observe)
