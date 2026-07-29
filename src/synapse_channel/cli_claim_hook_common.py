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
HookConfigRenderer = Callable[..., dict[str, object]]
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
    """Return an absolute Synapse executable for a provider hook recipe.

    Prefer the console script on ``PATH``. When a bare ``synapse`` name is not on
    ``PATH`` — a contributor venv whose ``bin`` directory is not exported — fall
    back to the ``synapse`` script beside the running interpreter, so the recipe
    still resolves hermetically to an absolute path instead of failing closed.

    Parameters
    ----------
    explicit : str or None
        An operator-supplied executable path or name; ``None`` means the default
        ``synapse`` console script.

    Returns
    -------
    str
        The resolved absolute executable path.

    Raises
    ------
    ValueError
        If neither ``PATH`` nor the interpreter's directory yields the executable.
    """
    candidate = explicit or "synapse"
    found = shutil.which(candidate)
    if found is None and Path(candidate).name == candidate:
        beside = Path(sys.executable).parent / candidate
        if beside.is_file():
            found = str(beside)
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


def render_json_hook_config(
    *,
    command: str,
    event: str,
    matcher: str,
    identity: str,
    uri: str,
    ready_timeout: float,
    token_file: str | None,
    synapse_bin: str | None,
    timeout_multiplier: int = 1,
    status_message: str | None = None,
) -> dict[str, object]:
    """Build the shell-command JSON envelope shared by provider hook stores."""
    hook: dict[str, object] = {
        "type": "command",
        "command": render_hook_command(
            command=command,
            identity=identity,
            uri=uri,
            ready_timeout=ready_timeout,
            token_file=token_file,
            synapse_bin=synapse_bin,
        ),
        "timeout": hook_timeout(ready_timeout) * timeout_multiplier,
    }
    if status_message is not None:
        hook["statusMessage"] = status_message
    return {
        "hooks": {
            event: [
                {
                    "matcher": matcher,
                    "hooks": [hook],
                }
            ]
        }
    }


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
    timeout_normalizer: Callable[[float], float] = normalise_ready_timeout,
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
                timeout=timeout_normalizer(float(args.ready_timeout)),
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


def run_json_claim_hook_command(
    args: argparse.Namespace,
    *,
    provider: str,
    config_renderer: HookConfigRenderer,
    evaluator: HookEvaluator,
    failure_reason: str,
    async_runner: Callable[[Awaitable[GuardVerdict]], GuardVerdict] = _run_awaitable,
    payload_renderer: Callable[[str], dict[str, object]] | None = None,
    reject_raw_token: bool = True,
    timeout_normalizer: Callable[[float], float] = normalise_ready_timeout,
) -> int:
    """Run the common JSON recipe/runtime shell around one provider protocol."""
    if args.print_config:
        if reject_raw_token and not recipe_inputs_are_safe(args, provider=provider):
            return 2
        try:
            config = config_renderer(
                identity=args.identity,
                uri=args.uri,
                ready_timeout=args.ready_timeout,
                token_file=args.token_file,
                synapse_bin=args.synapse_bin,
            )
        except (OSError, ValueError) as exc:
            print(f"cannot render {provider} claim-hook config: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(config, indent=2, ensure_ascii=False))
        return 0

    return run_claim_hook(
        args,
        evaluator=evaluator,
        failure_reason=failure_reason,
        async_runner=async_runner,
        payload_renderer=payload_renderer,
        timeout_normalizer=timeout_normalizer,
    )


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
