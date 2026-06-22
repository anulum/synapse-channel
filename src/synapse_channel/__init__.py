# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — public package surface and version
"""SYNAPSE CHANNEL — local-first multi-agent coordination bus.

A small WebSocket fabric that lets several agents share presence, claim and
release units of work, chat, and advertise resources through one authoritative
hub. The pieces compose: :class:`~synapse_channel.hub.SynapseHub` routes,
:class:`~synapse_channel.client.SynapseAgent` connects, and
:class:`~synapse_channel.llm_worker.SynapseLLMWorker` answers on-channel through a
pluggable :mod:`~synapse_channel.chat_backends` backend. The ``synapse`` console
command (see :mod:`synapse_channel.cli`) drives all of it.
"""

from __future__ import annotations

__version__ = "0.29.0"

from synapse_channel.auth import TokenAuthenticator
from synapse_channel.capability import CapabilityCard, CapabilityRegistry
from synapse_channel.chat_backends import (
    ChatBackend,
    OpenAIChatClient,
    RuleBasedClient,
    sanitize_text,
)
from synapse_channel.client import DEFAULT_HUB_URI, SynapseAgent
from synapse_channel.deadlock import would_create_cycle
from synapse_channel.hub import SynapseHub
from synapse_channel.launcher import plan_team, run_team
from synapse_channel.ledger import Blackboard, LedgerTask, ProgressNote
from synapse_channel.lifecycle import TaskStatus, can_transition
from synapse_channel.llm_worker import SynapseLLMWorker, is_service_message
from synapse_channel.persistence import EventStore
from synapse_channel.protocol import (
    PRIORITY_SENDERS,
    MessageType,
    addresses_project,
    build_envelope,
    is_directed,
    is_recipient,
    system_message,
    wakes,
)
from synapse_channel.relay import decode_lite, encode_lite
from synapse_channel.routing import TaskClass, TieredChatClient, classify
from synapse_channel.scoping import paths_overlap, scopes_conflict
from synapse_channel.state import ResourceOffer, SynapseState, TaskClaim
from synapse_channel.supervisor import Intervention, SupervisorWorker, detect_stalls

__all__ = [
    "DEFAULT_HUB_URI",
    "PRIORITY_SENDERS",
    "Blackboard",
    "CapabilityCard",
    "CapabilityRegistry",
    "ChatBackend",
    "EventStore",
    "Intervention",
    "LedgerTask",
    "MessageType",
    "OpenAIChatClient",
    "ProgressNote",
    "ResourceOffer",
    "RuleBasedClient",
    "SupervisorWorker",
    "SynapseAgent",
    "SynapseHub",
    "SynapseLLMWorker",
    "SynapseState",
    "TaskClaim",
    "TaskClass",
    "TaskStatus",
    "TieredChatClient",
    "TokenAuthenticator",
    "__version__",
    "addresses_project",
    "build_envelope",
    "can_transition",
    "classify",
    "decode_lite",
    "detect_stalls",
    "encode_lite",
    "is_directed",
    "is_recipient",
    "is_service_message",
    "paths_overlap",
    "plan_team",
    "run_team",
    "sanitize_text",
    "scopes_conflict",
    "system_message",
    "wakes",
    "would_create_cycle",
]
