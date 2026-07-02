# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — installed-version benchmark CLI command
"""CLI wrapper for the installed-version benchmark suite.

``synapse benchmark`` runs the packaged probes — durable event-store writes
and replay, lite relay encoding, and real WebSocket round-trips against an
in-process hub — and prints a scorecard carrying the host context (load,
CPU, governor) and an explicit shared-workstation isolation label. With
``--compare BASELINE.json`` (a file an earlier run saved with ``--results``)
the run is gated against the baseline: throughput and latency drift beyond
``--tolerance`` exits ``1``, and a baseline from a different CPU model is
refused.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from synapse_channel.benchmark.comparison import (
    DEFAULT_TOLERANCE_PCT,
    compare_scorecards,
    comparison_to_json,
    load_baseline,
    render_comparison_human,
)
from synapse_channel.benchmark.probes import PROBES, run_probes
from synapse_channel.benchmark.scorecard import (
    capture_host_context,
    finish_scorecard,
    render_scorecard_human,
    scorecard_to_json,
    write_scorecard,
)


def _cmd_benchmark(args: argparse.Namespace) -> int:
    """Run the selected probes and print (and optionally write) the scorecard."""
    if args.list:
        for name in sorted(PROBES):
            default_iterations, implementation = PROBES[name]
            summary = (implementation.__doc__ or "").strip().splitlines()[0]
            print(f"{name} (default {default_iterations} iterations): {summary}")
        return 0
    if args.tolerance is not None and args.compare is None:
        print("--tolerance requires --compare", file=sys.stderr)
        return 2
    if args.tolerance is not None and args.tolerance <= 0:
        print("tolerance must be positive", file=sys.stderr)
        return 2
    names = args.probe if args.probe else sorted(PROBES)
    context = capture_host_context()
    try:
        results = run_probes(names, iterations=args.iterations)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    scorecard = finish_scorecard(context, results)
    comparison = None
    if args.compare is not None:
        tolerance = DEFAULT_TOLERANCE_PCT if args.tolerance is None else args.tolerance
        try:
            baseline = load_baseline(Path(args.compare))
            comparison = compare_scorecards(baseline, scorecard, tolerance_pct=tolerance)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    if args.json:
        document = scorecard_to_json(scorecard)
        if comparison is not None:
            document["comparison"] = comparison_to_json(comparison)
        print(json.dumps(document, indent=2, sort_keys=True))
    else:
        print(render_scorecard_human(scorecard))
        if comparison is not None:
            print()
            print(render_comparison_human(comparison))
    if args.results is not None:
        write_scorecard(Path(args.results), scorecard)
    if comparison is not None and comparison.regressions:
        return 1
    return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``benchmark`` subparser."""
    parser = subparsers.add_parser(
        "benchmark",
        help=(
            "Benchmark the installed package (event store, relay encoding, live hub "
            "round-trips) and print a scorecard with honest host context."
        ),
    )
    parser.add_argument(
        "--probe",
        action="append",
        default=None,
        metavar="NAME",
        help="Run only this probe (repeatable); default runs every probe. See --list.",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=None,
        help="Override every selected probe's default iteration count (positive).",
    )
    parser.add_argument(
        "--results",
        default=None,
        metavar="FILE",
        help="Also write the scorecard JSON to this file.",
    )
    parser.add_argument(
        "--compare",
        default=None,
        metavar="BASELINE",
        help=(
            "Compare against a scorecard saved with --results; exit 1 when throughput "
            "or latency regresses beyond the tolerance. Refused across CPU models."
        ),
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=None,
        metavar="PCT",
        help=(
            "Allowed drift in percent before --compare counts a regression "
            f"(default {DEFAULT_TOLERANCE_PCT:g}, sized for shared-workstation noise)."
        ),
    )
    parser.add_argument("--list", action="store_true", help="List the available probes and exit.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.set_defaults(func=_cmd_benchmark)
