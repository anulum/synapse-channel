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
* ``debug`` — fork a task's reconstructed state at a sequence point (read-only what-if);
* ``reproduce`` — fingerprint a task's authoritative history into a deterministic digest;
* ``causality`` — trace coordination causes, effects, or counterfactuals over the event log;
* ``merkle`` — commit the event log to a Merkle root and prove event inclusion;
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
* ``shell-hook`` / ``install-shell-hook`` — auto-arm fresh terminals and provider CLIs;
* ``completions`` — print a static tab-completion script for bash, zsh, or fish.

This module is the thin entry point: it builds the top-level parser
(:func:`build_parser`), resolves the shared-secret token, and dispatches
(:func:`main`). Every subcommand group — process (hub/worker/team/supervisor),
messaging (send/wait/arm/listen), read-only query (who/state/board/manifest/health),
service setup / worker-session, task-plan write (task declare/update/progress),
git, locking, mcp, and file/event — lives in its own ``cli_*`` module and
registers its subparsers through
:func:`build_parser`.

Registration is lazy: the ``cli_*`` modules are imported only when their
commands are needed. :func:`main` reads the requested command off ``argv``
first and registers just the unit that owns it, so ``synapse who`` does not
pay the import cost of the whole surface; ``--help``, ``--version``, and
unknown commands fall back to registering everything.
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from synapse_channel import __version__


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
        from synapse_channel.update_check import update_notice

        notice = update_notice()
        if notice:
            print(notice, file=sys.stderr)
        parser.exit()


# -- parser -------------------------------------------------------------------

_Registrar = Callable[["argparse._SubParsersAction[argparse.ArgumentParser]"], object]

#: Registration units in ``--help`` display order. Each unit pairs the
#: ``"module:function"`` registrar of one command family with the exact
#: top-level commands it adds. The pairing, coverage, and per-unit parser
#: equivalence are all pinned by contract tests, so a unit that drifts from
#: its declaration cannot ship.
_REGISTRATION_UNITS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("synapse_channel.cli_processes:add_parsers", ("hub", "worker", "team", "supervisor")),
    ("synapse_channel.cli_demo:add_parsers", ("demo",)),
    ("synapse_channel.cli_commands_overview:add_parsers", ("commands",)),
    ("synapse_channel.cli_completions:add_parsers", ("completions",)),
    ("synapse_channel.cli_quickstart_coding:add_parsers", ("quickstart-coding",)),
    ("synapse_channel.cli_new:add_parsers", ("new",)),
    ("synapse_channel.cli_messaging:add_parsers", ("send", "wait", "listen")),
    ("synapse_channel.cli_multihub:add_parsers", ("multihub",)),
    ("synapse_channel.cli:_register_participant_group", ("participant",)),
    ("synapse_channel.cli_arm:add_parser", ("arm",)),
    ("synapse_channel.cli_queries:add_parsers", ("who", "state", "board", "manifest", "health")),
    ("synapse_channel.cli_status:add_parsers", ("status",)),
    ("synapse_channel.cli_dashboard:add_parsers", ("dashboard",)),
    ("synapse_channel.cli_directory:add_parsers", ("directory",)),
    ("synapse_channel.cli_semantic_routing:add_parsers", ("route-task",)),
    ("synapse_channel.cli_resource_bidding:add_parsers", ("resource-bids",)),
    ("synapse_channel.cli_sandbox:add_parsers", ("sandbox",)),
    ("synapse_channel.cli_memory_projection:add_parsers", ("memory-recall",)),
    ("synapse_channel.cli_services:add_parsers", ("init", "worker-session")),
    ("synapse_channel.cli_agent_tmux:add_parsers", ("agent-tmux",)),
    ("synapse_channel.cli_channels:add_parsers", ("channel",)),
    ("synapse_channel.cli_encrypt_key:add_parsers", ("encrypt-key",)),
    ("synapse_channel.cli_codex_tmux:add_parsers", ("codex-tmux",)),
    ("synapse_channel.cli_shell:add_parsers", ("shell-hook", "install-shell-hook")),
    ("synapse_channel.cli_mcp:add_parsers", ("mcp",)),
    ("synapse_channel.cli_mcp_call:add_parsers", ("mcp-tools", "mcp-call")),
    ("synapse_channel.cli_a2a:add_parsers", ("a2a-card", "a2a-serve")),
    ("synapse_channel.cli_adapters:add_parsers", ("adapters",)),
    (
        "synapse_channel.cli_git:add_parsers",
        ("git-claim", "git-hook", "git-init", "git-release", "conflicts"),
    ),
    ("synapse_channel.cli_verify_release:add_parsers", ("verify-release",)),
    ("synapse_channel.cli_policy_check:add_parsers", ("policy-check",)),
    ("synapse_channel.cli_identity:add_parsers", ("identity",)),
    ("synapse_channel.cli_acl_shadow:add_parsers", ("acl",)),
    ("synapse_channel.cli_locking:add_parsers", ("lock", "release")),
    ("synapse_channel.cli_streams:add_parsers", ("relay", "ingest", "compact")),
    ("synapse_channel.cli_event_query:add_parsers", ("event-query",)),
    ("synapse_channel.cli_federation:add_parsers", ("federation",)),
    ("synapse_channel.cli_postmortem:add_parsers", ("postmortem",)),
    ("synapse_channel.cli_replay:add_parsers", ("debug", "reproduce")),
    ("synapse_channel.cli_causality:add_parsers", ("causality",)),
    ("synapse_channel.cli_merkle:add_parsers", ("merkle",)),
    ("synapse_channel.cli_reliability:add_parsers", ("reliability",)),
    ("synapse_channel.cli_trust_graph:add_parsers", ("trust-graph",)),
    ("synapse_channel.cli_cross_repo:add_parsers", ("cross-repo",)),
    ("synapse_channel.cli_benchmark:add_parsers", ("benchmark",)),
    ("synapse_channel.cli_accounting:add_parsers", ("accounting",)),
    ("synapse_channel.cli_approvals:add_parsers", ("approval",)),
    ("synapse_channel.cli_adaptive_ttl:add_parsers", ("ttl-advice",)),
    ("synapse_channel.cli_tasks:add_parsers", ("task",)),
    ("synapse_channel.cli_workflow:add_parsers", ("workflow",)),
    ("synapse_channel.cli_doctor:add_parsers", ("doctor",)),
)


def _register_participant_group(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register ``participant`` with its deliberation and costs extensions.

    The two extension modules attach subcommands to the group returned by the
    base registrar, so the three registrations must run together and in order.
    """
    from synapse_channel.cli_participants import add_parsers as add_participant_parsers
    from synapse_channel.cli_participants_costs import (
        add_parsers as add_participant_costs_parsers,
    )
    from synapse_channel.cli_participants_deliberate import (
        add_parsers as add_participant_deliberation_parsers,
    )

    participant_group = add_participant_parsers(subparsers)
    add_participant_deliberation_parsers(participant_group)
    add_participant_costs_parsers(participant_group)


def _registrar(spec: str) -> _Registrar:
    """Resolve a ``"module:function"`` registration spec, importing on demand."""
    module_name, _, function_name = spec.partition(":")
    registrar: _Registrar = getattr(importlib.import_module(module_name), function_name)
    return registrar


def _unit_owning(command: str) -> str | None:
    """Return the registrar spec whose unit provides ``command``, if any."""
    for spec, commands in _REGISTRATION_UNITS:
        if command in commands:
            return spec
    return None


def _requested_command(argv: list[str]) -> str | None:
    """Return the first positional token of ``argv`` — the subcommand candidate.

    The top-level parser takes only zero-argument options (``--help``,
    ``--version``), so the first token that is neither an option nor the
    ``--`` separator names the command. A token this misjudges simply falls
    back to the full parser, which owns the error message.
    """
    for token in argv:
        if token != "--" and not token.startswith("-"):
            return token
    return None


def build_parser(*, command: str | None = None) -> argparse.ArgumentParser:
    """Build the top-level argument parser.

    Parameters
    ----------
    command : str or None, optional
        When given and recognised, register only the unit that owns this
        command, so start-up imports stay proportional to what will actually
        run. ``None`` or an unrecognised name registers every unit — the full
        parser then renders ``--help`` and the canonical unknown-command error.
    """
    parser = argparse.ArgumentParser(prog="synapse", description="Synapse multi-agent channel.")
    parser.add_argument("--version", action=_VersionAction)
    sub = parser.add_subparsers(dest="command")

    owner = _unit_owning(command) if command is not None else None
    if owner is not None:
        _registrar(owner)(sub)
    else:
        for spec, _commands in _REGISTRATION_UNITS:
            _registrar(spec)(sub)

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
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser(command=_requested_command(arguments))
    args = parser.parse_args(arguments)
    if not getattr(args, "command", None):
        parser.print_help()
        return 1
    if hasattr(args, "token"):
        args.token = _resolve_token(args)
    handler: Callable[[argparse.Namespace], int] = args.func
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
