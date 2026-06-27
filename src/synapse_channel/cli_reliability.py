# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — reliability memory CLI command
"""CLI wrapper for evidence-only reliability memory reports."""

from __future__ import annotations

import argparse
import json
import sys

from synapse_channel.core.reliability import (
    reliability_to_json,
    render_human,
    run_reliability_report,
)


def _cmd_reliability(args: argparse.Namespace) -> int:
    """Run one reliability memory report and print it."""
    try:
        report = run_reliability_report(args.db, as_of=args.as_of)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(reliability_to_json(report), indent=2, sort_keys=True))
    else:
        print(render_human(report))
    return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``reliability`` subparser."""
    parser = subparsers.add_parser(
        "reliability",
        help="Build evidence-only reliability memory from a hub SQLite event store.",
    )
    parser.add_argument("db", help="Path to the hub event store, e.g. ~/synapse/hub.db.")
    parser.add_argument(
        "--as-of",
        type=float,
        default=None,
        help="Timestamp used for stale lease checks; defaults to the latest event timestamp.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.set_defaults(func=_cmd_reliability)
