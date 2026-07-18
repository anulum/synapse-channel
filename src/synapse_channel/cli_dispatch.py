# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dispatcher CLI command
"""CLI command for the opt-in ready-task dispatcher."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from synapse_channel.client.agent import default_hub_uri
from synapse_channel.client.dispatcher import DispatcherWorker


def _cmd_dispatch(
    args: argparse.Namespace,
    *,
    runner: Callable[[Coroutine[Any, Any, int]], int] = asyncio.run,
) -> int:
    """Run the dispatcher for one project until interrupted (or one pass)."""
    worker = DispatcherWorker(
        project=args.project,
        name=args.name,
        uri=args.uri,
        token=args.token,
        interval=args.interval,
        once=args.once,
        dry_run=args.dry_run,
        suggestion_ttl=args.suggestion_ttl,
        capacity=args.capacity,
        max_attempts=args.max_attempts,
        outbox_path=Path(args.outbox) if args.outbox else None,
        ready_timeout=args.ready_timeout,
    )
    try:
        return runner(worker.run())
    except KeyboardInterrupt:
        print(f"\n[{worker.name}] dispatcher stopped by user.")
        return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``dispatch`` subcommand."""
    dispatch = subparsers.add_parser(
        "dispatch",
        help="Nudge project seats about ready board tasks, exactly once (opt-in).",
    )
    dispatch.add_argument(
        "--project",
        required=True,
        help="Exact project scope; only tasks and cards of this project qualify.",
    )
    dispatch.add_argument(
        "--name",
        default="",
        help="Connection identity; defaults to <project>/dispatcher.",
    )
    dispatch.add_argument("--uri", default=default_hub_uri())
    dispatch.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    dispatch.add_argument(
        "--interval", type=float, default=60.0, help="Seconds between passes (floored at 1)."
    )
    dispatch.add_argument("--once", action="store_true", help="Run a single pass and exit.")
    dispatch.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the deterministic plan without any mutation or wake.",
    )
    dispatch.add_argument(
        "--suggestion-ttl",
        type=float,
        default=900.0,
        help="Seconds before an un-claimed suggestion re-opens (default 900).",
    )
    dispatch.add_argument(
        "--capacity",
        type=int,
        default=1,
        help="Maximum active claims per seat (default 1).",
    )
    dispatch.add_argument(
        "--max-attempts",
        type=int,
        default=3,
        help="Wake retries per assignment before abandonment (default 3).",
    )
    dispatch.add_argument(
        "--outbox",
        default=None,
        help="JSONL outbox path; defaults to ~/.synapse/dispatch-outbox/<project>.jsonl.",
    )
    dispatch.add_argument(
        "--ready-timeout", type=float, default=5.0, help="Seconds to await hub readiness."
    )
    dispatch.set_defaults(func=_cmd_dispatch)
