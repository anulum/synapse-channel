# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the `synapse doctor` diagnostic CLI command
"""The ``synapse doctor`` subcommand: surface common coordination misconfigs.

It resolves the coordination identity the way the ``syn`` wrappers do, runs the
local identity/send-name/exposure checks, then queries the hub once to report
reachability and whether this identity's ``-rx`` waiter is live (presence is not
a wake). The check logic lives in :mod:`synapse_channel.client.diagnostics`; this
module only gathers the live inputs, renders the report, and sets the exit code.
``resolve_identity`` is imported lazily inside the handler because
:mod:`synapse_channel.ergonomics` imports :mod:`synapse_channel.cli`, which would
otherwise close an import cycle through this module.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from synapse_channel.cli_queries import AgentFactory, _query_hub
from synapse_channel.client.agent import DEFAULT_HUB_URI, SynapseAgent
from synapse_channel.client.diagnostics import (
    Diagnosis,
    check_exposure,
    check_identity,
    check_reachable,
    check_send_identity,
    check_waiter,
    summarise,
)
from synapse_channel.core.protocol import MessageType


async def _fetch_roster(
    *, uri: str, name: str, token: str | None, agent_factory: AgentFactory
) -> list[str] | None:
    """Return the live roster, or ``None`` when the hub is unreachable.

    Reuses the shared connect → request → poll flow; a non-zero return from the
    query means the hub never answered, which the caller reads as unreachable.
    """
    captured: list[list[str]] = []
    code = await _query_hub(
        uri=uri,
        name=name,
        token=token,
        response_type=MessageType.WHO_SNAPSHOT,
        transform=lambda data: [str(agent) for agent in data.get("online_agents", [])],
        request=lambda agent: agent.request_who(),
        render=lambda roster: captured.append(roster),
        agent_factory=agent_factory,
    )
    if code != 0:
        return None
    return captured[-1] if captured else []


async def _diagnose(
    *,
    uri: str,
    project: str | None,
    agent_id: str | None,
    token: str | None,
    send_name: str | None = None,
    agent_factory: AgentFactory = SynapseAgent,
) -> tuple[int, list[str]]:
    """Resolve the identity, run every check, and return ``(exit_code, report_lines)``.

    ``send_name`` checks a specific send identity for project-routable replies
    (the ``<project>-<suffix>`` footgun); it defaults to the resolved identity.
    """
    from synapse_channel.ergonomics import resolve_identity

    identity = resolve_identity(
        project=project,
        agent_id=agent_id,
        cwd_basename=Path.cwd().name,
        home_basename=Path(os.environ.get("HOME", str(Path.home()))).name,
    )
    diagnoses: list[Diagnosis] = [
        check_identity(identity),
        check_send_identity(send_name or identity.identity, project=identity.project),
        check_exposure(uri, token),
    ]
    roster = await _fetch_roster(
        uri=uri,
        name=f"{identity.identity}-doctor",
        token=token,
        agent_factory=agent_factory,
    )
    diagnoses.append(check_reachable(roster is not None, uri))
    diagnoses.append(check_waiter(roster, identity.waiter_name))
    return summarise(diagnoses)


def _cmd_doctor(args: argparse.Namespace) -> int:
    """Dispatch ``doctor``: print the report, exit non-zero when a check fails."""
    code, lines = asyncio.run(
        _diagnose(
            uri=args.uri,
            project=args.project,
            agent_id=args.id,
            token=args.token,
            send_name=args.send_name,
        )
    )
    for line in lines:
        print(line)
    return code


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``doctor`` subparser on the top-level CLI."""
    doctor = subparsers.add_parser(
        "doctor",
        help="Check for common coordination misconfigs (identity, exposure, hub, waiter).",
    )
    doctor.add_argument("--uri", default=DEFAULT_HUB_URI)
    doctor.add_argument(
        "--project", default=None, help="Project identity (over $SYN_PROJECT and the CWD)."
    )
    doctor.add_argument("--id", default=None, help="Short id for a multi-agent identity.")
    doctor.add_argument(
        "--send-name",
        default=None,
        help="A send identity to check for project-routable replies (default: the "
        "resolved identity); flags a <project>-<suffix> name that misses the project inbox.",
    )
    doctor.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    doctor.set_defaults(func=_cmd_doctor)
