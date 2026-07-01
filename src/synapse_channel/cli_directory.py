# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — read-only capability directory CLI
"""CLI command for the manifest-backed capability directory."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
from collections.abc import Mapping
from typing import Any

from synapse_channel.cli_query_transport import AgentFactory
from synapse_channel.client.agent import SynapseAgent, default_hub_uri
from synapse_channel.connect_failures import describe_connect_failure
from synapse_channel.core.capability_directory import (
    CapabilityDirectory,
    build_capability_directory,
    directory_to_json,
    filter_capability_directory,
)
from synapse_channel.core.protocol import MessageType


def _render_directory(directory: CapabilityDirectory) -> None:
    """Print a compact text representation of ``directory``."""
    print(f"Directory ({len(directory.entries)} entries):")
    for entry in directory.entries:
        if entry.entry_type == "agent":
            classes = ", ".join(entry.task_classes) or "none"
            skills = ", ".join(entry.skills) or "none"
            model = entry.model or "-"
            print(
                f"  agent {entry.agent} [{classes}] skills={skills} "
                f"model={model} contracts={entry.contracts} "
                f"trust={entry.trust}: {entry.description}"
            )
            continue
        print(
            f"  resource {entry.agent} {entry.resource_kind}/{entry.resource_name} "
            f"capacity={entry.capacity} trust={entry.trust}"
        )
    print(f"Trust boundary: {directory.trust_boundary}")


def _cards(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return manifest cards from a hub reply, ignoring malformed entries."""
    manifest = data.get("manifest", [])
    if not isinstance(manifest, list):
        return []
    return [dict(card) for card in manifest if isinstance(card, Mapping)]


def _resources(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return resource offers from a state snapshot, ignoring malformed entries."""
    snapshot = data.get("snapshot", {})
    if not isinstance(snapshot, Mapping):
        return []
    resources = snapshot.get("resources", [])
    if not isinstance(resources, list):
        return []
    return [dict(resource) for resource in resources if isinstance(resource, Mapping)]


async def _fetch_directory(
    *,
    uri: str,
    name: str,
    token: str | None,
    agent_factory: AgentFactory,
    ready_timeout: float,
    response_timeout: float,
) -> CapabilityDirectory | None:
    """Fetch live manifest and state snapshots and merge them into a directory."""
    replies: dict[str, dict[str, Any]] = {}
    expected = {MessageType.MANIFEST_SNAPSHOT, MessageType.STATE_SNAPSHOT}

    async def collect(data: dict[str, Any]) -> None:
        message_type = str(data.get("type", ""))
        if message_type in expected:
            replies[message_type] = data

    agent = agent_factory(name, collect, uri=uri, verbose=False, token=token)
    conn_task = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=ready_timeout):
            print(
                describe_connect_failure(
                    name,
                    uri,
                    close_code=agent.last_close_code,
                    close_reason=agent.last_close_reason,
                )
            )
            return None
        await agent.request_manifest()
        await agent.request_state()
        deadline = asyncio.get_running_loop().time() + max(0.0, response_timeout)
        while set(replies) != expected and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.025)
        if set(replies) != expected:
            missing = ", ".join(sorted(expected.difference(replies)))
            print(f"[{name}] Hub did not return capability directory snapshots: {missing}.")
            return None
        return build_capability_directory(
            manifest=_cards(replies[MessageType.MANIFEST_SNAPSHOT]),
            resources=_resources(replies[MessageType.STATE_SNAPSHOT]),
        )
    finally:
        agent.running = False
        conn_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await conn_task


async def _directory(
    *,
    uri: str,
    name: str,
    token: str | None = None,
    agent_factory: AgentFactory = SynapseAgent,
    ready_timeout: float = 5.0,
    response_timeout: float = 2.0,
    filter_agent: str | None = None,
    task_class: str | None = None,
    skill: str | None = None,
    resource_kind: str | None = None,
    as_json: bool = False,
) -> int:
    """Fetch, filter, and render the live capability directory.

    Parameters
    ----------
    uri, name : str
        Hub URI and query identity.
    token : str or None, optional
        Shared-secret token for a secured hub.
    agent_factory : AgentFactory, optional
        Client factory, injectable for tests.
    ready_timeout : float, optional
        Seconds to wait for the hub welcome handshake.
    response_timeout : float, optional
        Seconds to wait for both manifest and state snapshots.
    filter_agent, task_class, skill, resource_kind : str or None, optional
        Exact-match filters.
    as_json : bool, optional
        Print stable JSON instead of compact text.

    Returns
    -------
    int
        ``0`` on a rendered directory, ``1`` when the hub is unreachable or
        does not answer both required snapshots.
    """
    directory = await _fetch_directory(
        uri=uri,
        name=name,
        token=token,
        agent_factory=agent_factory,
        ready_timeout=ready_timeout,
        response_timeout=response_timeout,
    )
    if directory is None:
        return 1
    filtered = filter_capability_directory(
        directory,
        agent=filter_agent,
        task_class=task_class,
        skill=skill,
        resource_kind=resource_kind,
    )
    if as_json:
        print(directory_to_json(filtered))
    else:
        _render_directory(filtered)
    return 0


def _cmd_directory(args: argparse.Namespace) -> int:
    """Dispatch the ``directory`` subcommand."""
    return asyncio.run(
        _directory(
            uri=args.uri,
            name=args.name,
            token=args.token,
            ready_timeout=args.ready_timeout,
            response_timeout=args.response_timeout,
            filter_agent=args.agent,
            task_class=args.task_class,
            skill=args.skill,
            resource_kind=args.resource_kind,
            as_json=args.json,
        )
    )


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``directory`` subcommand."""
    directory = subparsers.add_parser(
        "directory",
        help="Print a read-only capability directory from live cards and resources.",
    )
    directory.add_argument("--uri", default=default_hub_uri())
    directory.add_argument("--name", default="DIRECTORY")
    directory.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    directory.add_argument("--agent", default=None, help="Show entries advertised by this agent.")
    directory.add_argument("--task-class", default=None, help="Show agent entries for this task.")
    directory.add_argument("--skill", default=None, help="Show agent entries with this skill tag.")
    directory.add_argument(
        "--resource-kind", default=None, help="Show resource entries of this kind."
    )
    directory.add_argument("--json", action="store_true", help="Print JSON instead of text.")
    directory.add_argument(
        "--response-timeout",
        type=float,
        default=2.0,
        help="Seconds to wait for manifest and state snapshots.",
    )
    directory.add_argument(
        "--ready-timeout", type=float, default=5.0, help="Seconds to await hub readiness."
    )
    directory.set_defaults(func=_cmd_directory)
