# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — staged Git claim-check CLI
"""Thin CLI adapter for the read-only staged path claim gate."""

from __future__ import annotations

import argparse
import asyncio
import math
import sys
from collections.abc import Callable, Coroutine
from typing import Any

from synapse_channel.claim_state import MAX_CLAIM_STATE_PHASE_TIMEOUT
from synapse_channel.git.staged_claim_check import (
    StagedClaimCheckResult,
    run_staged_claim_check,
)

ClaimChecker = Callable[..., Coroutine[Any, Any, StagedClaimCheckResult]]


def _phase_timeout(value: str) -> float:
    try:
        timeout = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a number") from exc
    if not math.isfinite(timeout) or timeout <= 0 or timeout > MAX_CLAIM_STATE_PHASE_TIMEOUT:
        raise argparse.ArgumentTypeError(
            f"timeout must be greater than zero and at most {MAX_CLAIM_STATE_PHASE_TIMEOUT:g}"
        )
    return timeout


def _cmd_git_claim_check(
    args: argparse.Namespace,
    *,
    checker: ClaimChecker = run_staged_claim_check,
    async_runner: Callable[
        [Coroutine[Any, Any, StagedClaimCheckResult]], StagedClaimCheckResult
    ] = asyncio.run,
) -> int:
    """Run the staged gate and print one focused commit/repair outcome."""
    result = async_runner(
        checker(
            identity=args.name,
            uri=args.uri,
            token_file=args.token_file,
            timeout=args.timeout,
        )
    )
    if result.allowed:
        if result.paths:
            print(f"staged claim coverage: OK ({len(result.paths)} paths)")
        else:
            print("staged claim coverage: no staged paths")
        return 0
    print(f"staged claim coverage denied: {result.reason}", file=sys.stderr)
    print(
        "repair: acquire an exact path claim, or refresh identity with "
        "`synapse git-init --name <exact-owner>`, then retry the commit.",
        file=sys.stderr,
    )
    return 1


def add_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``git-claim-check`` as its own lazy CLI unit."""
    parser = subparsers.add_parser(
        "git-claim-check",
        help="Fail unless one identity owns editable claims for every staged path.",
    )
    parser.add_argument(
        "--staged",
        action="store_true",
        required=True,
        help="Read paths from Git's staged index; filenames from hooks are ignored.",
    )
    parser.add_argument("--name", default=None, help="Exact claim owner; other sources must agree.")
    parser.add_argument(
        "--uri", default=None, help="Hub URI; defaults to repo config or SYNAPSE_URI."
    )
    parser.add_argument(
        "--token-file",
        default=None,
        help=(
            "Read the shared-secret token from this file; token values are never accepted in argv."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=_phase_timeout,
        default=5.0,
        help="Per-phase hub deadline in seconds (default: 5).",
    )
    parser.set_defaults(func=_cmd_git_claim_check)
