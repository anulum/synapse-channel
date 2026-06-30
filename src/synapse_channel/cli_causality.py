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
downstream events that lose their recorded support without it. It reads the
durable log and contacts no live hub.
"""

from __future__ import annotations

import argparse
import json
import sys

from synapse_channel.core.causality import (
    DIRECTIONS,
    causality_to_json,
    render_markdown,
    run_causality,
)


def _cmd_causality(args: argparse.Namespace) -> int:
    """Answer a causality query against a sequence point and print it."""
    try:
        query = run_causality(args.db, args.direction, args.seq)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(causality_to_json(query), indent=2, sort_keys=True))
    else:
        print(render_markdown(query))
    return 0 if query.present else 1


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``causality`` subparser."""
    causality = subparsers.add_parser(
        "causality",
        help="Trace coordination causes, effects, or counterfactuals over the event log.",
    )
    causality.add_argument(
        "direction",
        choices=DIRECTIONS,
        help="causes (upstream), effects (downstream), or counterfactual (lost support).",
    )
    causality.add_argument("db", help="Path to the hub event store, e.g. ~/synapse/hub.db.")
    causality.add_argument(
        "seq",
        type=int,
        metavar="SEQ",
        help="Event sequence to query.",
    )
    causality.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    causality.set_defaults(func=_cmd_causality)
