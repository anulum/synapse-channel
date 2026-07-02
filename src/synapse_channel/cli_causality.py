# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — coordination-causality CLI command
"""CLI wrapper for the coordination-causality graph over the event log.

``causality`` answers ``causes``, ``effects``, or ``counterfactual`` against an
event sequence: the events that preceded it, the events it enabled, or the
downstream events that lose their recorded support without it. ``contention``
takes no sequence: it weighs every pair of overlapping live claims by what each
blocks downstream and recommends — advisory, never preempting — which contender
yields. All four read the durable log and contact no live hub.

With ``--peer HUB=PATH`` (repeatable) the sequence queries run over the
*federated* graph instead: the named hubs' logs merge in the deterministic
multi-hub order and an edge whose endpoints two different hubs authored is
tagged ``federation`` (:mod:`synapse_channel.core.causality_federation`).
Events are then addressed as ``HUB:SEQ``; a plain ``SEQ`` means the primary
DB's hub. Cross-hub precedence is clock-ordered evidence — only as good as the
hubs' clock agreement — and the query stays read-only and advisory.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from synapse_channel.core.causality import (
    DEFAULT_MAX_GRAPH_NODES,
    DIRECTIONS,
    causality_to_json,
    render_markdown,
    run_causality,
)
from synapse_channel.core.causality_federation import (
    federated_to_json,
    parse_hub_ref,
    render_federated_markdown,
    run_federated_causality,
)
from synapse_channel.core.yield_advice import (
    advice_to_json,
    render_advice_markdown,
    run_yield_advice,
)

CONTENTION_MODE = "contention"
"""Query mode that weighs overlapping live claims instead of one sequence."""


def _cmd_causality(args: argparse.Namespace) -> int:
    """Answer a causality query against a sequence point and print it."""
    if args.hub_id and not args.peer:
        print(
            "--hub-id names the primary log in a federated query; it requires --peer",
            file=sys.stderr,
        )
        return 2
    if args.direction == CONTENTION_MODE:
        if args.peer:
            print(
                "causality contention weighs one hub's live claims; --peer is not supported",
                file=sys.stderr,
            )
            return 2
        return _cmd_contention(args)
    if args.seq is None:
        print(f"causality {args.direction} requires an event SEQ", file=sys.stderr)
        return 2
    if args.peer:
        return _cmd_federated(args)
    try:
        seq = int(args.seq)
    except ValueError:
        print(f"invalid SEQ '{args.seq}': expected an integer", file=sys.stderr)
        return 2
    try:
        query = run_causality(args.db, args.direction, seq, max_nodes=args.max_nodes)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(causality_to_json(query), indent=2, sort_keys=True))
    else:
        print(render_markdown(query))
    return 0 if query.present else 1


def _cmd_federated(args: argparse.Namespace) -> int:
    """Answer a causality query over the merged logs of several hubs."""
    primary = args.hub_id or Path(args.db).stem
    try:
        stores = _federated_stores(primary, args.db, args.peer)
        ref = parse_hub_ref(args.seq, primary)
        query = run_federated_causality(stores, args.direction, ref, max_nodes=args.max_nodes)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(federated_to_json(query), indent=2, sort_keys=True))
    else:
        print(render_federated_markdown(query))
    return 0 if query.present else 1


def _federated_stores(primary: str, db: str, peers: list[str]) -> dict[str, str]:
    """Resolve the primary DB plus every ``--peer HUB=PATH`` into hub-keyed stores.

    Raises
    ------
    ValueError
        If a peer spec is not ``HUB=PATH`` or a hub id repeats — the merge
        dedupes by ``(hub_id, seq)``, so two logs under one id would silently
        collapse instead of merging.
    """
    stores = {primary: db}
    for spec in peers:
        hub, sep, path = spec.partition("=")
        hub = hub.strip()
        if not sep or not hub or not path:
            msg = f"invalid --peer '{spec}': expected HUB=PATH"
            raise ValueError(msg)
        if hub in stores:
            msg = f"duplicate hub id '{hub}'; each merged log needs a unique hub id"
            raise ValueError(msg)
        stores[hub] = path
    return stores


def _cmd_contention(args: argparse.Namespace) -> int:
    """Weigh overlapping live claims and print the yield recommendations.

    Exit ``0`` when no live claims overlap, ``1`` when at least one pair does —
    the exit code doubles as a collision signal for scripts, mirroring how the
    sequence queries exit ``1`` for an absent event.
    """
    try:
        recommendations = run_yield_advice(args.db, max_nodes=args.max_nodes)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(advice_to_json(recommendations), indent=2, sort_keys=True))
    else:
        print(render_advice_markdown(recommendations))
    return 1 if recommendations else 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``causality`` subparser."""
    causality = subparsers.add_parser(
        "causality",
        help="Trace coordination causes, effects, counterfactuals, or claim contention.",
    )
    causality.add_argument(
        "direction",
        choices=(*DIRECTIONS, CONTENTION_MODE),
        help="causes (upstream), effects (downstream), counterfactual (lost support), "
        "or contention (weigh overlapping live claims; takes no SEQ).",
    )
    causality.add_argument("db", help="Path to the hub event store, e.g. ~/synapse/hub.db.")
    causality.add_argument(
        "seq",
        nargs="?",
        default=None,
        metavar="SEQ",
        help="Event sequence to query (HUB:SEQ with --peer); required for "
        "causes/effects/counterfactual.",
    )
    causality.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    causality.add_argument(
        "--peer",
        action="append",
        default=[],
        metavar="HUB=PATH",
        help="Merge a peer hub's event store into a federated graph (repeatable); "
        "an edge whose endpoints two different hubs authored is tagged 'federation'.",
    )
    causality.add_argument(
        "--hub-id",
        default=None,
        help="Hub id of the primary DB in a federated query; defaults to the DB file name.",
    )
    causality.add_argument(
        "--max-nodes",
        type=int,
        default=DEFAULT_MAX_GRAPH_NODES,
        help="Fail-closed ceiling on coordination events folded into the graph "
        "(0 lifts it); exceeding it errors instead of exhausting memory.",
    )
    causality.set_defaults(func=_cmd_causality)
