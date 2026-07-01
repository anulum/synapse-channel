# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — shared task-plan write CLI commands (task declare/update/progress)
"""The shared task-plan write ``synapse task`` subcommands.

These commands mutate the hub's shared blackboard rather than reading it or
exchanging chat: ``task declare`` posts a task, ``task update`` changes its
status or suggested owner, and ``task progress`` appends a progress note. Each
connects, performs one write, prints the hub's confirmation, and exits. They
reuse :func:`synapse_channel.cli_queries._query_hub` — the same connect → request
→ poll → render flow the read-only queries use — since a write is just a request
whose reply is the hub's confirmation broadcast. They are grouped here, apart
from the read-only queries and the messaging commands, so each module stays one
responsibility; :func:`add_parsers` registers the ``task`` subparser and its
declare/update/progress subcommands on the top-level CLI.

The write helpers take an injectable agent factory so the dispatch and the client
flows are unit-testable without a live hub.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from synapse_channel.cli_queries import _query_hub
from synapse_channel.client.agent import SynapseAgent, default_hub_uri
from synapse_channel.core.protocol import MessageType

AgentFactory = Callable[..., SynapseAgent]


async def _task_action(
    *,
    uri: str,
    name: str,
    token: str | None,
    confirm_type: str,
    send: Callable[[SynapseAgent], Awaitable[None]],
    render: Callable[[dict[str, Any]], str],
    agent_factory: AgentFactory = SynapseAgent,
    attempts: int = 60,
    ready_timeout: float = 5.0,
) -> int:
    """Connect, run one blackboard write, print the hub's confirmation, and exit.

    Parameters
    ----------
    uri, name : str
        Hub URI and the author's display name.
    token : str or None
        Shared-secret token for a secured hub.
    confirm_type : str
        Message type the hub broadcasts to confirm the write.
    send : Callable
        Coroutine that performs the write on the connected agent.
    render : Callable
        Formats the confirmation message into a line for stdout.
    agent_factory : AgentFactory, optional
        Factory for the client agent; injectable for testing.
    attempts : int, optional
        Number of confirmation polling attempts.
    ready_timeout : float, optional
        Seconds to wait for connection readiness.

    Returns
    -------
    int
        ``0`` once the confirmation is printed, ``1`` when the hub was unreachable.
    """
    return await _query_hub(
        uri=uri,
        name=name,
        token=token,
        agent_factory=agent_factory,
        response_type=confirm_type,
        request=send,
        render=lambda data: print(render(data)),
        attempts=attempts,
        ready_timeout=ready_timeout,
    )


def _cmd_task_declare(
    args: argparse.Namespace, *, agent_factory: AgentFactory = SynapseAgent
) -> int:
    """Declare a task on the shared blackboard."""
    deps = tuple(args.depends_on) if args.depends_on else ()

    async def send(agent: SynapseAgent) -> None:
        await agent.post_task(args.task_id, title=args.title, depends_on=deps)

    def render(msg: dict[str, Any]) -> str:
        task = msg.get("task", {})
        deps_txt = ", ".join(task.get("depends_on", [])) or "none"
        return f"declared {task.get('task_id')} — {task.get('title')} (deps: {deps_txt})"

    return asyncio.run(
        _task_action(
            uri=args.uri,
            name=args.name,
            token=args.token,
            confirm_type=MessageType.LEDGER_TASK_POSTED,
            send=send,
            render=render,
            agent_factory=agent_factory,
        )
    )


def _cmd_task_update(
    args: argparse.Namespace, *, agent_factory: AgentFactory = SynapseAgent
) -> int:
    """Update a blackboard task's status or suggested owner."""

    async def send(agent: SynapseAgent) -> None:
        await agent.update_ledger_task(
            args.task_id, status=args.status, suggested_owner=args.suggested_owner
        )

    def render(msg: dict[str, Any]) -> str:
        task = msg.get("task", {})
        return f"updated {task.get('task_id')} -> status={task.get('status')}"

    return asyncio.run(
        _task_action(
            uri=args.uri,
            name=args.name,
            token=args.token,
            confirm_type=MessageType.LEDGER_TASK_UPDATED,
            send=send,
            render=render,
            agent_factory=agent_factory,
        )
    )


def _cmd_task_progress(
    args: argparse.Namespace, *, agent_factory: AgentFactory = SynapseAgent
) -> int:
    """Post a progress note against a task on the blackboard."""

    async def send(agent: SynapseAgent) -> None:
        await agent.post_progress(args.task_id, args.text, kind=args.kind)

    def render(msg: dict[str, Any]) -> str:
        note = msg.get("note", {})
        task_id = note.get("task_id") or args.task_id
        return f"posted {note.get('kind', args.kind)} on {task_id}: {note.get('text', args.text)}"

    return asyncio.run(
        _task_action(
            uri=args.uri,
            name=args.name,
            token=args.token,
            confirm_type=MessageType.LEDGER_PROGRESS_POSTED,
            send=send,
            render=render,
            agent_factory=agent_factory,
        )
    )


def _cmd_task_help(args: argparse.Namespace) -> int:
    """Print usage when ``synapse task`` is run without an action."""
    del args
    print("Usage: synapse task {declare|update|progress} <task_id> ... (see synapse task -h)")
    return 1


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``task`` subparser and its declare/update/progress subcommands."""
    task = subparsers.add_parser("task", help="Declare and update the shared task plan.")
    task.set_defaults(func=_cmd_task_help)
    task_sub = task.add_subparsers(dest="task_command")

    def _add_task_common(parser_: argparse.ArgumentParser) -> None:
        parser_.add_argument("--uri", default=default_hub_uri())
        parser_.add_argument("--name", default="USER")
        parser_.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")

    declare = task_sub.add_parser("declare", help="Declare a task on the blackboard.")
    declare.add_argument("task_id")
    declare.add_argument("--title", default="")
    declare.add_argument(
        "--depends-on",
        action="append",
        default=None,
        help="Task id this one depends on (repeatable).",
    )
    _add_task_common(declare)
    declare.set_defaults(func=_cmd_task_declare)

    update = task_sub.add_parser("update", help="Update a task's status or suggested owner.")
    update.add_argument("task_id")
    update.add_argument("--status", default=None, help="New status, e.g. done.")
    update.add_argument("--suggested-owner", default=None)
    _add_task_common(update)
    update.set_defaults(func=_cmd_task_update)

    progress = task_sub.add_parser("progress", help="Post a progress note on a task.")
    progress.add_argument("task_id")
    progress.add_argument("text")
    progress.add_argument("--kind", default="note")
    _add_task_common(progress)
    progress.set_defaults(func=_cmd_task_progress)
