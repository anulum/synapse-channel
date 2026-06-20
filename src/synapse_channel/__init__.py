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

__version__ = "0.3.0"

from synapse_channel.chat_backends import (
    ChatBackend,
    OpenAIChatClient,
    RuleBasedClient,
    sanitize_text,
)
from synapse_channel.client import DEFAULT_HUB_URI, SynapseAgent
from synapse_channel.hub import SynapseHub
from synapse_channel.launcher import plan_team, run_team
from synapse_channel.llm_worker import SynapseLLMWorker, is_service_message
from synapse_channel.protocol import (
    MessageType,
    build_envelope,
    system_message,
)
from synapse_channel.state import ResourceOffer, SynapseState, TaskClaim

__all__ = [
    "DEFAULT_HUB_URI",
    "ChatBackend",
    "MessageType",
    "OpenAIChatClient",
    "ResourceOffer",
    "RuleBasedClient",
    "SynapseAgent",
    "SynapseHub",
    "SynapseLLMWorker",
    "SynapseState",
    "TaskClaim",
    "__version__",
    "build_envelope",
    "is_service_message",
    "plan_team",
    "run_team",
    "sanitize_text",
    "system_message",
]
