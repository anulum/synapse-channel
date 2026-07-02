# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — cross-repository dependency graph CLI command
"""CLI wrapper for the cross-repository dependency graph.

``synapse cross-repo`` scans a directory of repository checkouts into a
dependency graph (manifests and CODEOWNERS as edges), flags repository pairs
whose declared version constraints on the same package are provably disjoint
(``version_conflict`` edges), optionally joins the live claims of a hub
event log onto it, and prints the result as text, JSON, or Graphviz DOT.
With ``--repo`` the exit code becomes a coordination signal: ``1`` when a
live claim exists in a repository connected to the focus by a dependency
edge.
"""

from __future__ import annotations

import argparse
import json
import sys

from synapse_channel.core.cross_repo_graph import (
    SELF_RELATION,
    cross_repo_graph_to_json,
    render_cross_repo_dot,
    render_cross_repo_human,
    run_cross_repo_graph,
)


def _cmd_cross_repo(args: argparse.Namespace) -> int:
    """Scan, optionally join claims, and print one cross-repository report."""
    try:
        graph = run_cross_repo_graph(args.root, db_path=args.db, focus=args.repo)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(cross_repo_graph_to_json(graph), indent=2, sort_keys=True))
    elif args.dot:
        print(render_cross_repo_dot(graph))
    else:
        print(render_cross_repo_human(graph))
    if args.repo is not None and any(claim.relation != SELF_RELATION for claim in graph.claims):
        return 1
    return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``cross-repo`` subparser."""
    parser = subparsers.add_parser(
        "cross-repo",
        help=(
            "Scan a directory of repositories into a dependency graph "
            "(manifests/CODEOWNERS as edges) and join live claims onto it."
        ),
    )
    parser.add_argument(
        "root",
        help="Directory holding the repository checkouts (each subdirectory is one repo).",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Hub event store to join live claims from, e.g. ~/synapse/hub.db.",
    )
    parser.add_argument(
        "--repo",
        default=None,
        help=(
            "Focus repository: keep claims in it and in repositories connected to it "
            "by a dependency edge; exit 1 when a connected repository holds a live claim."
        ),
    )
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    output.add_argument("--dot", action="store_true", help="Emit a Graphviz digraph.")
    parser.set_defaults(func=_cmd_cross_repo)
