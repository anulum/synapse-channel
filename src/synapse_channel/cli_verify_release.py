# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — verified release receipt CLI
"""Command-line support for observed release verification receipts."""

from __future__ import annotations

import argparse
import json
import shlex
from pathlib import Path

from synapse_channel.core.release_verification import (
    build_verified_release_receipt,
    collect_git_state,
)


def _cmd_verify_release(args: argparse.Namespace) -> int:
    """Run declared verification commands and write a receipt JSON document."""
    commands = [shlex.split(command) for command in args.run]
    git_state = collect_git_state(Path.cwd())
    receipt = build_verified_release_receipt(
        task_id=args.task_id,
        owner=args.name,
        commands=commands,
        artifacts=args.artifacts,
        changed_files=git_state.changed_files,
        git_head=git_state.head,
        git_tree=git_state.tree,
        signature=args.signature,
        cwd=Path.cwd(),
    )
    payload = json.dumps(receipt, sort_keys=True)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload + "\n", encoding="utf-8")
        print(f"verified release receipt: {output}")
    else:
        print(payload)
    return 1 if receipt["known_failures"] else 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``verify-release`` subcommand."""
    parser = subparsers.add_parser(
        "verify-release",
        help="Run verification commands and write an observed release receipt JSON.",
    )
    parser.add_argument("task_id", help="Claim id the receipt will release.")
    parser.add_argument("--name", default="USER", help="Releasing identity for the receipt.")
    parser.add_argument(
        "--run",
        action="append",
        default=[],
        metavar="COMMAND",
        help="Verification command to run; repeat for multiple commands.",
    )
    parser.add_argument(
        "--artifact",
        dest="artifacts",
        action="append",
        default=[],
        help="Artifact file whose SHA-256 digest should be recorded.",
    )
    parser.add_argument("--output", default="", help="Write receipt JSON to this path.")
    parser.add_argument(
        "--signature",
        default="",
        help="Optional signature or signature reference to carry in the receipt.",
    )
    parser.set_defaults(func=_cmd_verify_release)
