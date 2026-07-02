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

With ``--peer HUB=PATH`` (repeatable) the sequence queries run over the
*federated* graph instead: the named hubs' logs merge in the deterministic
multi-hub order and an edge whose endpoints two different hubs authored is
tagged ``federation`` (:mod:`synapse_channel.core.causality_federation`).
Events are then addressed as ``HUB:SEQ``; a plain ``SEQ`` means the primary
DB's hub. Cross-hub precedence is clock-ordered evidence — only as good as the
hubs' clock agreement — and the query stays read-only and advisory. ``--dot``
renders the federated answer as a Graphviz digraph, one cluster per hub with
federation edges coloured, so the cross-hub topology is visible at a glance.

``otel`` takes no sequence either: it projects the whole graph onto
OpenTelemetry spans — one trace per task, one span per coordination event,
cross-task ``dependency``/``contention`` edges as span links — and either
writes the span records as JSON (``--out``, no extra dependency) or pushes
them to an OTLP/HTTP collector (``--endpoint``, needs the optional ``otel``
extra). Deterministic ids: re-exporting the same log yields the same spans.
``--service-name`` overrides the ``service.name`` resource so several hubs
can share one observability tenant; ``--filter TASK_ID`` (repeatable)
narrows the projection to named tasks, keeping links into excluded tasks
and counting the exclusions; an event recording the lifecycle failure
terminal projects span status ``ERROR``; ``--watch`` re-projects and
re-exports on a fixed cadence, idempotent collector-side thanks to the
deterministic ids.

``health`` takes no sequence either: it walks each task's recorded lifecycle
and flags orphaned claims (claimed, then silence), declared dependencies that
never completed, and unreleased claims silent past ``--stale-after`` seconds —
ages measured against the log's own final timestamp, never the wall clock.
Exit ``1`` signals at least one anomaly, mirroring ``contention``. With
``--watch`` the assessment repeats on a cadence: the first tick prints the
full report as the baseline and every later tick prints only the anomaly
transitions (``+ fact`` new, ``- fact`` cleared), so a steady fleet stays
quiet and the scrollback reads as a timeline; ``--json`` streams one full
report per tick as NDJSON instead, and a failing tick stops the watch
fail-visible.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path

from synapse_channel.core.causality import (
    DEFAULT_MAX_GRAPH_NODES,
    DIRECTIONS,
    causality_to_json,
    render_markdown,
    run_causality,
)
from synapse_channel.core.causality_federation import (
    federated_to_json,
    parse_hub_ref,
    render_federated_dot,
    render_federated_markdown,
    run_federated_causality,
)
from synapse_channel.core.causality_health import (
    DEFAULT_STALE_AFTER,
    health_facts,
    health_to_json,
    render_health_markdown,
    run_causal_health,
)
from synapse_channel.core.causality_otel import (
    SERVICE_NAME,
    projection_to_json,
    run_otel_projection,
)
from synapse_channel.core.yield_advice import (
    advice_to_json,
    render_advice_markdown,
    run_yield_advice,
)

CONTENTION_MODE = "contention"
"""Query mode that weighs overlapping live claims instead of one sequence."""

OTEL_MODE = "otel"
"""Mode that projects the causality graph onto OpenTelemetry spans."""

HEALTH_MODE = "health"
"""Mode that flags anomalies in each task's recorded lifecycle."""


def _cmd_causality(args: argparse.Namespace) -> int:
    """Answer a causality query against a sequence point and print it."""
    if args.hub_id and not args.peer:
        print(
            "--hub-id names the primary log in a federated query; it requires --peer",
            file=sys.stderr,
        )
        return 2
    otel_only = (
        args.out is not None
        or args.endpoint is not None
        or args.service_name is not None
        or args.filter
    )
    if args.direction != OTEL_MODE and otel_only:
        print(
            "--out/--endpoint/--service-name/--filter belong to the otel mode",
            file=sys.stderr,
        )
        return 2
    if args.watch and args.direction not in (OTEL_MODE, HEALTH_MODE):
        print("--watch re-runs the otel or health mode on a cadence", file=sys.stderr)
        return 2
    if args.dot and not args.peer:
        print(
            "--dot renders the federated causal graph; it requires --peer HUB=PATH",
            file=sys.stderr,
        )
        return 2
    if args.direction != HEALTH_MODE and args.stale_after is not None:
        print("--stale-after belongs to the health mode", file=sys.stderr)
        return 2
    if args.direction == HEALTH_MODE:
        if args.peer:
            print(
                "causality health assesses one hub's log; --peer is not supported",
                file=sys.stderr,
            )
            return 2
        return _cmd_health(args)
    if args.direction == CONTENTION_MODE:
        if args.peer:
            print(
                "causality contention weighs one hub's live claims; --peer is not supported",
                file=sys.stderr,
            )
            return 2
        return _cmd_contention(args)
    if args.direction == OTEL_MODE:
        if args.peer:
            print(
                "causality otel projects one hub's log; --peer is not supported",
                file=sys.stderr,
            )
            return 2
        return _cmd_otel(args)
    if args.seq is None:
        print(f"causality {args.direction} requires an event SEQ", file=sys.stderr)
        return 2
    if args.peer:
        return _cmd_federated(args)
    try:
        seq = int(args.seq)
    except ValueError:
        print(f"invalid SEQ '{args.seq}': expected an integer", file=sys.stderr)
        return 2
    try:
        query = run_causality(args.db, args.direction, seq, max_nodes=args.max_nodes)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(causality_to_json(query), indent=2, sort_keys=True))
    else:
        print(render_markdown(query))
    return 0 if query.present else 1


def _cmd_otel(args: argparse.Namespace) -> int:
    """Project the causality graph onto OpenTelemetry spans and write or push them.

    ``--out FILE`` writes the pure span records as JSON (no extra dependency);
    ``--endpoint URL`` pushes real OTLP over HTTP and needs the optional
    ``otel`` extra. Exactly one of the two is required. ``--service-name``
    overrides the ``service.name`` resource; ``--filter TASK_ID`` (repeatable)
    projects only the named tasks and refuses a task the log does not record.
    ``--watch`` re-projects and re-exports every ``--interval`` seconds —
    live coordination observability; the deterministic ids make each
    re-export idempotent on the collector side.
    """
    if (args.out is None) == (args.endpoint is None):
        print(
            "causality otel requires exactly one of --out FILE or --endpoint URL", file=sys.stderr
        )
        return 2
    if args.watch:
        if args.interval <= 0:
            print("--interval must be positive", file=sys.stderr)
            return 2
        try:
            return _watch_otel(args)
        except KeyboardInterrupt:
            return 0
    return _otel_once(args)


def _watch_otel(args: argparse.Namespace, *, sleeper: Callable[[float], None] | None = None) -> int:
    """Re-project and re-export the spans on a fixed cadence.

    Each tick is one full :func:`_otel_once` pass — the store is reread, so
    events recorded since the last tick appear in the next export, and the
    deterministic span ids mean a collector receiving the same span twice
    stores one. A failing tick stops the watch with its exit code, exactly
    as a single export fails visibly; ``--count`` bounds the ticks (``0``
    runs until interrupted, and Ctrl-C is the normal way to stop).

    Parameters
    ----------
    args : argparse.Namespace
        The parsed ``causality otel`` arguments.
    sleeper : Callable[[float], None] or None, optional
        Sleep function between ticks; ``None`` uses :func:`time.sleep`.
        Injectable for testing.
    """
    sleep = time.sleep if sleeper is None else sleeper
    ticks = 0
    while True:
        code = _otel_once(args)
        if code != 0:
            return code
        ticks += 1
        if args.count and ticks >= args.count:
            return 0
        sleep(args.interval)


def _otel_once(args: argparse.Namespace) -> int:
    """Run one projection-and-export pass and print its summary line."""
    try:
        projection = run_otel_projection(
            args.db,
            max_nodes=args.max_nodes,
            service_name=args.service_name if args.service_name is not None else SERVICE_NAME,
            task_filter=args.filter or None,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.out is not None:
        try:
            Path(args.out).write_text(
                json.dumps(projection_to_json(projection), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            print(f"cannot write span records: {exc}", file=sys.stderr)
            return 2
        destination = args.out
    else:
        from synapse_channel.otel_export import push_projection

        try:
            push_projection(projection, args.endpoint)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        destination = args.endpoint
    skipped = (
        f", {projection.skipped_events} taskless event(s) skipped"
        if projection.skipped_events
        else ""
    )
    filtered = (
        f", {projection.filtered_out_tasks} task(s) filtered out"
        if projection.filtered_out_tasks
        else ""
    )
    print(
        f"exported {len(projection.spans)} span(s) across {projection.trace_count} "
        f"trace(s) to {destination}{skipped}{filtered}"
    )
    return 0


def _cmd_federated(args: argparse.Namespace) -> int:
    """Answer a causality query over the merged logs of several hubs."""
    primary = args.hub_id or Path(args.db).stem
    try:
        stores = _federated_stores(primary, args.db, args.peer)
        ref = parse_hub_ref(args.seq, primary)
        query = run_federated_causality(stores, args.direction, ref, max_nodes=args.max_nodes)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(federated_to_json(query), indent=2, sort_keys=True))
    elif args.dot:
        print(render_federated_dot(query))
    else:
        print(render_federated_markdown(query))
    return 0 if query.present else 1


def _federated_stores(primary: str, db: str, peers: list[str]) -> dict[str, str]:
    """Resolve the primary DB plus every ``--peer HUB=PATH`` into hub-keyed stores.

    Raises
    ------
    ValueError
        If a peer spec is not ``HUB=PATH`` or a hub id repeats — the merge
        dedupes by ``(hub_id, seq)``, so two logs under one id would silently
        collapse instead of merging.
    """
    stores = {primary: db}
    for spec in peers:
        hub, sep, path = spec.partition("=")
        hub = hub.strip()
        if not sep or not hub or not path:
            msg = f"invalid --peer '{spec}': expected HUB=PATH"
            raise ValueError(msg)
        if hub in stores:
            msg = f"duplicate hub id '{hub}'; each merged log needs a unique hub id"
            raise ValueError(msg)
        stores[hub] = path
    return stores


def _cmd_health(args: argparse.Namespace) -> int:
    """Flag lifecycle anomalies in the causal graph and print the report.

    Exit ``0`` when the log shows no anomaly, ``1`` when at least one claim is
    orphaned or stale or a declared dependency never completed — the exit code
    doubles as a health signal for scripts, mirroring ``contention``. With
    ``--watch`` the assessment repeats on a cadence via :func:`_watch_health`.
    """
    stale_after = args.stale_after if args.stale_after is not None else DEFAULT_STALE_AFTER
    if stale_after <= 0:
        print("--stale-after must be positive", file=sys.stderr)
        return 2
    if args.watch:
        if args.interval <= 0:
            print("--interval must be positive", file=sys.stderr)
            return 2
        try:
            return _watch_health(args, stale_after=stale_after)
        except KeyboardInterrupt:
            return 0
    try:
        report = run_causal_health(args.db, max_nodes=args.max_nodes, stale_after=stale_after)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(health_to_json(report), indent=2, sort_keys=True))
    else:
        print(render_health_markdown(report))
    return 1 if report.anomaly_count else 0


def _watch_health(
    args: argparse.Namespace,
    *,
    stale_after: float,
    sleeper: Callable[[float], None] | None = None,
) -> int:
    """Re-assess the log's lifecycle health on a cadence, reporting transitions.

    Each tick is one full :func:`run_causal_health` pass — the store is
    reread, so events recorded since the last tick move the assessment. The
    first tick prints the full report as the baseline; every later tick
    prints only the transitions, ``+ fact`` for a new anomaly and ``- fact``
    for a cleared one (:func:`health_facts`), so a steady fleet stays quiet
    and a scrollback reads as a timeline of what went wrong and what
    recovered. Under ``--json`` every tick emits the full report as one
    compact NDJSON line instead. A failing tick stops the watch with exit
    ``2``, fail-visible like a single run; ``--count`` bounds the ticks and
    the exit code then reports the LAST tick's anomaly signal (``1`` when
    anomalies remain, ``0`` when clear).

    Parameters
    ----------
    args : argparse.Namespace
        The parsed ``causality health`` arguments.
    stale_after : float
        Validated staleness threshold in seconds.
    sleeper : Callable[[float], None] or None, optional
        Sleep function between ticks; ``None`` uses :func:`time.sleep`.
        Injectable for testing.
    """
    sleep = time.sleep if sleeper is None else sleeper
    ticks = 0
    previous: frozenset[str] | None = None
    while True:
        try:
            report = run_causal_health(args.db, max_nodes=args.max_nodes, stale_after=stale_after)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        facts = health_facts(report)
        if args.json:
            print(json.dumps(health_to_json(report), sort_keys=True))
        elif previous is None:
            print(render_health_markdown(report))
        else:
            for fact in sorted(facts - previous):
                print(f"+ {fact}")
            for fact in sorted(previous - facts):
                print(f"- {fact}")
        sys.stdout.flush()
        previous = facts
        ticks += 1
        if args.count and ticks >= args.count:
            return 1 if report.anomaly_count else 0
        sleep(args.interval)


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
        choices=(*DIRECTIONS, CONTENTION_MODE, OTEL_MODE, HEALTH_MODE),
        help="causes (upstream), effects (downstream), counterfactual (lost support), "
        "contention (weigh overlapping live claims; takes no SEQ), otel "
        "(project the graph onto OpenTelemetry spans; takes no SEQ), or health "
        "(flag orphaned claims, dangling dependencies, and stale claims; takes no SEQ).",
    )
    causality.add_argument("db", help="Path to the hub event store, e.g. ~/synapse/hub.db.")
    causality.add_argument(
        "seq",
        nargs="?",
        default=None,
        metavar="SEQ",
        help="Event sequence to query (HUB:SEQ with --peer); required for "
        "causes/effects/counterfactual.",
    )
    output = causality.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    output.add_argument(
        "--dot",
        action="store_true",
        help="Emit the federated causal neighbourhood as a Graphviz digraph — one "
        "cluster per hub, federation edges coloured and labelled with their basis; "
        "requires --peer.",
    )
    causality.add_argument(
        "--peer",
        action="append",
        default=[],
        metavar="HUB=PATH",
        help="Merge a peer hub's event store into a federated graph (repeatable); "
        "an edge whose endpoints two different hubs authored is tagged 'federation'.",
    )
    causality.add_argument(
        "--hub-id",
        default=None,
        help="Hub id of the primary DB in a federated query; defaults to the DB file name.",
    )
    causality.add_argument(
        "--out",
        default=None,
        metavar="FILE",
        help="otel mode: write the span records as JSON to this file (no extra dependency).",
    )
    causality.add_argument(
        "--endpoint",
        default=None,
        metavar="URL",
        help="otel mode: push OTLP/HTTP to a collector's full traces URL "
        "(e.g. http://localhost:4318/v1/traces); needs `pip install 'synapse-channel[otel]'`.",
    )
    causality.add_argument(
        "--service-name",
        default=None,
        metavar="NAME",
        help="otel mode: override the service.name resource on the exported spans "
        f"(default: {SERVICE_NAME}); distinguishes hubs sharing one observability tenant.",
    )
    causality.add_argument(
        "--filter",
        action="append",
        default=[],
        metavar="TASK_ID",
        help="otel mode: project only this task's trace (repeatable); a task the log "
        "does not record is refused. Links into excluded tasks are kept and the "
        "exclusions counted.",
    )
    causality.add_argument(
        "--watch",
        action="store_true",
        help="otel/health mode: repeat every --interval seconds until interrupted "
        "(Ctrl-C). otel re-projects and re-exports (deterministic ids make "
        "re-exports idempotent collector-side); health prints the first report "
        "as the baseline and then only '+ fact'/'- fact' anomaly transitions.",
    )
    causality.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Seconds between --watch ticks (default: 2.0).",
    )
    causality.add_argument(
        "--count",
        type=int,
        default=0,
        help="Stop --watch after this many ticks (0 = until interrupted).",
    )
    causality.add_argument(
        "--stale-after",
        type=float,
        default=None,
        metavar="SECONDS",
        help="health mode: flag an unreleased claim as stale after this much "
        f"log-relative silence (default: {DEFAULT_STALE_AFTER:.0f}).",
    )
    causality.add_argument(
        "--max-nodes",
        type=int,
        default=DEFAULT_MAX_GRAPH_NODES,
        help="Fail-closed ceiling on coordination events folded into the graph "
        "(0 lifts it); exceeding it errors instead of exhausting memory.",
    )
    causality.set_defaults(func=_cmd_causality)
