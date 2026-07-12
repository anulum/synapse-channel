# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Kimi Code Edit/Write claim-hook CLI and recipe
"""Run the Kimi claim guard or safely manage its marked ``config.toml`` block."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping
from pathlib import Path

from synapse_channel.claim_state import fetch_state_snapshot
from synapse_channel.cli_claim_hook_common import (
    add_claim_hook_arguments,
    recipe_inputs_are_safe,
    run_claim_hook,
)
from synapse_channel.file_claim_guard import GuardVerdict
from synapse_channel.kimi_claim_guard import evaluate_hook_event
from synapse_channel.kimi_hook_config_file import (
    KimiHookConfigFileError,
    install_hook_config,
    resolve_kimi_config_path,
    uninstall_hook_config,
)
from synapse_channel.kimi_hook_installer import (
    KimiHookInstallerError,
    render_hook_config,
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


def _resolve_kimi_config(path: str | None, *, environ: Mapping[str, str] | None = None) -> Path:
    """Return an absolute Kimi config path, respecting ``KIMI_CODE_HOME``."""
    return resolve_kimi_config_path(path, environ=environ)


def _install_config(args: argparse.Namespace) -> int:
    if not recipe_inputs_are_safe(args, provider="Kimi"):
        return 2
    try:
        config_path = _resolve_kimi_config(args.kimi_config)
        result = install_hook_config(
            config_path,
            identity=args.identity,
            uri=args.uri,
            ready_timeout=args.ready_timeout,
            token_file=args.token_file,
            synapse_bin=args.synapse_bin,
        )
    except (KimiHookConfigFileError, KimiHookInstallerError, OSError, ValueError) as exc:
        print(f"cannot install Kimi claim hook: {exc}", file=sys.stderr)
        return 2
    if result.outcome == "unchanged":
        print(f"Synapse Kimi hook already installed in {result.path}")
    else:
        print(f"{result.outcome} Synapse Kimi hook in {result.path}")
    return 0


def _uninstall_config(args: argparse.Namespace) -> int:
    try:
        config_path = _resolve_kimi_config(args.kimi_config)
        result = uninstall_hook_config(config_path)
    except (KimiHookConfigFileError, KimiHookInstallerError, OSError, ValueError) as exc:
        print(f"cannot uninstall Kimi claim hook: {exc}", file=sys.stderr)
        return 2
    if result.outcome == "not-installed":
        print(f"Synapse Kimi hook not installed in {result.path}")
    elif result.outcome == "removed-file":
        print(f"removed empty Kimi config {result.path}")
    else:
        print(f"removed Synapse Kimi hook from {result.path}")
    return 0


def _cmd_kimi_claim_hook(args: argparse.Namespace) -> int:
    if args.print_config and (args.install_config or args.uninstall_config):
        print(
            "choose exactly one of --print-config, --install-config, or --uninstall-config",
            file=sys.stderr,
        )
        return 2
    if args.uninstall_config:
        return _uninstall_config(args)
    if not args.identity:
        print("--identity is required unless --uninstall-config is used", file=sys.stderr)
        return 2
    if args.print_config:
        if not recipe_inputs_are_safe(args, provider="Kimi"):
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
            print(f"cannot render Kimi claim-hook config: {exc}", file=sys.stderr)
            return 2
        print(config, end="")
        return 0
    if args.install_config:
        return _install_config(args)

    return run_claim_hook(
        args,
        evaluator=_evaluate,
        failure_reason="Synapse claim verification failed; Kimi Edit/Write denied.",
    )


def add_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the nested ``adapters kimi-claim-hook`` command."""
    parser = subparsers.add_parser(
        "kimi-claim-hook",
        help="Guard Kimi Code Edit/Write calls with live Synapse file claims.",
    )
    add_claim_hook_arguments(parser, identity_required=False)
    config_actions = parser.add_mutually_exclusive_group()
    config_actions.add_argument(
        "--install-config",
        action="store_true",
        help=(
            "Install the Synapse PreToolUse hook block into "
            "$KIMI_CODE_HOME/config.toml (default: ~/.kimi-code/config.toml) "
            "instead of reading stdin."
        ),
    )
    config_actions.add_argument(
        "--uninstall-config",
        action="store_true",
        help="Remove the Synapse hook block from KIMI's config.toml.",
    )
    parser.add_argument(
        "--kimi-config",
        default=None,
        help="Override $KIMI_CODE_HOME/config.toml (default: ~/.kimi-code/config.toml).",
    )
    parser.set_defaults(func=_cmd_kimi_claim_hook)
