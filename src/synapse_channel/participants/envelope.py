# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — typed turn request and result envelopes for the Participant Fabric
"""Typed envelopes that carry one participant turn across the bus.

A multi-hop conversation between heterogeneous provider sessions degrades when each
hop re-summarises the last in free text — the telephone game. The Participant Fabric
avoids that by exchanging **typed result objects** instead: a participant receives a
:class:`TurnRequest` and returns a :class:`TurnResult` whose fields (answer, rationale,
abstention, provider session token, cost) are explicit, so a moderator or a peer reads
structure rather than reconstructing meaning from prose.

:class:`TurnRequest` is an in-process call argument; :class:`TurnResult` is the wire
shape that travels on the bus as a JSON chat payload, so it is a :class:`TypedDict`
with explicit serialisation helpers. The result is built from a provider-agnostic
:class:`~synapse_channel.participants.stream_json.StreamOutcome`, keeping this module
free of any provider-specific parsing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    from synapse_channel.participants.participant import ParticipantChannel
    from synapse_channel.participants.stream_json import StreamOutcome

ENVELOPE_KIND = "participant.turn_result"
"""Discriminator stored on every serialised :class:`TurnResult` payload.

The bus carries many chat-payload shapes; a consumer reading a payload off the wire
checks this marker before trusting the other fields, so a foreign or malformed message
is rejected rather than coerced into a turn result.
"""

REQUEST_KIND = "participant.turn_request"
"""Discriminator stored on every serialised :class:`TurnRequest` payload.

A bus-mediated channel (PTY or MCP) publishes a turn request to a peer over the bus; the
peer-side responder checks this marker before treating a payload as a turn to answer, so a
foreign or malformed message is rejected rather than acted upon.
"""


@dataclass(frozen=True)
class TurnRequest:
    """One turn handed to a participant.

    Parameters
    ----------
    topic_id : str
        Identifier shared by every turn of one conversation, so results posted to the
        bus can be correlated back to the question that prompted them.
    prompt : str
        The question or instruction this turn must answer.
    context : str, optional
        Shared framing injected into the provider as a system-level addendum (role,
        ground rules, and any framed peer contributions). It is never the provider's
        user prompt, so peer-supplied text cannot masquerade as the operator's ask.
    resume_session : str, optional
        Provider session token from a previous turn. When set, the driver resumes that
        session so the participant keeps memory across bus turns; empty starts fresh.
    """

    topic_id: str
    prompt: str
    context: str = ""
    resume_session: str = ""


class TurnResult(TypedDict):
    """Wire envelope for the structured outcome of one participant turn.

    This is the JSON shape posted to the bus. ``answer`` is the participant's reply;
    ``rationale`` is its disclosed reasoning when the provider streams it; ``abstained``
    and ``is_error`` separate "declined / produced nothing" from "the turn failed", each
    explained by ``reason``. ``session`` is the provider resume token for continuity and
    ``cost_usd`` the metered spend, which the conversation layer sums against a budget.
    """

    kind: str
    participant: str
    channel: str
    topic_id: str
    answer: str
    rationale: str
    abstained: bool
    is_error: bool
    reason: str
    session: str
    cost_usd: float
    stop_reason: str


def turn_request_to_payload(request: TurnRequest) -> str:
    """Serialise a turn request to a compact JSON bus payload.

    Used by a bus-mediated channel to publish a turn to a peer. The payload carries the
    :data:`REQUEST_KIND` discriminator so the peer-side responder can reject anything that is
    not a turn request.

    Parameters
    ----------
    request : TurnRequest
        The turn to serialise.

    Returns
    -------
    str
        Deterministic JSON (sorted keys) suitable for a chat payload.
    """
    return json.dumps(
        {
            "kind": REQUEST_KIND,
            "topic_id": request.topic_id,
            "prompt": request.prompt,
            "context": request.context,
            "resume_session": request.resume_session,
        },
        sort_keys=True,
        ensure_ascii=False,
    )


def turn_request_from_payload(payload: str) -> TurnRequest | None:
    """Parse a bus payload back into a turn request, or ``None`` when it is not one.

    The bus carries many payload shapes and untrusted content; this validates the
    discriminator and coerces each field to its declared type rather than trusting the
    incoming JSON, so a foreign or tampered payload is rejected instead of acted upon.

    Parameters
    ----------
    payload : str
        The raw chat payload.

    Returns
    -------
    TurnRequest or None
        The validated request, or ``None`` when the payload is not JSON, is not an object,
        or does not carry the turn-request discriminator.
    """
    try:
        raw: Any = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(raw, dict) or raw.get("kind") != REQUEST_KIND:
        return None
    return TurnRequest(
        topic_id=str(raw.get("topic_id", "")),
        prompt=str(raw.get("prompt", "")),
        context=str(raw.get("context", "")),
        resume_session=str(raw.get("resume_session", "")),
    )


def build_turn_result(
    *,
    participant: str,
    channel: ParticipantChannel,
    request: TurnRequest,
    outcome: StreamOutcome,
) -> TurnResult:
    """Assemble a :class:`TurnResult` from a provider-agnostic stream outcome.

    Parameters
    ----------
    participant : str
        Bus identity of the participant that produced this turn.
    channel : ParticipantChannel
        Channel the participant was driven through.
    request : TurnRequest
        The turn this result answers; supplies ``topic_id``.
    outcome : StreamOutcome
        Parsed provider output (answer, rationale, session, cost, error state).

    Returns
    -------
    TurnResult
        A turn declares ``abstained`` when it did not error yet produced no answer —
        a real "I have nothing to add", distinct from a failed turn.
    """
    answer = outcome.answer.strip()
    abstained = not outcome.is_error and answer == ""
    reason = outcome.subtype if outcome.is_error else ("no answer produced" if abstained else "")
    return TurnResult(
        kind=ENVELOPE_KIND,
        participant=participant,
        channel=channel.value,
        topic_id=request.topic_id,
        answer=answer,
        rationale=outcome.rationale.strip(),
        abstained=abstained,
        is_error=outcome.is_error,
        reason=reason,
        session=outcome.session_id,
        cost_usd=outcome.cost_usd,
        stop_reason=outcome.stop_reason,
    )


def error_turn_result(
    *,
    participant: str,
    channel: ParticipantChannel,
    request: TurnRequest,
    reason: str,
) -> TurnResult:
    """Build a failed-turn envelope when the provider could not be run at all.

    Used when the driver never obtained a parseable stream — the binary was missing,
    the process raised, or it timed out — so the failure travels the bus as a typed
    result rather than an exception that strands the conversation.

    Parameters
    ----------
    participant, channel, request : see :func:`build_turn_result`.
    reason : str
        Human-readable cause of the failure.

    Returns
    -------
    TurnResult
        An envelope with ``is_error`` set and empty answer/rationale.
    """
    return TurnResult(
        kind=ENVELOPE_KIND,
        participant=participant,
        channel=channel.value,
        topic_id=request.topic_id,
        answer="",
        rationale="",
        abstained=False,
        is_error=True,
        reason=reason,
        session="",
        cost_usd=0.0,
        stop_reason="error",
    )


def turn_result_to_payload(result: TurnResult) -> str:
    """Serialise a turn result to a compact JSON bus payload.

    Parameters
    ----------
    result : TurnResult
        The envelope to serialise.

    Returns
    -------
    str
        Deterministic JSON (sorted keys) suitable for a chat payload.
    """
    return json.dumps(result, sort_keys=True, ensure_ascii=False)


def turn_result_from_payload(payload: str) -> TurnResult | None:
    """Parse a bus payload back into a turn result, or ``None`` when it is not one.

    The bus carries many payload shapes and untrusted content; this validates the
    discriminator and coerces each field to its declared type rather than trusting the
    incoming JSON, so a foreign or tampered payload is rejected instead of propagated.

    Parameters
    ----------
    payload : str
        The raw chat payload.

    Returns
    -------
    TurnResult or None
        The validated envelope, or ``None`` when the payload is not JSON, is not an
        object, or does not carry the turn-result discriminator.
    """
    try:
        raw: Any = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(raw, dict) or raw.get("kind") != ENVELOPE_KIND:
        return None
    return TurnResult(
        kind=ENVELOPE_KIND,
        participant=str(raw.get("participant", "")),
        channel=str(raw.get("channel", "")),
        topic_id=str(raw.get("topic_id", "")),
        answer=str(raw.get("answer", "")),
        rationale=str(raw.get("rationale", "")),
        abstained=bool(raw.get("abstained", False)),
        is_error=bool(raw.get("is_error", False)),
        reason=str(raw.get("reason", "")),
        session=str(raw.get("session", "")),
        cost_usd=_as_float(raw.get("cost_usd", 0.0)),
        stop_reason=str(raw.get("stop_reason", "")),
    )


def _as_float(value: Any) -> float:
    """Return ``value`` as a float, defaulting to ``0.0`` on a non-numeric input."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
