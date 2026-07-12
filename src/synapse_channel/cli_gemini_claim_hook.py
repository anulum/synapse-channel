# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Gemini CLI BeforeTool claim-hook CLI and recipe
"""Run the Gemini claim guard or print a mergeable ``settings.json`` hooks fragment.

The recipe targets the hook store read on 2026-07-12 from the installed ``gemini``
0.47.0 bundle source: hooks live under the ``"hooks"`` key of ``.gemini/settings.json``
(workspace or user scope), keyed by Gemini's native event names. ``BeforeTool``
matchers are regular expressions tested against the tool name, hook ``timeout`` is in
**milliseconds** (default 60 000), and project-scope hooks are refused by Gemini in
untrusted folders.
"""

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
from synapse_channel.file_claim_guard import GuardVerdict
from synapse_channel.gemini_claim_guard import evaluate_hook_event, gemini_denial_payload

GEMINI_TOOL_MATCHER = "^(replace|write_file)$"
"""Anchored matcher for Gemini's two file-mutation tools (regex per the hook planner)."""


def render_hook_config(
    *,
    identity: str,
    uri: str,
    ready_timeout: float,
    token_file: str | None,
    synapse_bin: str | None,
) -> dict[str, Any]:
    """Return a token-safe ``settings.json`` ``hooks`` fragment for ``BeforeTool``."""
    command = render_hook_command(
        command="gemini-claim-hook",
        identity=identity,
        uri=uri,
        ready_timeout=ready_timeout,
        token_file=token_file,
        synapse_bin=synapse_bin,
    )
    return {
        "hooks": {
            "BeforeTool": [
                {
                    "matcher": GEMINI_TOOL_MATCHER,
                    "hooks": [
                        {
                            "type": "command",
                            "command": command,
                            "timeout": hook_timeout(ready_timeout) * 1000,
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


def _cmd_gemini_claim_hook(args: argparse.Namespace) -> int:
    if args.print_config:
        if not recipe_inputs_are_safe(args, provider="Gemini"):
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
            print(f"cannot render Gemini claim-hook config: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(config, indent=2, ensure_ascii=False))
        return 0
    return run_claim_hook(
        args,
        evaluator=_evaluate,
        failure_reason="Synapse claim verification failed; Gemini replace/write_file denied.",
        payload_renderer=gemini_denial_payload,
    )


def add_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the nested ``adapters gemini-claim-hook`` command."""
    parser = subparsers.add_parser(
        "gemini-claim-hook",
        help="Guard Gemini CLI replace/write_file calls with live Synapse file claims.",
    )
    add_claim_hook_arguments(parser)
    parser.set_defaults(func=_cmd_gemini_claim_hook)
