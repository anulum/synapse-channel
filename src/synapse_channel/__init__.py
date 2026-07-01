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
hub. The pieces compose: :class:`~synapse_channel.core.hub.SynapseHub` routes,
:class:`~synapse_channel.client.agent.SynapseAgent` connects, and
:class:`~synapse_channel.client.llm_worker.SynapseLLMWorker` answers on-channel through a
pluggable :mod:`~synapse_channel.client.chat_backends` backend. The ``synapse`` console
command (see :mod:`synapse_channel.cli`) drives all of it.
"""

from __future__ import annotations

__version__ = "0.81.0"

from synapse_channel.client.agent import (
    DEFAULT_HUB_URI,
    HUB_URI_ENV_VAR,
    SynapseAgent,
    default_hub_uri,
)
from synapse_channel.client.chat_backends import (
    ChatBackend,
    OpenAIChatClient,
    RuleBasedClient,
    sanitize_text,
)
from synapse_channel.client.launcher import plan_team, run_team
from synapse_channel.client.llm_worker import SynapseLLMWorker, is_service_message
from synapse_channel.client.routing import TaskClass, TieredChatClient, classify
from synapse_channel.client.supervisor import (
    Intervention,
    StallPolicy,
    SupervisorWorker,
    detect_stalls,
)
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.capability import CapabilityCard, CapabilityRegistry
from synapse_channel.core.capability_contracts import CapabilityContract
from synapse_channel.core.compaction import (
    CompactionResult,
    RetentionPolicy,
    compact,
)
from synapse_channel.core.deadlock import would_create_cycle
from synapse_channel.core.emit_gate import Decision, admit
from synapse_channel.core.finding import (
    ClaimStatus,
    EvidenceKind,
    Finding,
    Freshness,
    Lifecycle,
    Subkind,
)
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import MEMORY_KINDS
from synapse_channel.core.ledger import Blackboard, LedgerTask, ProgressNote
from synapse_channel.core.lifecycle import TaskStatus, can_transition
from synapse_channel.core.metrics import (
    Metric,
    collect_hub_metrics,
    health_snapshot,
    render_prometheus,
)
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import (
    PRIORITY_SENDERS,
    MessageType,
    addresses_project,
    build_envelope,
    is_directed,
    is_recipient,
    system_message,
    wakes,
)
from synapse_channel.core.scoping import paths_overlap, scopes_conflict
from synapse_channel.core.state import ResourceOffer, SynapseState, TaskClaim
from synapse_channel.relay import decode_lite, encode_lite

__all__ = [
    "DEFAULT_HUB_URI",
    "HUB_URI_ENV_VAR",
    "MEMORY_KINDS",
    "PRIORITY_SENDERS",
    "Blackboard",
    "CapabilityCard",
    "CapabilityContract",
    "CapabilityRegistry",
    "ChatBackend",
    "ClaimStatus",
    "CompactionResult",
    "Decision",
    "EventStore",
    "EvidenceKind",
    "Finding",
    "Freshness",
    "Intervention",
    "LedgerTask",
    "Lifecycle",
    "MessageType",
    "Metric",
    "OpenAIChatClient",
    "ProgressNote",
    "ResourceOffer",
    "RetentionPolicy",
    "RuleBasedClient",
    "Subkind",
    "StallPolicy",
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
    "admit",
    "build_envelope",
    "can_transition",
    "classify",
    "collect_hub_metrics",
    "compact",
    "decode_lite",
    "default_hub_uri",
    "detect_stalls",
    "encode_lite",
    "health_snapshot",
    "is_directed",
    "is_recipient",
    "is_service_message",
    "paths_overlap",
    "plan_team",
    "render_prometheus",
    "run_team",
    "sanitize_text",
    "scopes_conflict",
    "system_message",
    "wakes",
    "would_create_cycle",
]
