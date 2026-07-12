# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — shared provider claim-hook CLI mechanics
"""Bound timeouts, render safe commands, and fail closed around hook runtimes."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import shlex
import shutil
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

from synapse_channel.claim_state import MAX_CLAIM_STATE_PHASE_TIMEOUT
from synapse_channel.client.agent import default_hub_uri
from synapse_channel.file_claim_guard import GuardVerdict

HookEvaluator = Callable[..., Awaitable[GuardVerdict]]
_MIN_READY_TIMEOUT = 0.1
_MAX_HOOK_READY_TIMEOUT = min(MAX_CLAIM_STATE_PHASE_TIMEOUT, 299.0)


def normalise_ready_timeout(value: float) -> float:
    """Return a finite deadline whose two phases fit a 600-second hook limit."""
    if not math.isfinite(value) or value <= 0 or value > _MAX_HOOK_READY_TIMEOUT:
        raise ValueError(
            "--ready-timeout must be finite, greater than zero, "
            f"and at most {_MAX_HOOK_READY_TIMEOUT:g} seconds"
        )
    return max(_MIN_READY_TIMEOUT, value)


def parse_ready_timeout(value: str) -> float:
    """Parse one finite-positive ``--ready-timeout`` value for argparse."""
    try:
        return normalise_ready_timeout(float(value))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


async def _await_verdict(awaitable: Awaitable[GuardVerdict]) -> GuardVerdict:
    return await awaitable


def _run_awaitable(awaitable: Awaitable[GuardVerdict]) -> GuardVerdict:
    return asyncio.run(_await_verdict(awaitable))


def resolve_synapse_binary(explicit: str | None) -> str:
    """Return an absolute Synapse executable for a provider hook recipe."""
    candidate = explicit or "synapse"
    found = shutil.which(candidate)
    if found is None:
        raise ValueError(f"cannot resolve Synapse executable {candidate!r}")
    return str(Path(found).expanduser().resolve())


def hook_timeout(ready_timeout: float) -> int:
    """Leave process-level headroom beyond both authoritative query phases."""
    return max(5, math.ceil(2 * normalise_ready_timeout(ready_timeout) + 2))


def render_hook_command(
    *,
    command: str,
    identity: str,
    uri: str,
    ready_timeout: float,
    token_file: str | None,
    synapse_bin: str | None,
) -> str:
    """Render one shell-safe command without embedding token contents."""
    args = [
        resolve_synapse_binary(synapse_bin),
        "adapters",
        command,
        "--identity",
        identity,
        "--uri",
        uri,
        "--ready-timeout",
        str(normalise_ready_timeout(ready_timeout)),
    ]
    if token_file:
        args.extend(["--token-file", str(Path(token_file).expanduser().resolve())])
    return shlex.join(args)


def recipe_inputs_are_safe(args: argparse.Namespace, *, provider: str) -> bool:
    """Reject a raw token that a persistent provider recipe cannot safely carry."""
    if args.token and not args.token_file:
        print(
            f"{provider} hook recipes never embed --token; store it in a private file "
            "and use --token-file.",
            file=sys.stderr,
        )
        return False
    return True


def run_claim_hook(
    args: argparse.Namespace,
    *,
    evaluator: HookEvaluator,
    failure_reason: str,
    async_runner: Callable[[Awaitable[GuardVerdict]], GuardVerdict] = _run_awaitable,
    payload_renderer: Callable[[str], dict[str, object]] | None = None,
) -> int:
    """Evaluate stdin and convert every handled failure to deny JSON on exit zero.

    ``payload_renderer`` maps a denial reason to the provider's structured deny object;
    when omitted, the Claude-family ``PreToolUse`` shape shared by Codex and Kimi
    applies. Gemini passes its native top-level ``decision``/``reason`` renderer.
    """
    raw = sys.stdin.read()
    try:
        verdict = async_runner(
            evaluator(
                raw,
                identity=args.identity,
                uri=args.uri,
                token=args.token,
                timeout=normalise_ready_timeout(float(args.ready_timeout)),
            )
        )
    except Exception:
        verdict = GuardVerdict(False, failure_reason)
    if verdict.allowed:
        return 0
    if payload_renderer is None:
        from synapse_channel.file_claim_guard import denial_payload

        payload_renderer = denial_payload
    print(json.dumps(payload_renderer(verdict.reason), ensure_ascii=False))
    return 0


def add_claim_hook_arguments(
    parser: argparse.ArgumentParser, *, identity_required: bool = True
) -> None:
    """Register the runtime and read-only recipe fields shared by provider hooks."""
    parser.add_argument(
        "--identity",
        required=identity_required,
        default=None,
        help="Exact identity that must own the claim.",
    )
    parser.add_argument("--uri", default=default_hub_uri(), help="Authoritative Synapse hub URI.")
    parser.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    parser.add_argument(
        "--token-file",
        default=None,
        help="Read the hub token from this file; config recipes embed only this path.",
    )
    parser.add_argument(
        "--ready-timeout",
        type=parse_ready_timeout,
        default=2.0,
        help="Seconds allowed for each connect and state-snapshot phase (default: 2).",
    )
    parser.add_argument(
        "--print-config",
        action="store_true",
        help="Print a mergeable provider hook fragment instead of reading stdin.",
    )
    parser.add_argument(
        "--synapse-bin",
        default=None,
        help="Executable to resolve into the printed provider recipe.",
    )
