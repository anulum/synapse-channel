# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — `synapse multihub` CLI: observe or follow a peer hub read-only
"""``synapse multihub`` — observe peers and recover durable quarantines.

The honest-peer multi-hub read-side merge layer is a library
(:mod:`synapse_channel.core.multihub_follower`); this command exposes it so an operator
can read a peer hub without writing Python. Both subcommands fold a peer's event log
through the follower and print the *observed* board, unresolved payload-free board
conflicts, progress count, and claim view — advisory only, since claims are never
granted across hubs — differing only in how they reach the peer's log:

* ``observe`` opens the peer hub's event-store **file** through the same ``read_since``
  seam the follower uses (SQLite WAL allows a concurrent reader beside the live peer hub);
* ``follow`` pulls the peer's log over a real **connection** via the network transport
  (:func:`synapse_channel.core.multihub_transport.network_fetcher`), for a peer reachable
  over the network rather than a shared filesystem.

The observation commands are read-only by construction: they fold and exit, granting
nothing. The explicit ``recover`` command appends an audited recovery to a stopped local
hub's journal; it never edits the peer log. The store factory
and fetcher factory are injectable so the commands are testable without a real peer. The
``follow`` pull is open or token-authenticated here, and ``--pin`` accepts a self-signed
``wss://`` peer by SHA-256 certificate pin
(:func:`~synapse_channel.core.multihub_transport.pinned_connector`); deny-by-default
federation/mTLS gating is available in the library
(:func:`~synapse_channel.core.multihub_federation.peer_authoriser`).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from synapse_channel.core.multihub_equivocation import MultiHubIntegrityError
from synapse_channel.core.multihub_fold import ObservedState
from synapse_channel.core.multihub_follower import EventFetcher, MultiHubFollower, store_fetcher
from synapse_channel.core.multihub_transport import (
    MultiHubFetchError,
    network_fetcher,
    pinned_connector,
)
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.persistence_sqlcipher import SqlCipherKeyError
from synapse_channel.terminal_text import terminal_text

StoreFactory = Callable[[str], EventStore]
FetcherFactory = Callable[..., EventFetcher]


def _render(state: ObservedState, peer_id: str, *, json_out: bool) -> None:
    """Print the observed coordination state of a peer, as JSON or readable lines."""
    if json_out:
        print(json.dumps({"peer_id": peer_id, **state.to_dict()}, indent=2))
        return
    print(
        f"observing peer '{terminal_text(peer_id)}' — {len(state.board)} tasks, "
        f"{len(state.progress)} progress notes, {len(state.observed_claims)} observed claims"
    )
    if state.board:
        print("board (display-only LWW — non-authoritative, non-causal):")
        for task_id in sorted(state.board):
            task = state.board[task_id]
            status = task.get("status", "?")
            title = task.get("title", "")
            provenance = state.board_provenance[task_id]
            print(
                f"  [{terminal_text(status)}] {terminal_text(task_id)} — "
                f"{terminal_text(title)} [display source "
                f"{terminal_text(provenance.hub_id)}#{provenance.seq} "
                f"@ {provenance.timestamp:g}]"
            )
    if state.board_conflicts:
        print("board conflicts (unresolved divergence — not causally ordered):")
        for task_id in sorted(state.board_conflicts):
            conflict = state.board_conflicts[task_id]
            contenders = ", ".join(
                f"{terminal_text(item.hub_id)}#{item.seq}="
                f"{terminal_text(item.record_fingerprint[:12])}"
                for item in conflict.contenders
            )
            print(f"  {terminal_text(task_id)} — {contenders}")
    if state.observed_claims:
        print("observed claims (advisory — not granted):")
        for task_id in sorted(state.observed_claims):
            observed = state.observed_claims[task_id]
            owner = observed.claim.get("owner", "?")
            print(
                f"  {terminal_text(task_id)} -> {terminal_text(owner)} "
                f"@ {terminal_text(observed.hub_id)}"
            )


def _cmd_observe(args: argparse.Namespace, *, store_factory: StoreFactory = EventStore) -> int:
    """Open a peer hub's event store, fold its log, and print the observed state."""
    if not Path(args.peer_db).is_file():
        print(f"peer database not found: {terminal_text(args.peer_db)}", file=sys.stderr)
        return 2
    peer_id = args.peer_id or Path(args.peer_db).stem
    key_file = getattr(args, "db_key_file", None)
    try:
        # Production opens honour SQLCipher; test injectables take path only.
        if store_factory is EventStore:
            store = EventStore(args.peer_db, key_file=key_file)
        else:
            store = store_factory(args.peer_db)
    except (sqlite3.Error, SqlCipherKeyError, MultiHubIntegrityError, ValueError) as exc:
        print(f"could not read peer event store: {terminal_text(exc)}", file=sys.stderr)
        return 2
    try:
        state = asyncio.run(MultiHubFollower().poll(peer_id, store_fetcher(store)))
    except (sqlite3.Error, SqlCipherKeyError, MultiHubIntegrityError, ValueError) as exc:
        print(f"could not read peer event store: {terminal_text(exc)}", file=sys.stderr)
        return 2
    finally:
        store.close()
    _render(state, peer_id, json_out=args.json)
    return 0


def _cmd_follow(
    args: argparse.Namespace, *, fetcher_factory: FetcherFactory = network_fetcher
) -> int:
    """Pull a peer hub's log over a connection, fold it, and print the observed state."""
    peer_id = args.peer_id or urlsplit(args.peer_uri).netloc or args.peer_uri
    fetch_kwargs: dict[str, Any] = {
        "local_id": args.local_id,
        "token": args.token,
        "limit": args.limit,
        "timeout": args.timeout,
    }
    pin = getattr(args, "pin", None)
    if pin:
        fetch_kwargs["connector"] = pinned_connector(pin)
    try:
        fetch = fetcher_factory(args.peer_uri, **fetch_kwargs)
        state = asyncio.run(MultiHubFollower().poll(peer_id, fetch))
    except (MultiHubFetchError, MultiHubIntegrityError, ValueError) as exc:
        print(f"could not follow peer hub: {terminal_text(exc)}", file=sys.stderr)
        return 2
    _render(state, peer_id, json_out=args.json)
    return 0


def _cmd_recover(args: argparse.Namespace) -> int:
    """Append an explicit recovery for one durable peer quarantine."""
    if not Path(args.journal).is_file():
        print(f"hub journal not found: {terminal_text(args.journal)}", file=sys.stderr)
        return 2
    store: EventStore | None = None
    try:
        store = EventStore(args.journal, key_file=args.db_key_file)
        follower = MultiHubFollower(journal=store, observer_id=args.operator)
        follower.recover_peer(
            args.peer_id,
            recovered_by=args.operator,
            reason=args.reason,
            new_log_generation=args.new_log_generation,
        )
    except (sqlite3.Error, SqlCipherKeyError, ValueError) as exc:
        print(f"could not recover peer quarantine: {terminal_text(exc)}", file=sys.stderr)
        return 2
    finally:
        if store is not None:
            store.close()
    print(
        f"recorded recovery for peer '{terminal_text(args.peer_id)}' "
        f"with log generation '{terminal_text(args.new_log_generation)}'"
    )
    return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``multihub`` command group."""
    parser = subparsers.add_parser(
        "multihub",
        help="Observe/follow peer state or recover a durable multi-hub quarantine.",
    )
    group = parser.add_subparsers(dest="multihub_command", required=True)

    observe = group.add_parser(
        "observe", help="Fold a peer hub's event-log file and print its observed board and claims."
    )
    observe.add_argument("--peer-db", required=True, help="Path to the peer hub's event-store db.")
    observe.add_argument(
        "--db-key-file",
        default=None,
        help="Owner-only SQLCipher key for an encrypted peer event store.",
    )
    observe.add_argument(
        "--peer-id", default=None, help="Id to tag the peer's events with; defaults to the db name."
    )
    observe.add_argument("--json", action="store_true", help="Emit the observed state as JSON.")
    observe.set_defaults(func=_cmd_observe)

    follow = group.add_parser(
        "follow",
        help="Pull a peer hub's event log over a connection and print its observed board.",
    )
    follow.add_argument(
        "--peer-uri", required=True, help="Peer hub websocket URI (ws:// or wss://)."
    )
    follow.add_argument(
        "--peer-id", default=None, help="Id to tag the peer's events with; defaults to the host."
    )
    follow.add_argument(
        "--local-id",
        default="multihub-follower",
        help="Identity stamped on the request so the peer addresses the snapshot back.",
    )
    follow.add_argument("--token", default=None, help="Auth token for a secured peer hub.")
    follow.add_argument(
        "--pin",
        default=None,
        help=(
            "Accept the wss:// peer only if its live certificate matches this sha256:<hex> "
            "pin (self-signed or private-CA peers; no CA needed)."
        ),
    )
    follow.add_argument(
        "--limit", type=int, default=None, help="Maximum events to pull in the batch."
    )
    follow.add_argument(
        "--timeout", type=float, default=10.0, help="Seconds to wait for the snapshot."
    )
    follow.add_argument("--json", action="store_true", help="Emit the observed state as JSON.")
    follow.set_defaults(func=_cmd_follow)

    recover = group.add_parser(
        "recover",
        help="Record explicit recovery of a durable peer quarantine while the hub is stopped.",
    )
    recover.add_argument("--journal", required=True, help="Stopped local hub's event-store db.")
    recover.add_argument(
        "--db-key-file",
        default=None,
        help="Owner-only SQLCipher key for an encrypted local journal.",
    )
    recover.add_argument("--peer-id", required=True, help="Exact quarantined peer identity.")
    recover.add_argument("--operator", required=True, help="Operator accepting the recovery.")
    recover.add_argument(
        "--reason", required=True, help="Recovery rationale; the journal retains only its digest."
    )
    recover.add_argument(
        "--new-log-generation",
        required=True,
        help="Operator-accepted new log generation or checkpoint identity.",
    )
    recover.set_defaults(func=_cmd_recover)
