# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — trust-graph query CLI command
"""CLI wrapper for the queryable evidence trust graph.

``synapse trust-graph`` projects the durable event log into typed evidence
edges (positive receipts, stale claims, declared failed checks, broken
handoffs, conflict pairs) between agent and task nodes, filtered to one
agent, task, or time window, and printed as text, JSON, or Graphviz DOT.
"""

from __future__ import annotations

import argparse
import json
import sys

from synapse_channel.core.trust_graph import (
    graph_involving,
    render_trust_graph_dot,
    render_trust_graph_human,
    run_trust_graph,
    trust_graph_to_json,
)


def _cmd_trust_graph(args: argparse.Namespace) -> int:
    """Build, filter, and print one trust-graph query."""
    try:
        graph = run_trust_graph(args.db, as_of=args.as_of)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    graph = graph_involving(graph, agent=args.agent, task=args.task, since=args.since)
    if args.json:
        print(json.dumps(trust_graph_to_json(graph), indent=2, sort_keys=True))
    elif args.dot:
        print(render_trust_graph_dot(graph))
    else:
        print(render_trust_graph_human(graph))
    return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``trust-graph`` subparser."""
    parser = subparsers.add_parser(
        "trust-graph",
        help=(
            "Query the evidence trust graph (receipts, stale claims, conflicts) "
            "from a hub SQLite event store."
        ),
    )
    parser.add_argument("db", help="Path to the hub event store, e.g. ~/synapse/hub.db.")
    parser.add_argument(
        "--agent",
        default=None,
        help="Keep only evidence edges with this agent as an endpoint.",
    )
    parser.add_argument(
        "--task",
        default=None,
        help="Keep only evidence edges involving this task id (conflict pairs included).",
    )
    parser.add_argument(
        "--since",
        type=float,
        default=None,
        help="Keep only evidence at or after this timestamp (the decay window).",
    )
    parser.add_argument(
        "--as-of",
        type=float,
        default=None,
        help="Timestamp used for stale lease checks; defaults to the latest event timestamp.",
    )
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    output.add_argument("--dot", action="store_true", help="Emit a Graphviz digraph.")
    parser.set_defaults(func=_cmd_trust_graph)
