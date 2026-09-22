# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — human app task queue CLI
"""Owner-local commands for explicit app handoffs and verified results."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from synapse_channel.core import app_tasks
from synapse_channel.core.secure_path import SecurePathError, read_owner_only_file_bytes


def _input(path: str) -> dict[str, Any]:
    raw = read_owner_only_file_bytes(Path(path), purpose="app task input", max_bytes=1_048_576)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise app_tasks.AppTaskError("input must be one UTF-8 JSON object") from exc
    if not isinstance(value, dict):
        raise app_tasks.AppTaskError("input must be one JSON object")
    return value


def _run(args: argparse.Namespace) -> int:
    store = Path(args.store) if args.store else app_tasks.default_app_task_store()
    result: object
    try:
        if args.action == "offer":
            result = app_tasks.offer(
                store,
                _input(args.file),
                ledger=Path(args.ledger) if args.ledger else None,
            )
        elif args.action == "attach":
            result = app_tasks.attach(store, args.task_id, _input(args.file))
        elif args.action == "show":
            result = app_tasks.get(store, args.task_id)
        elif args.action == "history":
            result = app_tasks.history(store, args.task_id)
        elif args.action == "verify":
            result = app_tasks.verify(store, args.task_id)
        elif args.action == "correct-usage":
            result = app_tasks.correct_usage(store, args.task_id, args.amount, args.reason)
        else:
            result = app_tasks.advance(store, args.task_id, args.action)
    except (app_tasks.AppTaskError, SecurePathError, OSError, ValueError) as exc:
        print(f"app-task: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


def add_parsers(group: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register one local queue surface without app automation."""
    root = group.add_parser("app-task", help="Queue explicit human app work locally.")
    root.add_argument("--store", help="Owner-only task database; default is XDG state home.")
    sub = root.add_subparsers(dest="action", required=True)
    for action in (
        "offer",
        "accept",
        "start",
        "attach",
        "verify",
        "decline",
        "cancel",
        "show",
        "history",
        "correct-usage",
    ):
        parser = sub.add_parser(action)
        if action in {"offer", "attach"}:
            parser.add_argument("--file", required=True, help="Owner-only JSON input file.")
        if action == "offer":
            parser.add_argument("--ledger", help="Owner-only C04 entitlement ledger.")
        else:
            parser.add_argument("task_id")
        if action == "correct-usage":
            parser.add_argument("--amount", required=True)
            parser.add_argument("--reason", required=True)
        parser.set_defaults(func=_run)
