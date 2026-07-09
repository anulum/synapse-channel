# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — memory projection CLI command
"""Command-line adapter for deterministic local memory recall."""

from __future__ import annotations

import argparse
import sys

from synapse_channel.core.memory_projection import (
    MemoryRecallInputError,
    memory_recall_to_json,
    read_memory_recall,
    render_memory_recall,
)


def _cmd_memory_recall(args: argparse.Namespace) -> int:
    """Run deterministic local memory recall over a hub event store.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed ``memory-recall`` arguments.

    Returns
    -------
    int
        ``0`` on success, ``2`` for invalid local input.
    """
    try:
        report = read_memory_recall(
            args.db,
            args.query,
            since_seq=args.since_seq,
            limit=args.limit,
            key_file=getattr(args, "db_key_file", None),
        )
    except MemoryRecallInputError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(memory_recall_to_json(report))
    else:
        print(render_memory_recall(report))
    return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``memory-recall`` subparser."""
    parser = subparsers.add_parser(
        "memory-recall",
        help="Recall matching durable memory records from a local event store.",
    )
    parser.add_argument("db", help="Path to the hub event store, e.g. ~/synapse/hub.db.")
    parser.add_argument(
        "--db-key-file",
        default=None,
        help="Owner-only SQLCipher key for an encrypted event store.",
    )
    parser.add_argument("query", help="Plain-text recall query.")
    parser.add_argument(
        "--since-seq",
        type=int,
        default=0,
        help="Return recall candidates whose event sequence is above this cursor.",
    )
    parser.add_argument("--limit", type=int, default=5, help="Maximum recall hits to return.")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of text.")
    parser.set_defaults(func=_cmd_memory_recall)
