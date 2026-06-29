# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — unified `synapse` command-line entry point
"""Command-line entry point for the Synapse channel.

The ``synapse`` command exposes these subcommands:

* ``hub`` — run the coordination hub;
* ``demo`` — run a self-contained first-run coordination demo;
* ``quickstart-coding`` — create and run the coding-fleet first-run demo;
* ``new`` — create runnable demo workspaces;
* ``worker`` — run a model worker that answers on the channel;
* ``team`` — launch a hub plus one or two local workers in one shot;
* ``send`` — connect, send one message, optionally wait for replies, and exit;
* ``wait`` — block until a message addressed to you arrives, then exit (a wake trigger);
* ``arm`` — keep a directed waiter armed, re-arming after every wake or reconnect;
* ``listen`` — connect and stream channel messages until interrupted;
* ``relay`` — decode and print a lite relay log a hub mirrored to a file;
* ``ingest`` — stream durable events from a hub event store since a sequence cursor (read-side);
* ``compact`` — bound the durable log: keep latest-N checkpoints per task, age out old findings;
* ``event-query`` — query event-log state at a sequence or timestamp;
* ``postmortem`` — build a replayable task postmortem from the event log;
* ``reliability`` — build evidence-only reliability memory from the event log;
* ``accounting`` — record and report opt-in model cost/token usage from the event log;
* ``approval`` — request, decide, and replay human-in-the-loop approval gates;
* ``ttl-advice`` — build read-only adaptive lease TTL advice from the event log;
* ``dashboard`` — serve a local read-only web dashboard for hub snapshots;
* ``directory`` — print a read-only capability/resource discovery directory;
* ``route-task`` — recommend agents for a board task from local capability signals;
* ``resource-bids`` — rank live resource offers for a board task without reserving them;
* ``memory-recall`` — recall matching durable memory records from a local event store;
* ``board`` — print the hub's shared task/progress blackboard;
* ``supervisor`` — run an LLM-free supervisor that re-offers stalled tasks;
* ``manifest`` — print the capability manifest of advertised agents;
* ``who`` — list the agents currently online, optionally for one project;
* ``state`` — print active claims and their checkpoints (a resume view);
* ``git-claim`` — claim a task scoped to the current git branch (branch resolved client-side);
* ``git-hook`` — install git hooks that auto-release branch-scoped claims on commit/merge;
* ``git-release`` — release branch-scoped claims whose paths were committed/merged (hook-invoked);
* ``conflicts`` — predict merge conflicts between branch-scoped claims on different branches;
* ``health`` — probe the hub and report reachability as the exit code;
* ``verify-release`` — run declared checks and write an observed release receipt;
* ``policy-check`` — evaluate a release receipt against a policy file (advisory);
* ``identity`` — inventory and audit declared agent identities;
* ``acl`` — shadow-mode (non-blocking) ACL evaluation of candidate accesses;
* ``lock`` — hold a lease while running a command, to serialise it across agents;
* ``release`` — manually drop a claim you own (e.g. an ``--auto-release-on manual`` claim);
* ``task`` — declare and update the shared task plan from the command line;
* ``mcp`` — run a Model Context Protocol server over stdio, bridged to the hub;
* ``mcp-tools`` / ``mcp-call`` — list and call allowlisted external MCP tools (outbound);
* ``a2a-card`` — emit an Agent2Agent Agent Card projected from the live manifest;
* ``init`` — print or install local user services for hub, presence, and wake arming;
* ``worker-session`` — launch a provider command with identity env and a wake sidecar.
* ``channel`` — manage private-channel membership (create/join/leave/list);
* ``encrypt-key`` — generate and check at-rest encryption key files;
* ``agent-tmux`` — wake an existing terminal-agent tmux session from Synapse messages;
* ``codex-tmux`` — Codex-named alias of ``agent-tmux``;
* ``shell-hook`` / ``install-shell-hook`` — auto-arm fresh terminals and provider CLIs.

This module is the thin entry point: it builds the top-level parser
(:func:`build_parser`), resolves the shared-secret token, and dispatches
(:func:`main`). Every subcommand group — process (hub/worker/team/supervisor),
messaging (send/wait/arm/listen), read-only query (who/state/board/manifest/health),
service setup / worker-session, task-plan write (task declare/update/progress),
git, locking, mcp, and file/event — lives in its own ``cli_*`` module and
registers its subparsers through
:func:`build_parser`.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from synapse_channel import __version__
from synapse_channel.cli_a2a import add_parsers as add_a2a_parsers
from synapse_channel.cli_accounting import add_parsers as add_accounting_parsers
from synapse_channel.cli_acl_shadow import add_parsers as add_acl_shadow_parsers
from synapse_channel.cli_adapters import add_parsers as add_adapters_parsers
from synapse_channel.cli_adaptive_ttl import add_parsers as add_ttl_advice_parsers
from synapse_channel.cli_agent_tmux import add_parsers as add_agent_tmux_parsers
from synapse_channel.cli_approvals import add_parsers as add_approval_parsers
from synapse_channel.cli_arm import add_parser as add_arm_parser
from synapse_channel.cli_channels import add_parsers as add_channel_parsers
from synapse_channel.cli_codex_tmux import add_parsers as add_codex_tmux_parsers
from synapse_channel.cli_dashboard import add_parsers as add_dashboard_parsers
from synapse_channel.cli_demo import add_parsers as add_demo_parsers
from synapse_channel.cli_directory import add_parsers as add_directory_parsers
from synapse_channel.cli_doctor import add_parsers as add_doctor_parsers
from synapse_channel.cli_encrypt_key import add_parsers as add_encrypt_key_parsers
from synapse_channel.cli_event_query import add_parsers as add_event_query_parsers
from synapse_channel.cli_federation import add_parsers as add_federation_parsers
from synapse_channel.cli_git import add_parsers as add_git_parsers
from synapse_channel.cli_identity import add_parsers as add_identity_parsers
from synapse_channel.cli_locking import add_parsers as add_locking_parsers
from synapse_channel.cli_mcp import add_parsers as add_mcp_parsers
from synapse_channel.cli_mcp_call import add_parsers as add_mcp_call_parsers
from synapse_channel.cli_memory_projection import add_parsers as add_memory_projection_parsers
from synapse_channel.cli_messaging import add_parsers as add_messaging_parsers
from synapse_channel.cli_multihub import add_parsers as add_multihub_parsers
from synapse_channel.cli_new import add_parsers as add_new_parsers
from synapse_channel.cli_policy_check import add_parsers as add_policy_check_parsers
from synapse_channel.cli_postmortem import add_parsers as add_postmortem_parsers
from synapse_channel.cli_processes import add_parsers as add_process_parsers
from synapse_channel.cli_queries import add_parsers as add_query_parsers
from synapse_channel.cli_quickstart_coding import add_parsers as add_quickstart_coding_parsers
from synapse_channel.cli_reliability import add_parsers as add_reliability_parsers
from synapse_channel.cli_resource_bidding import add_parsers as add_resource_bidding_parsers
from synapse_channel.cli_sandbox import add_parsers as add_sandbox_parsers
from synapse_channel.cli_semantic_routing import add_parsers as add_semantic_routing_parsers
from synapse_channel.cli_services import add_parsers as add_service_parsers
from synapse_channel.cli_shell import add_parsers as add_shell_parsers
from synapse_channel.cli_streams import add_parsers as add_stream_parsers
from synapse_channel.cli_tasks import add_parsers as add_task_parsers
from synapse_channel.cli_verify_release import add_parsers as add_verify_release_parsers
from synapse_channel.cli_workflow import add_parsers as add_workflow_parsers
from synapse_channel.update_check import update_notice


class _VersionAction(argparse.Action):
    """Print the version and an opt-in upgrade notice, then exit.

    Behaves like argparse's built-in ``version`` action (prints and raises
    ``SystemExit``) but appends a one-line PyPI upgrade notice on stderr when
    ``SYNAPSE_UPDATE_CHECK=1`` is set and a newer release exists. The notice is
    best-effort and silenced by ``SYNAPSE_NO_UPDATE_CHECK``.
    """

    def __init__(self, option_strings: list[str], dest: str, **kwargs: Any) -> None:
        kwargs.setdefault("nargs", 0)
        kwargs.setdefault("help", "show the version and exit; optional update check is opt-in")
        super().__init__(option_strings, dest, **kwargs)

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        print(f"synapse-channel {__version__}")
        notice = update_notice()
        if notice:
            print(notice, file=sys.stderr)
        parser.exit()


# -- parser -------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with all subcommands."""
    parser = argparse.ArgumentParser(prog="synapse", description="Synapse multi-agent channel.")
    parser.add_argument("--version", action=_VersionAction)
    sub = parser.add_subparsers(dest="command")

    add_process_parsers(sub)

    add_demo_parsers(sub)

    add_quickstart_coding_parsers(sub)

    add_new_parsers(sub)

    add_messaging_parsers(sub)
    add_multihub_parsers(sub)

    add_arm_parser(sub)

    add_query_parsers(sub)

    add_dashboard_parsers(sub)

    add_directory_parsers(sub)

    add_semantic_routing_parsers(sub)

    add_resource_bidding_parsers(sub)
    add_sandbox_parsers(sub)

    add_memory_projection_parsers(sub)

    add_service_parsers(sub)

    add_agent_tmux_parsers(sub)
    add_channel_parsers(sub)
    add_encrypt_key_parsers(sub)
    add_codex_tmux_parsers(sub)

    add_shell_parsers(sub)

    add_mcp_parsers(sub)

    add_mcp_call_parsers(sub)

    add_a2a_parsers(sub)
    add_adapters_parsers(sub)

    add_git_parsers(sub)

    add_verify_release_parsers(sub)

    add_policy_check_parsers(sub)

    add_identity_parsers(sub)

    add_acl_shadow_parsers(sub)

    add_locking_parsers(sub)

    add_stream_parsers(sub)

    add_event_query_parsers(sub)
    add_federation_parsers(sub)

    add_postmortem_parsers(sub)

    add_reliability_parsers(sub)

    add_accounting_parsers(sub)

    add_approval_parsers(sub)

    add_ttl_advice_parsers(sub)

    add_task_parsers(sub)

    add_workflow_parsers(sub)

    add_doctor_parsers(sub)

    # Give every command that takes --token a --token-file companion, so the secret
    # can come from a file instead of argv (which is visible to anyone running `ps`).
    for subparser in sub.choices.values():
        if any("--token" in action.option_strings for action in subparser._actions):
            subparser.add_argument(
                "--token-file",
                default=None,
                help="Read the shared-secret token from this file instead of --token.",
            )

    return parser


#: Environment variable name read as a fallback source for the hub shared-secret token.
TOKEN_ENV = "SYNAPSE_TOKEN"  # nosec B105


def _resolve_token(args: argparse.Namespace) -> str | None:
    """Resolve the hub token from ``--token``, then ``--token-file``, then the env var.

    Precedence is ``--token`` (an explicit override) → ``--token-file`` → the
    ``SYNAPSE_TOKEN`` environment variable. Prefer ``--token-file`` or the
    environment variable for a real secret: a ``--token`` value is visible in the
    process list. (This describes which source is *used*, not which is more secure
    — a value passed as ``--token`` is exposed regardless of what wins.)

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments; uses ``token`` and the optional ``token_file``.

    Returns
    -------
    str or None
        The resolved token, or ``None`` when no source supplies one.
    """
    if args.token:
        return str(args.token)
    token_file = getattr(args, "token_file", None)
    if token_file:
        return Path(token_file).read_text(encoding="utf-8").strip()
    return os.environ.get(TOKEN_ENV) or None


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch to the selected subcommand.

    Parameters
    ----------
    argv : list[str] or None, optional
        Argument vector; defaults to ``sys.argv[1:]`` when ``None``.

    Returns
    -------
    int
        The selected command's exit code, or ``1`` when no command was given.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 1
    if hasattr(args, "token"):
        args.token = _resolve_token(args)
    handler: Callable[[argparse.Namespace], int] = args.func
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
