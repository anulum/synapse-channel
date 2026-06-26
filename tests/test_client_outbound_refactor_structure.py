# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — structural regression tests for the client outbound split

from __future__ import annotations

from synapse_channel.client import agent_outbound
from synapse_channel.client.agent_outbound_base import AgentSendMixin
from synapse_channel.client.agent_outbound_capability import AgentCapabilityMixin
from synapse_channel.client.agent_outbound_ledger import AgentLedgerMixin
from synapse_channel.client.agent_outbound_memory import AgentMemoryMixin
from synapse_channel.client.agent_outbound_tasks import AgentTaskMutationMixin
from synapse_channel.client.agent_outbound_types import _OutboundAgent


def test_agent_outbound_compatibility_surface_composes_owner_mixins() -> None:
    assert agent_outbound._OutboundAgent is _OutboundAgent

    outbound_mro = agent_outbound.AgentOutboundMixin.__mro__
    assert AgentSendMixin in outbound_mro
    assert AgentMemoryMixin in outbound_mro
    assert AgentTaskMutationMixin in outbound_mro
    assert AgentLedgerMixin in outbound_mro
    assert AgentCapabilityMixin in outbound_mro


def test_agent_outbound_methods_are_inherited_from_owner_modules() -> None:
    assert agent_outbound.AgentOutboundMixin.send_message is AgentSendMixin.send_message
    assert agent_outbound.AgentOutboundMixin.chat is AgentSendMixin.chat
    assert agent_outbound.AgentOutboundMixin.log_recall is AgentMemoryMixin.log_recall
    assert agent_outbound.AgentOutboundMixin.record_finding is AgentMemoryMixin.record_finding
    assert agent_outbound.AgentOutboundMixin.claim is AgentTaskMutationMixin.claim
    assert agent_outbound.AgentOutboundMixin.release is AgentTaskMutationMixin.release
    assert agent_outbound.AgentOutboundMixin.update_task is AgentTaskMutationMixin.update_task
    assert agent_outbound.AgentOutboundMixin.handoff is AgentTaskMutationMixin.handoff
    assert (
        agent_outbound.AgentOutboundMixin.save_checkpoint is AgentTaskMutationMixin.save_checkpoint
    )
    assert agent_outbound.AgentOutboundMixin.request_wait is AgentTaskMutationMixin.request_wait
    assert agent_outbound.AgentOutboundMixin.post_task is AgentLedgerMixin.post_task
    assert agent_outbound.AgentOutboundMixin.update_ledger_task is (
        AgentLedgerMixin.update_ledger_task
    )
    assert agent_outbound.AgentOutboundMixin.post_progress is AgentLedgerMixin.post_progress
    assert agent_outbound.AgentOutboundMixin.advertise is AgentCapabilityMixin.advertise
