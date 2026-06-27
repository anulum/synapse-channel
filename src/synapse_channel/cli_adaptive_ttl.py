# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — adaptive lease TTL advice CLI command
"""CLI wrapper for read-only adaptive lease TTL advice."""

from __future__ import annotations

import argparse
import json
import sys

from synapse_channel.core.adaptive_ttl import (
    DEFAULT_CURRENT_TTL_SECONDS,
    DEFAULT_MAX_TTL_SECONDS,
    DEFAULT_MIN_OWNER_SAMPLES,
    DEFAULT_MIN_SAMPLES,
    DEFAULT_MIN_TTL_SECONDS,
    DEFAULT_SAFETY_MULTIPLIER,
    render_human,
    run_ttl_advice,
    ttl_advice_to_json,
)


def _cmd_ttl_advice(args: argparse.Namespace) -> int:
    """Run one lease TTL advice report and print it."""
    try:
        report = run_ttl_advice(
            args.db,
            as_of=args.as_of,
            current_default_seconds=args.current_default,
            min_samples=args.min_samples,
            min_owner_samples=args.min_owner_samples,
            min_ttl_seconds=args.min_ttl,
            max_ttl_seconds=args.max_ttl,
            safety_multiplier=args.safety_multiplier,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(ttl_advice_to_json(report), indent=2, sort_keys=True))
    else:
        print(render_human(report))
    return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``ttl-advice`` subparser."""
    parser = subparsers.add_parser(
        "ttl-advice",
        help="Build read-only lease TTL advice from a hub SQLite event store.",
    )
    parser.add_argument("db", help="Path to the hub event store, e.g. ~/synapse/hub.db.")
    parser.add_argument(
        "--as-of",
        type=float,
        default=None,
        help="Timestamp used for stale live-claim counts; defaults to latest event time.",
    )
    parser.add_argument(
        "--current-default",
        type=float,
        default=DEFAULT_CURRENT_TTL_SECONDS,
        help="Current operator default TTL in seconds.",
    )
    parser.add_argument(
        "--min-samples",
        type=int,
        default=DEFAULT_MIN_SAMPLES,
        help="Completed samples required before changing the default recommendation.",
    )
    parser.add_argument(
        "--min-owner-samples",
        type=int,
        default=DEFAULT_MIN_OWNER_SAMPLES,
        help="Completed samples required before emitting owner-specific advice.",
    )
    parser.add_argument(
        "--min-ttl",
        type=float,
        default=DEFAULT_MIN_TTL_SECONDS,
        help="Lower bound for generated TTL advice, in seconds.",
    )
    parser.add_argument(
        "--max-ttl",
        type=float,
        default=DEFAULT_MAX_TTL_SECONDS,
        help="Upper bound for generated TTL advice, in seconds.",
    )
    parser.add_argument(
        "--safety-multiplier",
        type=float,
        default=DEFAULT_SAFETY_MULTIPLIER,
        help="Multiplier applied to observed p90 durations before clamping.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.set_defaults(func=_cmd_ttl_advice)
