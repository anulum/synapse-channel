# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Claude Code claim-guard CLI and configuration recipe
"""Register and run the Claude Code ``Edit|Write`` claim guard.

The runtime reads Claude's ``PreToolUse`` JSON from stdin. ``--print-config``
instead emits a mergeable exec-form settings fragment and never writes a Claude
configuration file. Keeping the recipe read-only avoids clobbering an operator's
existing permissions or hooks.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import shutil
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from synapse_channel.claude_claim_guard import (
    GuardVerdict,
    denial_payload,
    evaluate_hook_event,
)
from synapse_channel.client.agent import default_hub_uri

HookEvaluator = Callable[..., Awaitable[GuardVerdict]]
_MIN_READY_TIMEOUT = 0.1


def _normalise_ready_timeout(value: float) -> float:
    """Return the bounded finite-positive timeout shared by every CLI path."""
    if not math.isfinite(value) or value <= 0:
        raise ValueError("--ready-timeout must be finite and greater than zero")
    return max(_MIN_READY_TIMEOUT, value)


def _parse_ready_timeout(value: str) -> float:
    """Parse one finite-positive ``--ready-timeout`` value for argparse."""
    try:
        return _normalise_ready_timeout(float(value))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


async def _await_verdict(awaitable: Awaitable[GuardVerdict]) -> GuardVerdict:
    """Turn a general awaitable into the coroutine required by :func:`asyncio.run`."""
    return await awaitable


def _run_awaitable(awaitable: Awaitable[GuardVerdict]) -> GuardVerdict:
    """Run one hook verdict awaitable to completion."""
    return asyncio.run(_await_verdict(awaitable))


def _resolve_synapse_binary(explicit: str | None) -> str:
    """Return an absolute executable for an exec-form Claude hook recipe."""
    candidate = explicit or "synapse"
    found = shutil.which(candidate)
    if found is None:
        raise ValueError(f"cannot resolve Synapse executable {candidate!r}")
    return str(Path(found).expanduser().resolve())


def render_hook_config(
    *,
    identity: str,
    uri: str,
    ready_timeout: float,
    token_file: str | None,
    synapse_bin: str | None,
) -> dict[str, Any]:
    """Return a mergeable Claude settings fragment for the claim guard.

    The handler timeout exceeds the complete connect-plus-snapshot deadline, so
    expected hub failures reach the structured deny path instead of being killed
    by Claude first. Secrets are never embedded; a secured hub is referenced only
    through ``--token-file``.
    """
    bounded_timeout = _normalise_ready_timeout(float(ready_timeout))
    args = [
        "adapters",
        "claude-claim-hook",
        "--identity",
        identity,
        "--uri",
        uri,
        "--ready-timeout",
        str(bounded_timeout),
    ]
    if token_file:
        args.extend(["--token-file", str(Path(token_file).expanduser().resolve())])
    hook_timeout = max(5, math.ceil(2 * bounded_timeout + 2))
    return {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Edit|Write",
                    "hooks": [
                        {
                            "type": "command",
                            "command": _resolve_synapse_binary(synapse_bin),
                            "args": args,
                            "timeout": hook_timeout,
                            "statusMessage": "Verifying Synapse file claim",
                        }
                    ],
                }
            ]
        }
    }


def _cmd_claude_claim_hook(
    args: argparse.Namespace,
    *,
    evaluator: HookEvaluator = evaluate_hook_event,
    async_runner: Callable[[Awaitable[GuardVerdict]], GuardVerdict] = _run_awaitable,
) -> int:
    """Print a recipe or evaluate stdin, returning only safe hook outcomes."""
    if args.print_config:
        try:
            config = render_hook_config(
                identity=args.identity,
                uri=args.uri,
                ready_timeout=args.ready_timeout,
                token_file=args.token_file,
                synapse_bin=args.synapse_bin,
            )
        except (OSError, ValueError) as exc:
            print(f"cannot render Claude claim-hook config: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(config, indent=2, ensure_ascii=False))
        return 0

    raw = sys.stdin.read()
    try:
        verdict = async_runner(
            evaluator(
                raw,
                identity=args.identity,
                uri=args.uri,
                token=args.token,
                timeout=_normalise_ready_timeout(float(args.ready_timeout)),
            )
        )
    except Exception:
        # Claude treats exit 1 as non-blocking. Convert every unexpected runtime
        # exception into valid deny JSON on exit 0 so a bug cannot authorise a write.
        verdict = GuardVerdict(False, "Synapse claim verification failed; Edit/Write denied.")
    if verdict.allowed:
        return 0
    print(json.dumps(denial_payload(verdict.reason), ensure_ascii=False))
    return 0


def add_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the nested ``adapters claude-claim-hook`` command."""
    parser = subparsers.add_parser(
        "claude-claim-hook",
        help="Guard Claude Code Edit/Write calls with live Synapse file claims.",
    )
    parser.add_argument("--identity", required=True, help="Exact identity that must own the claim.")
    parser.add_argument("--uri", default=default_hub_uri(), help="Authoritative Synapse hub URI.")
    parser.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    parser.add_argument(
        "--token-file",
        default=None,
        help="Read the hub token from this file; config recipes embed only this path.",
    )
    parser.add_argument(
        "--ready-timeout",
        type=_parse_ready_timeout,
        default=2.0,
        help="Seconds allowed for each connect and state-snapshot phase (default: 2).",
    )
    parser.add_argument(
        "--print-config",
        action="store_true",
        help="Print a mergeable Claude settings fragment instead of reading stdin.",
    )
    parser.add_argument(
        "--synapse-bin",
        default=None,
        help="Executable to resolve into the printed exec-form recipe.",
    )
    parser.set_defaults(func=_cmd_claude_claim_hook)
