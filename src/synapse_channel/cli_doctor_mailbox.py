# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — mailbox-pending doctor projection
"""Focused WHO parsing and diagnosis for mailbox pending counts."""

from __future__ import annotations

from dataclasses import dataclass

from synapse_channel.cli_query_transport import AgentFactory, _query_hub
from synapse_channel.client.diagnostics import Diagnosis
from synapse_channel.core.mailbox_pending import format_pending_line, parse_pending_counts
from synapse_channel.core.protocol import MessageType
from synapse_channel.terminal_text import shell_long_option, terminal_text


@dataclass(frozen=True)
class DoctorRoster:
    """Roster and additive mailbox projection returned by one WHO snapshot."""

    agents: tuple[str, ...]
    mailbox_pending: dict[str, int] | None


def doctor_roster_from_snapshot(data: dict[str, object]) -> DoctorRoster:
    """Parse the WHO fields doctor needs without treating malformed data as zero."""
    raw_agents = data.get("online_agents")
    agents = tuple(str(agent) for agent in raw_agents) if isinstance(raw_agents, list) else ()
    return DoctorRoster(
        agents=agents,
        mailbox_pending=parse_pending_counts(data.get("mailbox_pending")),
    )


async def fetch_doctor_roster(
    *,
    uri: str,
    name: str,
    token: str | None,
    agent_factory: AgentFactory,
    ready_timeout: float = 5.0,
) -> DoctorRoster | None:
    """Return the live WHO projection, or ``None`` when the hub is unreachable."""
    captured: list[DoctorRoster] = []
    code = await _query_hub(
        uri=uri,
        name=name,
        token=token,
        response_type=MessageType.WHO_SNAPSHOT,
        transform=doctor_roster_from_snapshot,
        request=lambda agent: agent.request_who(),
        render=captured.append,
        agent_factory=agent_factory,
        ready_timeout=ready_timeout,
    )
    if code != 0:
        return None
    return captured[-1] if captured else DoctorRoster((), None)


def diagnose_mailbox_pending(
    counts: dict[str, int] | None,
    *,
    identity: str,
) -> Diagnosis:
    """Report one identity's hub-authoritative receiver backlog."""
    if counts is None:
        return Diagnosis(
            check="mailbox-pending",
            status="warn",
            detail=f"mailbox pending count unavailable for {terminal_text(identity)}",
            remedy=(
                "use a hub with a durable --db on the current Synapse version, then re-run doctor"
            ),
        )
    count = counts.get(identity, 0)
    detail = format_pending_line(terminal_text(identity), count)
    if count == 0:
        return Diagnosis(check="mailbox-pending", status="pass", detail=detail)
    return Diagnosis(
        check="mailbox-pending",
        status="warn",
        detail=detail,
        remedy=(
            f"replay with synapse arm {shell_long_option('--name', f'{identity}-rx')} "
            f"{shell_long_option('--for', identity)} "
            "--directed-only --mailbox --max-wakes 1; inspect bodies with "
            f"syn inbox {shell_long_option('--as', identity)}, then keep the permanent "
            "waiter armed"
        ),
    )
