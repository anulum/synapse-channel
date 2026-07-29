# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Grok claim-guard CLI and hooks JSON recipe
"""Run the Grok claim guard or print a mergeable global hooks fragment."""

from __future__ import annotations

import argparse
from typing import Any

from synapse_channel.claim_state import fetch_state_snapshot
from synapse_channel.cli_claim_hook_common import (
    add_claim_hook_arguments,
    render_json_hook_config,
    run_json_claim_hook_command,
)
from synapse_channel.file_claim_guard import GuardVerdict
from synapse_channel.grok_claim_guard import denial_payload, evaluate_hook_event

GROK_TOOL_MATCHER = "^(search_replace|write|Edit|Write|MultiEdit|run_terminal_command)$"
"""Matcher covering Grok's file editors and native terminal command tool."""


def render_hook_config(
    *,
    identity: str,
    uri: str,
    ready_timeout: float,
    token_file: str | None,
    synapse_bin: str | None,
) -> dict[str, Any]:
    """Return a token-safe Grok global PreToolUse hooks fragment."""
    return render_json_hook_config(
        command="grok-claim-hook",
        event="PreToolUse",
        matcher=GROK_TOOL_MATCHER,
        identity=identity,
        uri=uri,
        ready_timeout=ready_timeout,
        token_file=token_file,
        synapse_bin=synapse_bin,
    )


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


def _cmd_grok_claim_hook(args: argparse.Namespace) -> int:
    return run_json_claim_hook_command(
        args,
        provider="Grok",
        config_renderer=render_hook_config,
        evaluator=_evaluate,
        failure_reason="Synapse claim verification failed; Grok mutation denied.",
        payload_renderer=denial_payload,
    )


def add_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the nested adapters grok-claim-hook command."""
    parser = subparsers.add_parser(
        "grok-claim-hook",
        help="Guard Grok file edits and terminal commands with live Synapse claims.",
    )
    add_claim_hook_arguments(parser)
    parser.set_defaults(func=_cmd_grok_claim_hook)
