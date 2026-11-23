# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — hub limits and name-takeover damping configuration records
"""Ceilings and name-takeover damping configuration records for SynapseHub."""

from __future__ import annotations

from dataclasses import dataclass

from synapse_channel.core.dead_letter_escalation import DEFAULT_DEAD_LETTER_ESCALATION_THRESHOLD
from synapse_channel.core.hub_defaults import (
    DEFAULT_COMPACT_HINT_THRESHOLD,
    DEFAULT_MAX_CLIENTS,
    DEFAULT_MAX_CONNECTIONS_PER_HOST,
    DEFAULT_MAX_FINDINGS_PER_AGENT,
    DEFAULT_MAX_HISTORY,
    DEFAULT_MAX_MSG_BYTES,
    DEFAULT_TAKEOVER_COOLDOWN,
    DEFAULT_TAKEOVER_OSCILLATION_THRESHOLD,
    DEFAULT_TAKEOVER_OSCILLATION_WINDOW,
    DEFAULT_TAKEOVER_QUARANTINE,
)
from synapse_channel.core.ledger import (
    DEFAULT_MAX_PROGRESS,
    DEFAULT_MAX_PROGRESS_PER_AUTHOR,
    DEFAULT_MAX_PROGRESS_PER_TASK,
)
from synapse_channel.core.name_ownership import DEFAULT_LEASE_OFFLINE_TTL
from synapse_channel.core.scoping import MAX_DECLARED_PATHS
from synapse_channel.core.state import MAX_CLAIMS_PER_AGENT, MAX_OFFERS_PER_AGENT

__all__ = ["HubLimits", "TakeoverDamping"]


@dataclass(frozen=True, kw_only=True)
class HubLimits:
    """Every ceiling the hub enforces: retention, quotas, and transport bounds.

    Field names retain the original ``SynapseHub`` keywords. See the hub's
    parameter documentation for the meaning and failure mode of each bound.
    """

    max_history: int = DEFAULT_MAX_HISTORY
    max_msg_bytes: int = DEFAULT_MAX_MSG_BYTES
    max_clients: int = DEFAULT_MAX_CLIENTS
    max_unauth_clients: int | None = None
    max_connections_per_host: int | None = DEFAULT_MAX_CONNECTIONS_PER_HOST
    max_progress: int = DEFAULT_MAX_PROGRESS
    max_progress_per_author: int = DEFAULT_MAX_PROGRESS_PER_AUTHOR
    max_progress_per_task: int = DEFAULT_MAX_PROGRESS_PER_TASK
    board_task_cap: int | None = None
    max_findings_per_agent: int = DEFAULT_MAX_FINDINGS_PER_AGENT
    max_claims_per_agent: int = MAX_CLAIMS_PER_AGENT
    max_offers_per_agent: int = MAX_OFFERS_PER_AGENT
    max_paths_per_claim: int = MAX_DECLARED_PATHS
    compact_hint_threshold: int = DEFAULT_COMPACT_HINT_THRESHOLD
    dead_letter_escalation_threshold: int = DEFAULT_DEAD_LETTER_ESCALATION_THRESHOLD


@dataclass(frozen=True, kw_only=True)
class TakeoverDamping:
    """Damping and ownership rules applied when one agent name is contested.

    Cooldown blunts a single eviction storm; the oscillation window and
    threshold detect two waiters at war over one name; quarantine pins a
    thrashing name to its current owner. The lease offline TTL bounds how
    long a name's ownership lease outlives its holder's disconnect, so a
    re-arming owner re-takes its name and a stranger cannot squat it in the
    gap.
    """

    takeover_cooldown: float = DEFAULT_TAKEOVER_COOLDOWN
    takeover_oscillation_window: float = DEFAULT_TAKEOVER_OSCILLATION_WINDOW
    takeover_oscillation_threshold: int = DEFAULT_TAKEOVER_OSCILLATION_THRESHOLD
    takeover_quarantine: float = DEFAULT_TAKEOVER_QUARANTINE
    lease_offline_ttl: float = DEFAULT_LEASE_OFFLINE_TTL
