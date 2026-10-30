# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — contracts command
"""``synapse contracts check``: API contract conflicts between branches.

The command reads ``contracts.json`` at the base revision and compares each
named branch with it (see :mod:`synapse_channel.git.contract_conflicts`). It
prints one JSON report and exits 0 when no conflict is found, 1 when one is,
2 when the input cannot be used, and 3 when the base declares no contracts
(not configured, which is not the same as no conflict). It is advisory: it
never claims, releases or blocks anything.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from synapse_channel.git.contract_conflicts import MANIFEST, ContractError, check


def _check(args: argparse.Namespace) -> int:
    """Print the report; exit 0 none, 1 found, 2 refused, 3 not configured."""
    try:
        result = check(args.repo, args.base, args.branch)
    except (ContractError, OSError, UnicodeDecodeError) as exc:
        print(f"synapse contracts: {exc}", file=sys.stderr)
        return 2
    report = {
        **result.to_json(),
        "base": args.base,
        "manifest": MANIFEST,
        "advisory": "evidence only; nothing was claimed, released or blocked",
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    if not result.configured:
        return 3
    return 1 if result.conflicts else 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``contracts`` with its ``check`` subcommand.

    Parameters
    ----------
    subparsers : argparse._SubParsersAction
        The top-level ``synapse`` subcommand group.
    """
    group = subparsers.add_parser(
        "contracts", help="Find API contract conflicts between branches that share no file."
    )
    nested = group.add_subparsers(dest="contracts_command", required=True)
    run = nested.add_parser("check", help="Compare branches against the base's contracts.json.")
    run.add_argument("--repo", type=Path, default=Path.cwd(), help="Repository (default: here).")
    run.add_argument("--base", default="main", help="Base revision the branches merge into.")
    run.add_argument(
        "--branch", action="append", required=True, help="Branch to compare; repeat for each."
    )
    run.set_defaults(func=_check)
