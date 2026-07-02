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
"""

from __future__ import annotations

import argparse
import json
import sys

from synapse_channel.core.causality import (
    DEFAULT_MAX_GRAPH_NODES,
    DIRECTIONS,
    causality_to_json,
    render_markdown,
    run_causality,
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
    if args.direction == CONTENTION_MODE:
        return _cmd_contention(args)
    if args.seq is None:
        print(f"causality {args.direction} requires an event SEQ", file=sys.stderr)
        return 2
    try:
        query = run_causality(args.db, args.direction, args.seq, max_nodes=args.max_nodes)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(causality_to_json(query), indent=2, sort_keys=True))
    else:
        print(render_markdown(query))
    return 0 if query.present else 1


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
        type=int,
        nargs="?",
        default=None,
        metavar="SEQ",
        help="Event sequence to query; required for causes/effects/counterfactual.",
    )
    causality.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    causality.add_argument(
        "--max-nodes",
        type=int,
        default=DEFAULT_MAX_GRAPH_NODES,
        help="Fail-closed ceiling on coordination events folded into the graph "
        "(0 lifts it); exceeding it errors instead of exhausting memory.",
    )
    causality.set_defaults(func=_cmd_causality)
