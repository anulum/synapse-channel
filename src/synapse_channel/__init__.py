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

The public names below resolve lazily (:pep:`562`): the submodule behind a name
is imported on first attribute access, so ``import synapse_channel`` stays
cheap for consumers that touch only a slice of the surface — the CLI in
particular no longer pays for the whole facade at start-up.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

__version__ = "0.89.0"

if TYPE_CHECKING:
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
    from synapse_channel.core.hub_config import (
        FederationConfig,
        HubAuthConfig,
        HubConfig,
        HubLimits,
        HubMetricsConfig,
        MultiHubConfig,
        TakeoverDamping,
    )
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

#: Public name → "module:attribute" it resolves to. Every ``__all__`` entry
#: except ``__version__`` must appear here; the contract tests pin the two
#: collections against each other and against the real modules.
_EXPORTS: dict[str, str] = {
    "DEFAULT_HUB_URI": "synapse_channel.client.agent:DEFAULT_HUB_URI",
    "HUB_URI_ENV_VAR": "synapse_channel.client.agent:HUB_URI_ENV_VAR",
    "SynapseAgent": "synapse_channel.client.agent:SynapseAgent",
    "default_hub_uri": "synapse_channel.client.agent:default_hub_uri",
    "ChatBackend": "synapse_channel.client.chat_backends:ChatBackend",
    "OpenAIChatClient": "synapse_channel.client.chat_backends:OpenAIChatClient",
    "RuleBasedClient": "synapse_channel.client.chat_backends:RuleBasedClient",
    "sanitize_text": "synapse_channel.client.chat_backends:sanitize_text",
    "plan_team": "synapse_channel.client.launcher:plan_team",
    "run_team": "synapse_channel.client.launcher:run_team",
    "SynapseLLMWorker": "synapse_channel.client.llm_worker:SynapseLLMWorker",
    "is_service_message": "synapse_channel.client.llm_worker:is_service_message",
    "TaskClass": "synapse_channel.client.routing:TaskClass",
    "TieredChatClient": "synapse_channel.client.routing:TieredChatClient",
    "classify": "synapse_channel.client.routing:classify",
    "Intervention": "synapse_channel.client.supervisor:Intervention",
    "StallPolicy": "synapse_channel.client.supervisor:StallPolicy",
    "SupervisorWorker": "synapse_channel.client.supervisor:SupervisorWorker",
    "detect_stalls": "synapse_channel.client.supervisor:detect_stalls",
    "TokenAuthenticator": "synapse_channel.core.auth:TokenAuthenticator",
    "CapabilityCard": "synapse_channel.core.capability:CapabilityCard",
    "CapabilityRegistry": "synapse_channel.core.capability:CapabilityRegistry",
    "CapabilityContract": "synapse_channel.core.capability_contracts:CapabilityContract",
    "CompactionResult": "synapse_channel.core.compaction:CompactionResult",
    "RetentionPolicy": "synapse_channel.core.compaction:RetentionPolicy",
    "compact": "synapse_channel.core.compaction:compact",
    "would_create_cycle": "synapse_channel.core.deadlock:would_create_cycle",
    "Decision": "synapse_channel.core.emit_gate:Decision",
    "admit": "synapse_channel.core.emit_gate:admit",
    "ClaimStatus": "synapse_channel.core.finding:ClaimStatus",
    "EvidenceKind": "synapse_channel.core.finding:EvidenceKind",
    "Finding": "synapse_channel.core.finding:Finding",
    "Freshness": "synapse_channel.core.finding:Freshness",
    "Lifecycle": "synapse_channel.core.finding:Lifecycle",
    "Subkind": "synapse_channel.core.finding:Subkind",
    "SynapseHub": "synapse_channel.core.hub:SynapseHub",
    "FederationConfig": "synapse_channel.core.hub_config:FederationConfig",
    "HubAuthConfig": "synapse_channel.core.hub_config:HubAuthConfig",
    "HubConfig": "synapse_channel.core.hub_config:HubConfig",
    "HubLimits": "synapse_channel.core.hub_config:HubLimits",
    "HubMetricsConfig": "synapse_channel.core.hub_config:HubMetricsConfig",
    "MultiHubConfig": "synapse_channel.core.hub_config:MultiHubConfig",
    "TakeoverDamping": "synapse_channel.core.hub_config:TakeoverDamping",
    "MEMORY_KINDS": "synapse_channel.core.journal:MEMORY_KINDS",
    "Blackboard": "synapse_channel.core.ledger:Blackboard",
    "LedgerTask": "synapse_channel.core.ledger:LedgerTask",
    "ProgressNote": "synapse_channel.core.ledger:ProgressNote",
    "TaskStatus": "synapse_channel.core.lifecycle:TaskStatus",
    "can_transition": "synapse_channel.core.lifecycle:can_transition",
    "Metric": "synapse_channel.core.metrics:Metric",
    "collect_hub_metrics": "synapse_channel.core.metrics:collect_hub_metrics",
    "health_snapshot": "synapse_channel.core.metrics:health_snapshot",
    "render_prometheus": "synapse_channel.core.metrics:render_prometheus",
    "EventStore": "synapse_channel.core.persistence:EventStore",
    "PRIORITY_SENDERS": "synapse_channel.core.protocol:PRIORITY_SENDERS",
    "MessageType": "synapse_channel.core.protocol:MessageType",
    "addresses_project": "synapse_channel.core.protocol:addresses_project",
    "build_envelope": "synapse_channel.core.protocol:build_envelope",
    "is_directed": "synapse_channel.core.protocol:is_directed",
    "is_recipient": "synapse_channel.core.protocol:is_recipient",
    "system_message": "synapse_channel.core.protocol:system_message",
    "wakes": "synapse_channel.core.protocol:wakes",
    "paths_overlap": "synapse_channel.core.scoping:paths_overlap",
    "scopes_conflict": "synapse_channel.core.scoping:scopes_conflict",
    "ResourceOffer": "synapse_channel.core.state:ResourceOffer",
    "SynapseState": "synapse_channel.core.state:SynapseState",
    "TaskClaim": "synapse_channel.core.state:TaskClaim",
    "decode_lite": "synapse_channel.relay:decode_lite",
    "encode_lite": "synapse_channel.relay:encode_lite",
}


def __getattr__(name: str) -> Any:
    """Resolve a public name on first access and cache it on the package."""
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, _, attribute = target.partition(":")
    value = getattr(importlib.import_module(module_name), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))


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
    "FederationConfig",
    "Finding",
    "Freshness",
    "HubAuthConfig",
    "HubConfig",
    "HubLimits",
    "HubMetricsConfig",
    "Intervention",
    "LedgerTask",
    "Lifecycle",
    "MessageType",
    "Metric",
    "MultiHubConfig",
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
    "TakeoverDamping",
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
