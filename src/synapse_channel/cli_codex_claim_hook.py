# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Codex apply_patch claim-hook CLI and recipe
"""Run the Codex claim guard or print a mergeable ``hooks.json`` fragment."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from synapse_channel.claim_state import fetch_state_snapshot
from synapse_channel.cli_claim_hook_common import (
    add_claim_hook_arguments,
    hook_timeout,
    recipe_inputs_are_safe,
    render_hook_command,
    run_claim_hook,
)
from synapse_channel.codex_claim_guard import evaluate_hook_event
from synapse_channel.file_claim_guard import GuardVerdict


def render_hook_config(
    *,
    identity: str,
    uri: str,
    ready_timeout: float,
    token_file: str | None,
    synapse_bin: str | None,
) -> dict[str, Any]:
    """Return a token-safe Codex ``hooks.json`` fragment for ``apply_patch``."""
    command = render_hook_command(
        command="codex-claim-hook",
        identity=identity,
        uri=uri,
        ready_timeout=ready_timeout,
        token_file=token_file,
        synapse_bin=synapse_bin,
    )
    return {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Edit|Write",
                    "hooks": [
                        {
                            "type": "command",
                            "command": command,
                            "timeout": hook_timeout(ready_timeout),
                            "statusMessage": "Verifying Synapse file claims",
                        }
                    ],
                }
            ]
        }
    }


async def _evaluate(
    raw: str,
    *,
    identity: str,
    uri: str,
    token: str | None,
    timeout: float,
) -> GuardVerdict:
    return await evaluate_hook_event(
        raw,
        identity=identity,
        uri=uri,
        token=token,
        timeout=timeout,
        state_fetcher=fetch_state_snapshot,
    )


def _cmd_codex_claim_hook(args: argparse.Namespace) -> int:
    if args.print_config:
        if not recipe_inputs_are_safe(args, provider="Codex"):
            return 2
        try:
            config = render_hook_config(
                identity=args.identity,
                uri=args.uri,
                ready_timeout=args.ready_timeout,
                token_file=args.token_file,
                synapse_bin=args.synapse_bin,
            )
        except (OSError, ValueError) as exc:
            print(f"cannot render Codex claim-hook config: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(config, indent=2, ensure_ascii=False))
        return 0
    return run_claim_hook(
        args,
        evaluator=_evaluate,
        failure_reason="Synapse claim verification failed; Codex apply_patch denied.",
    )


def add_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the nested ``adapters codex-claim-hook`` command."""
    parser = subparsers.add_parser(
        "codex-claim-hook",
        help="Guard Codex apply_patch calls with live Synapse file claims.",
    )
    add_claim_hook_arguments(parser)
    parser.set_defaults(func=_cmd_codex_claim_hook)
