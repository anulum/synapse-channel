# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Participant Fabric: drive provider sessions as uniform bus peers
"""Participant Fabric — drive heterogeneous provider sessions as uniform bus peers.

An **optional layer on top of the bus** (never folded into the single-dependency, no-LLM
core) that presents each provider CLI — Claude Code first — as a uniform
:class:`Participant`. A participant answers a :class:`TurnRequest` with a typed
:class:`TurnResult`; an :func:`conduct_exchange` runs the smallest multiplied-reasoning
loop (one participant answers, a second reacts to its result as fenced data); and
:class:`BusExchange` publishes each result to a live hub. Peer output crossing into another
participant always passes through :func:`frame_peer_contribution`, the cross-agent
prompt-injection boundary.

The headless channel is covered via :class:`HeadlessClaudeParticipant`. Session continuity
across turns is added by wrapping a participant in a :class:`ContinuitySeat`;
:func:`conduct_conversation` runs a bounded multi-round deliberation, and :class:`BusConversation`
publishes it to a live hub. Additional providers and the moderated multi-party conversation
layer build on these same pieces.
"""

from __future__ import annotations

from synapse_channel.participants.bus_relay import (
    BusConversation,
    BusConvocation,
    BusExchange,
)
from synapse_channel.participants.codex_stream import parse_codex_stream
from synapse_channel.participants.continuity import ContinuitySeat
from synapse_channel.participants.convene import (
    ConvocationTranscript,
    convene,
)
from synapse_channel.participants.conversation import (
    ConversationTranscript,
    conduct_conversation,
)
from synapse_channel.participants.envelope import (
    ENVELOPE_KIND,
    REQUEST_KIND,
    TurnRequest,
    TurnResult,
    build_turn_result,
    error_turn_result,
    turn_request_from_payload,
    turn_request_to_payload,
    turn_result_from_payload,
    turn_result_to_payload,
)
from synapse_channel.participants.exchange import (
    ExchangeTranscript,
    conduct_exchange,
)
from synapse_channel.participants.grok_stream import (
    GROK_SCHEMA_VERIFIED,
    parse_grok_stream,
)
from synapse_channel.participants.headless_claude import (
    HeadlessClaudeParticipant,
    build_claude_argv,
)
from synapse_channel.participants.headless_codex import (
    CodexParticipant,
    build_codex_argv,
    compose_codex_prompt,
)
from synapse_channel.participants.headless_grok import (
    GrokParticipant,
    build_grok_argv,
)
from synapse_channel.participants.headless_kimi import (
    KimiParticipant,
    build_kimi_argv,
    compose_kimi_prompt,
)
from synapse_channel.participants.headless_ollama import (
    OllamaParticipant,
    build_ollama_argv,
    compose_ollama_prompt,
)
from synapse_channel.participants.kimi_stream import (
    extract_kimi_session,
    parse_kimi_stream,
)
from synapse_channel.participants.modes import (
    ConversationMode,
    ModePolicy,
    select_mode,
)
from synapse_channel.participants.ollama_output import parse_ollama_output
from synapse_channel.participants.participant import (
    Participant,
    ParticipantChannel,
    ParticipantHealth,
)
from synapse_channel.participants.peer_boundary import (
    frame_peer_contribution,
    frame_peer_panel,
)
from synapse_channel.participants.stream_json import StreamOutcome, parse_claude_stream
from synapse_channel.participants.turn_relay import (
    DEGRADED_FREETEXT_STOP,
    RelaySettings,
    no_wake,
    relay_turn,
)
from synapse_channel.participants.turn_responder import (
    ResponderSettings,
    TurnResponder,
)

__all__ = [
    "DEGRADED_FREETEXT_STOP",
    "ENVELOPE_KIND",
    "GROK_SCHEMA_VERIFIED",
    "REQUEST_KIND",
    "BusConversation",
    "BusConvocation",
    "BusExchange",
    "CodexParticipant",
    "ContinuitySeat",
    "ConversationMode",
    "ConversationTranscript",
    "ConvocationTranscript",
    "ExchangeTranscript",
    "GrokParticipant",
    "KimiParticipant",
    "ModePolicy",
    "HeadlessClaudeParticipant",
    "OllamaParticipant",
    "Participant",
    "ParticipantChannel",
    "ParticipantHealth",
    "RelaySettings",
    "ResponderSettings",
    "StreamOutcome",
    "TurnRequest",
    "TurnResponder",
    "TurnResult",
    "build_claude_argv",
    "build_codex_argv",
    "build_grok_argv",
    "build_kimi_argv",
    "build_ollama_argv",
    "build_turn_result",
    "compose_codex_prompt",
    "compose_kimi_prompt",
    "compose_ollama_prompt",
    "conduct_conversation",
    "conduct_exchange",
    "convene",
    "error_turn_result",
    "extract_kimi_session",
    "frame_peer_contribution",
    "frame_peer_panel",
    "no_wake",
    "parse_claude_stream",
    "parse_codex_stream",
    "parse_grok_stream",
    "parse_kimi_stream",
    "parse_ollama_output",
    "relay_turn",
    "select_mode",
    "turn_request_from_payload",
    "turn_request_to_payload",
    "turn_result_from_payload",
    "turn_result_to_payload",
]
