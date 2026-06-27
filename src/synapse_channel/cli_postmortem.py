# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — postmortem CLI command
"""CLI wrapper for replayable task postmortems."""

from __future__ import annotations

import argparse
import json
import sys

from synapse_channel.core.postmortem import (
    postmortem_to_json,
    render_markdown,
    run_task_postmortem,
)


def _cmd_postmortem(args: argparse.Namespace) -> int:
    """Run one replayable task postmortem and print the report."""
    try:
        report = run_task_postmortem(args.db, args.task_id)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(postmortem_to_json(report), indent=2, sort_keys=True))
    else:
        print(render_markdown(report))
    return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``postmortem`` subparser."""
    parser = subparsers.add_parser(
        "postmortem",
        help="Build a replayable task postmortem from a hub SQLite event store.",
    )
    parser.add_argument("db", help="Path to the hub event store, e.g. ~/synapse/hub.db.")
    parser.add_argument("task_id", help="Task id to reconstruct.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.set_defaults(func=_cmd_postmortem)
