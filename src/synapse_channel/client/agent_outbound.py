# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — outbound protocol compatibility surface
"""Compatibility surface for :class:`synapse_channel.client.agent.SynapseAgent`."""

from __future__ import annotations

from synapse_channel.client.agent_outbound_base import AgentSendMixin
from synapse_channel.client.agent_outbound_capability import AgentCapabilityMixin
from synapse_channel.client.agent_outbound_ledger import AgentLedgerMixin
from synapse_channel.client.agent_outbound_memory import AgentMemoryMixin
from synapse_channel.client.agent_outbound_tasks import AgentTaskMutationMixin
from synapse_channel.client.agent_outbound_types import _OutboundAgent

__all__ = [
    "AgentOutboundMixin",
    "_OutboundAgent",
]


class AgentOutboundMixin(
    AgentSendMixin,
    AgentMemoryMixin,
    AgentTaskMutationMixin,
    AgentLedgerMixin,
    AgentCapabilityMixin,
):
    """Send chat, task mutation, memory, ledger, wait, and capability envelopes."""
