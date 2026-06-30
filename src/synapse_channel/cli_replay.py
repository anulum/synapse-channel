# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — replay debugger and deterministic-reproduction CLI commands
"""CLI wrappers for the event-log replay debugger and reproduction check.

``debug`` forks a task's reconstructed state at a sequence point (a read-only
what-if rewind); ``reproduce`` fingerprints a task's authoritative history into a
portable digest and optionally gates it against an expected value.
"""

from __future__ import annotations

import argparse
import json
import sys

from synapse_channel.core.replay import (
    fork_plan_to_json,
    load_task_for_seq,
    render_markdown,
    run_fork,
)
from synapse_channel.core.reproduce import (
    render_markdown as render_reproduction_markdown,
)
from synapse_channel.core.reproduce import (
    reproduction_to_json,
    run_reproduction,
    verify_reproduction,
)


def _parse_overrides(pairs: list[str]) -> dict[str, str]:
    """Parse ``key=value`` override pairs into a mapping.

    Parameters
    ----------
    pairs : list[str]
        Raw ``--set`` arguments.

    Returns
    -------
    dict[str, str]
        Field-to-value overrides.

    Raises
    ------
    ValueError
        If a pair has no ``=`` separator or an empty key.
    """
    overrides: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key.strip():
            msg = f"invalid --set '{pair}'; expected key=value"
            raise ValueError(msg)
        overrides[key.strip()] = value
    return overrides


def _cmd_debug(args: argparse.Namespace) -> int:
    """Fork a task's reconstructed state at a sequence point and print the plan."""
    try:
        overrides = _parse_overrides(args.set)
        task_id = args.task or load_task_for_seq(args.db, args.fork_at)
        if not task_id:
            print(
                f"no task found at seq {args.fork_at}; pass --task to name one",
                file=sys.stderr,
            )
            return 2
        plan = run_fork(args.db, task_id, fork_seq=args.fork_at, overrides=overrides)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(fork_plan_to_json(plan), indent=2, sort_keys=True))
    else:
        print(render_markdown(plan))
    return 0 if plan.held else 1


def _cmd_reproduce(args: argparse.Namespace) -> int:
    """Fingerprint a task's authoritative history and optionally gate it."""
    try:
        report = run_reproduction(args.db, args.task_id)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not report.present:
        print(f"no authoritative events for task '{report.task_id}'", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(reproduction_to_json(report), indent=2, sort_keys=True))
    else:
        print(render_reproduction_markdown(report))
    if args.expect:
        if verify_reproduction(report, args.expect):
            print(f"digest matches: {report.digest}", file=sys.stderr)
            return 0
        print(
            f"digest mismatch: expected {args.expect.strip().lower()}, got {report.digest}",
            file=sys.stderr,
        )
        return 1
    return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``debug`` and ``reproduce`` subparsers."""
    debug = subparsers.add_parser(
        "debug",
        help="Fork a task's reconstructed state at a sequence point (read-only what-if).",
    )
    debug.add_argument("db", help="Path to the hub event store, e.g. ~/synapse/hub.db.")
    debug.add_argument(
        "--fork-at",
        dest="fork_at",
        type=int,
        required=True,
        metavar="SEQ",
        help="Inclusive event sequence to rewind the task to.",
    )
    debug.add_argument(
        "--task",
        default="",
        help="Task id to fork; inferred from the event at SEQ when omitted.",
    )
    debug.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="FIELD=VALUE",
        help="Override a resume-manifest field (repeatable).",
    )
    debug.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    debug.set_defaults(func=_cmd_debug)

    reproduce = subparsers.add_parser(
        "reproduce",
        help="Fingerprint a task's authoritative history into a deterministic digest.",
    )
    reproduce.add_argument("db", help="Path to the hub event store, e.g. ~/synapse/hub.db.")
    reproduce.add_argument("task_id", help="Task id to fingerprint.")
    reproduce.add_argument(
        "--expect",
        default="",
        metavar="DIGEST",
        help="Gate on an expected sha256 digest; exit 1 on mismatch.",
    )
    reproduce.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    reproduce.set_defaults(func=_cmd_reproduce)
