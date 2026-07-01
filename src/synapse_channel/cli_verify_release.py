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
import os
import shlex
import sys
import tempfile
from pathlib import Path

from synapse_channel.core.merkle import MerkleRoot, run_root
from synapse_channel.core.release_verification import (
    build_verified_release_receipt,
    collect_git_state,
)


def _write_receipt_file(output: Path, payload: str) -> None:
    """Atomically write the receipt with owner-only permissions.

    The receipt records verification command argv and output digests; an argv can
    carry a secret, so the file is created ``0600`` via ``mkstemp`` and renamed
    into place, so a reader never observes a partial or world-readable receipt.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=output.parent, prefix=f".{output.name}.", suffix=".tmp")
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
        os.replace(temp, output)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def _cmd_verify_release(args: argparse.Namespace) -> int:
    """Run declared verification commands and write a receipt JSON document."""
    commands = [shlex.split(command) for command in args.run]
    git_state = collect_git_state(Path.cwd())
    merkle: MerkleRoot | None = None
    if args.merkle_db:
        try:
            merkle = run_root(args.merkle_db)
        except ValueError as exc:
            print(f"verify-release: {exc}", file=sys.stderr)
            return 2
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
        merkle=merkle,
    )
    payload = json.dumps(receipt, sort_keys=True)
    if args.output:
        output = Path(args.output)
        _write_receipt_file(output, payload)
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
    parser.add_argument(
        "--merkle-db",
        default="",
        metavar="FILE",
        help="Hub event store whose Merkle root is committed into the receipt, "
        "binding the release to the exact coordination history behind it; "
        "`synapse policy-check --merkle-db` re-verifies it later.",
    )
    parser.set_defaults(func=_cmd_verify_release)
