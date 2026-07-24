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
edge. ``--watch`` rescans and reprints every ``--interval`` seconds — a
standing dashboard over the same evidence. ``--suggest-resolution`` turns
each detected version conflict into advice: the intersection of all
consumers' declared ranges names which repository's constraint is the odd
one out and what the rest already reconcile at — advisory text only,
nothing rewrites a manifest. ``--watch --notify-cmd CMD`` runs an operator
command whenever the coordination signal *changes* between refreshes — a
live claim appearing or clearing in a scanned repository, a version
conflict appearing or resolving — with the delta on stdin, so desktop
notifications, ``synapse send``, or any other sink wires up without
coupling the scanner to a live hub.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess  # nosec B404
import sys
import time
from collections.abc import Callable
from typing import TextIO

from synapse_channel.core.cross_repo_graph import (
    SELF_RELATION,
    VERSION_CONFLICT_EDGE,
    CrossRepoGraph,
    cross_repo_graph_to_json,
    render_cross_repo_dot,
    render_cross_repo_human,
    run_cross_repo_graph,
)
from synapse_channel.core.version_resolution import (
    render_resolution_markdown,
    resolution_to_json,
    run_resolution_advice,
)

NOTIFY_TIMEOUT_SECONDS = 60.0
"""Ceiling on one notify-command run, so a hung sink cannot stall the watch."""


def _claim_signal(graph: CrossRepoGraph, focus: str | None) -> int:
    """Return the ``--repo`` coordination exit code for one scanned graph."""
    if focus is not None and any(claim.relation != SELF_RELATION for claim in graph.claims):
        return 1
    return 0


def coordination_facts(graph: CrossRepoGraph) -> frozenset[str]:
    """Return one line per observable coordination fact in a scanned graph.

    The facts a watcher wants to be told about: every live claim joined to
    the graph and every provable version conflict. Each renders as a stable
    one-line identity, so two refreshes diff by plain set difference.
    """
    facts = {
        f"claim {claim.repo} {claim.task_id}@{claim.owner} [{claim.relation}]"
        for claim in graph.claims
    }
    facts.update(
        f"version_conflict {edge.source}<->{edge.target} {edge.evidence.get('package', '')}"
        for edge in graph.edges
        if edge.kind == VERSION_CONFLICT_EDGE
    )
    return frozenset(facts)


def run_notify_command(command: str, added: list[str], removed: list[str], root: str) -> None:
    """Run the operator's notify command with the coordination delta on stdin.

    The command is split with :func:`shlex.split` and executed without a
    shell — an operator wanting pipes or redirection wraps the command in
    ``sh -c '…'`` explicitly. Appeared facts arrive as ``+ fact`` lines and
    cleared facts as ``- fact`` lines; the scanned root is exposed as
    ``SYNAPSE_CROSS_REPO_ROOT``. A failing or hanging sink is reported on
    stderr and never stops the watch — notification is best-effort, the
    report is the record.
    """
    summary = (
        "\n".join(
            [f"+ {fact}" for fact in sorted(added)] + [f"- {fact}" for fact in sorted(removed)]
        )
        + "\n"
    )
    try:
        # On Windows, normalise ``\`` to ``/`` before POSIX shlex so drive paths
        # stay intact (posix=True would eat backslashes; posix=False keeps quotes
        # inside -c payloads). CreateProcess accepts forward-slash paths.
        to_split = command.replace("\\", "/") if os.name == "nt" else command
        argv = shlex.split(to_split, posix=True)
        completed = subprocess.run(  # nosec B603
            argv,
            input=summary,
            text=True,
            timeout=NOTIFY_TIMEOUT_SECONDS,
            env={**os.environ, "SYNAPSE_CROSS_REPO_ROOT": root},
            check=False,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"notify command failed: {exc}", file=sys.stderr)
        return
    if completed.returncode != 0:
        print(f"notify command exited {completed.returncode}", file=sys.stderr)


def watch_cross_repo(
    *,
    root: str,
    db: str | None,
    focus: str | None,
    as_json: bool,
    interval: float,
    count: int,
    notify_cmd: str | None = None,
    out: TextIO | None = None,
    sleeper: Callable[[float], None] | None = None,
    key_file: str | None = None,
) -> int:
    """Rescan and reprint the cross-repository report on a fixed cadence.

    Each refresh is one full :func:`run_cross_repo_graph` pass — manifests
    are reread and claims rejoined, so repository edits and hub activity
    show up on the next tick. On a TTY the screen is cleared and the report
    redrawn in place; piped output appends each report behind a ``---``
    divider line so a consumer can split refreshes, and ``--json`` emits one
    compact JSON document per line (NDJSON) either way. ``count`` bounds the
    refreshes (``0`` runs until interrupted).

    Parameters
    ----------
    root, db, focus
        Passed through to :func:`run_cross_repo_graph` unchanged.
    as_json : bool
        Emit NDJSON instead of the human report.
    interval : float
        Seconds between refreshes.
    count : int
        Refreshes to run; ``0`` means until interrupted.
    notify_cmd : str or None, optional
        Operator command run via :func:`run_notify_command` whenever the
        coordination facts (:func:`coordination_facts`) CHANGE between two
        consecutive refreshes. Fires on transitions only — never on the
        first refresh, which establishes the baseline, and never on a
        steady state, so a quiet fleet stays quiet.
    out : typing.TextIO or None, optional
        Output stream; defaults to ``sys.stdout``.
    sleeper : Callable[[float], None] or None, optional
        Sleep function between refreshes; ``None`` uses :func:`time.sleep`
        (resolved per call, so a patched clock takes effect). Injectable
        for testing.

    Returns
    -------
    int
        The LAST refresh's coordination signal — ``1`` when ``--repo`` is
        set and a connected repository holds a live claim, else ``0`` — or
        ``2`` when a refresh fails (missing root, store, or focus).
    """
    stream = sys.stdout if out is None else out
    sleep = time.sleep if sleeper is None else sleeper
    in_place = stream.isatty() and not as_json
    exit_code = 0
    refreshes = 0
    previous_facts: frozenset[str] | None = None
    while True:
        try:
            graph = run_cross_repo_graph(root, db_path=db, focus=focus, key_file=key_file)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if as_json:
            stream.write(json.dumps(cross_repo_graph_to_json(graph), sort_keys=True) + "\n")
        else:
            if in_place:
                stream.write("\x1b[H\x1b[2J")
            elif refreshes:
                stream.write("---\n")
            stream.write(render_cross_repo_human(graph) + "\n")
        stream.flush()
        if notify_cmd is not None:
            facts = coordination_facts(graph)
            if previous_facts is not None and facts != previous_facts:
                run_notify_command(
                    notify_cmd,
                    sorted(facts - previous_facts),
                    sorted(previous_facts - facts),
                    root,
                )
            previous_facts = facts
        exit_code = _claim_signal(graph, focus)
        refreshes += 1
        if count and refreshes >= count:
            return exit_code
        sleep(interval)


def _cmd_cross_repo(args: argparse.Namespace) -> int:
    """Scan, optionally join claims, and print one cross-repository report.

    With ``--watch`` the report refreshes every ``--interval`` seconds until
    ``--count`` refreshes ran or the operator interrupts; Ctrl-C is the
    normal way to stop a watch, so it exits ``0`` rather than tracing.
    """
    if args.suggest_resolution and (args.watch or args.dot):
        print("--suggest-resolution does not combine with --watch or --dot", file=sys.stderr)
        return 2
    if args.notify_cmd is not None and not args.watch:
        print("--notify-cmd fires on watch transitions; it requires --watch", file=sys.stderr)
        return 2
    key_file = getattr(args, "db_key_file", None)
    if args.watch:
        if args.dot:
            print("--watch does not combine with --dot", file=sys.stderr)
            return 2
        if args.interval <= 0:
            print("--interval must be positive", file=sys.stderr)
            return 2
        try:
            return watch_cross_repo(
                root=args.root,
                db=args.db,
                focus=args.repo,
                as_json=args.json,
                interval=args.interval,
                count=args.count,
                notify_cmd=args.notify_cmd,
                key_file=key_file,
            )
        except KeyboardInterrupt:
            return 0
    try:
        graph = run_cross_repo_graph(args.root, db_path=args.db, focus=args.repo, key_file=key_file)
        advice = run_resolution_advice(args.root) if args.suggest_resolution else ()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        payload = cross_repo_graph_to_json(graph)
        if args.suggest_resolution:
            payload["resolutions"] = resolution_to_json(advice)
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif args.dot:
        print(render_cross_repo_dot(graph))
    else:
        print(render_cross_repo_human(graph))
        if args.suggest_resolution:
            print()
            print(render_resolution_markdown(advice))
    return _claim_signal(graph, args.repo)


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
        "--db-key-file",
        default=None,
        help="Owner-only SQLCipher key for an encrypted hub event store (--db).",
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
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Rescan and reprint every --interval seconds until interrupted (Ctrl-C).",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Seconds between --watch refreshes.",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=0,
        help="Stop after this many --watch refreshes (0 = until interrupted).",
    )
    parser.add_argument(
        "--suggest-resolution",
        action="store_true",
        help="For each detected version conflict, name the odd-one-out declaration "
        "and the range the other consumers reconcile at (advisory text; nothing "
        "rewrites a manifest).",
    )
    parser.add_argument(
        "--notify-cmd",
        default=None,
        metavar="CMD",
        help="Run CMD (shlex-split, no shell; wrap in `sh -c` for pipes) whenever the "
        "coordination facts change between --watch refreshes, with the delta on "
        "stdin (+/- lines) and SYNAPSE_CROSS_REPO_ROOT in the environment; "
        "requires --watch.",
    )
    parser.set_defaults(func=_cmd_cross_repo)
