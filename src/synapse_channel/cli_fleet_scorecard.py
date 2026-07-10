# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fleet scorecard bundle and OTLP export command
"""CLI adapter for the offline-first fleet scorecard.

``synapse fleet-scorecard`` composes existing durable reports and either writes
one owner-only JSON bundle or pushes its causality spans and numeric points to
an OTLP/HTTP collector. The command reads local stores only; the collector push
is the sole network action and never mutates coordination state.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from synapse_channel.benchmark.trend import load_history
from synapse_channel.cli_accounting import _load_budgets, load_pricing_table
from synapse_channel.core.causality import DEFAULT_MAX_GRAPH_NODES
from synapse_channel.core.causality_otel import SERVICE_NAME
from synapse_channel.core.fleet_scorecard import (
    fleet_scorecard_to_json,
    run_fleet_scorecard,
)
from synapse_channel.otel_export import DEFAULT_EXPORT_TIMEOUT, push_projection
from synapse_channel.otel_metrics_export import push_metric_points


def _cmd_fleet_scorecard(args: argparse.Namespace) -> int:
    """Build one scorecard and write or export it."""
    if args.max_nodes < 0:
        print("fleet-scorecard: --max-nodes must be zero or positive", file=sys.stderr)
        return 2
    if args.timeout <= 0:
        print("fleet-scorecard: --timeout must be positive", file=sys.stderr)
        return 2
    service_name = str(args.service_name).strip()
    if not service_name:
        print("fleet-scorecard: --service-name must not be blank", file=sys.stderr)
        return 2
    try:
        pricing = load_pricing_table(args.pricing)
        budgets = _load_budgets(args.budget)
        history = None if args.trend is None else load_history(args.trend)
        scorecard = run_fleet_scorecard(
            args.db,
            benchmark_runs=history,
            pricing=pricing,
            budgets=budgets,
            max_nodes=args.max_nodes,
            service_name=service_name,
            key_file=args.db_key_file,
        )
    except ValueError as exc:
        print(f"fleet-scorecard: {exc}", file=sys.stderr)
        return 2

    if args.out is not None:
        output = Path(args.out)
        try:
            _write_bundle(output, fleet_scorecard_to_json(scorecard))
        except OSError as exc:
            print(f"fleet-scorecard: could not write {output}: {exc}", file=sys.stderr)
            return 2
        print(
            f"fleet scorecard: {output} "
            f"({len(scorecard.causality.spans)} spans, {len(scorecard.metrics)} metric points)"
        )
        return 0

    try:
        traces_endpoint, metrics_endpoint = _collector_endpoints(args.endpoint)
        metric_count = push_metric_points(
            scorecard.metrics,
            metrics_endpoint,
            service_name=service_name,
            timeout=args.timeout,
        )
        span_count = push_projection(
            scorecard.causality,
            traces_endpoint,
            timeout=args.timeout,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"fleet-scorecard: {exc}", file=sys.stderr)
        return 2
    print(
        f"fleet scorecard exported: {span_count} spans -> {traces_endpoint}; "
        f"{metric_count} metric points -> {metrics_endpoint}"
    )
    return 0


def _collector_endpoints(base: str) -> tuple[str, str]:
    """Return full OTLP traces and metrics URLs below a collector base URL.

    Parameters
    ----------
    base : str
        HTTP(S) collector base URL, with an optional path prefix but no query,
        fragment, credentials, or signal suffix.

    Returns
    -------
    tuple[str, str]
        Full ``/v1/traces`` and ``/v1/metrics`` endpoints.

    Raises
    ------
    ValueError
        If the base URL is malformed or already names one signal endpoint.
    """
    parsed = urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        msg = "--endpoint must be an http(s) OTLP collector base URL"
        raise ValueError(msg)
    if parsed.username is not None or parsed.password is not None:
        msg = (
            "--endpoint must not embed credentials; configure collector authentication outside argv"
        )
        raise ValueError(msg)
    if parsed.query or parsed.fragment:
        msg = "--endpoint must not contain a query string or fragment"
        raise ValueError(msg)
    root = base.rstrip("/")
    if root.endswith(("/v1/traces", "/v1/metrics")):
        msg = "--endpoint is the collector base; omit the /v1/traces or /v1/metrics suffix"
        raise ValueError(msg)
    return f"{root}/v1/traces", f"{root}/v1/metrics"


def _write_bundle(path: Path, document: dict[str, object]) -> None:
    """Atomically write one owner-only scorecard JSON bundle.

    The bundle can contain task identities and opt-in cost evidence. A sibling
    temporary file is created ``0600``, flushed, and renamed so neither a
    partial nor a newly world-readable bundle is observable.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``fleet-scorecard`` command."""
    parser = subparsers.add_parser(
        "fleet-scorecard",
        help=(
            "Compose causality, opt-in costs, contention, reliability, and optional "
            "benchmark history into JSON or an OTLP collector push."
        ),
    )
    parser.add_argument("db", help="Path to the hub event-store database.")
    parser.add_argument(
        "--db-key-file",
        default=None,
        help="Owner-only SQLCipher key for an encrypted event store.",
    )
    parser.add_argument(
        "--trend",
        default=None,
        help="Optional benchmark trend SQLite store; its complete history enters JSON.",
    )
    parser.add_argument(
        "--pricing",
        default=None,
        help="Optional accounting pricing JSON (model -> input_per_1k/output_per_1k).",
    )
    parser.add_argument(
        "--budget",
        default=None,
        help="Optional accounting budget JSON (agent -> local spend ceiling).",
    )
    parser.add_argument(
        "--max-nodes",
        type=int,
        default=DEFAULT_MAX_GRAPH_NODES,
        help="Fail-closed causality/contention event ceiling; 0 lifts it.",
    )
    parser.add_argument(
        "--service-name",
        default=SERVICE_NAME,
        help="OpenTelemetry service.name for traces and metrics.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_EXPORT_TIMEOUT,
        help="OTLP export timeout in seconds.",
    )
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument(
        "--out",
        help="Write one atomic owner-only JSON bundle.",
    )
    destination.add_argument(
        "--endpoint",
        help="Push to this OTLP/HTTP collector base (the command appends both signal paths).",
    )
    parser.set_defaults(func=_cmd_fleet_scorecard)
