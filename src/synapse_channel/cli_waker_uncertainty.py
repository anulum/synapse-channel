# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — list and resolve uncertain wakes
"""``synapse waker uncertain`` and ``synapse waker resolve``.

``uncertain`` reports every unresolved uncertain wake as JSON lines and exits
with status 3 when at least one of them is an operator question. ``resolve``
records an operator's decision once. Neither command runs a wake.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from synapse_channel import wake_marks
from synapse_channel.wake_uncertainty import DEFAULT_QUESTION_AFTER_SECONDS, unresolved

QUESTIONS_PENDING = 3
"""Exit status of ``uncertain`` when an operator question is pending."""


def _marks_dir(args: argparse.Namespace) -> Path:
    return Path(args.marks_dir) if args.marks_dir else wake_marks.default_marks_dir()


def _uncertain(args: argparse.Namespace) -> int:
    """Print one JSON object per unresolved uncertain wake."""
    try:
        rows = unresolved(
            _marks_dir(args),
            now=datetime.now(timezone.utc),
            question_after_seconds=args.question_after,
        )
    except (OSError, ValueError) as exc:
        print(f"waker uncertain: {exc}", file=sys.stderr)
        return 2
    for row in rows:
        print(json.dumps(row.to_json(), sort_keys=True))
    return QUESTIONS_PENDING if any(row.question for row in rows) else 0


def _resolve(args: argparse.Namespace) -> int:
    """Record one operator decision; never run the wake again."""
    try:
        wake_marks.resolve(
            _marks_dir(args),
            args.seat,
            args.wake_id,
            args.decision,
            args.operator,
            now=datetime.now(timezone.utc),
        )
    except FileExistsError:
        print("waker resolve: this wake is already resolved", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"waker resolve: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"seat": args.seat, "wake_id": args.wake_id, "resolved": args.decision}))
    return 0


def add_parsers(nested: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``uncertain`` and ``resolve`` in the ``waker`` command group.

    Parameters
    ----------
    nested : argparse._SubParsersAction
        The ``synapse waker`` subcommand group.
    """
    uncertain = nested.add_parser(
        "uncertain", help="List uncertain wakes no operator has resolved; never wakes."
    )
    uncertain.add_argument("--marks-dir", help="Marks directory (default: the data directory).")
    uncertain.add_argument(
        "--question-after",
        type=float,
        default=DEFAULT_QUESTION_AFTER_SECONDS,
        help="Seconds after which an unresolved wake becomes an operator question.",
    )
    uncertain.set_defaults(func=_uncertain)

    resolve = nested.add_parser(
        "resolve", help="Record an operator decision about one uncertain wake; never wakes."
    )
    resolve.add_argument("--marks-dir", help="Marks directory (default: the data directory).")
    resolve.add_argument("--seat", required=True, help="Exact seat of the wake.")
    resolve.add_argument("--wake-id", required=True, type=int, help="Hub sequence of the message.")
    resolve.add_argument(
        "--as",
        dest="decision",
        required=True,
        choices=sorted(wake_marks.RESOLUTIONS),
        help="read: the seat read the message; abandoned: the wake is given up.",
    )
    resolve.add_argument("--operator", required=True, help="Who decides; kept in the mark.")
    resolve.set_defaults(func=_resolve)
