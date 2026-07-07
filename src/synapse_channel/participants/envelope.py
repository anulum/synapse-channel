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
import math
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
    model : str, optional
        Model the operator wants this turn answered by, recorded so opt-in usage accounting can
        attribute tokens and cost to a model. Empty leaves the choice to the participant's own
        configured model; the result echoes whichever applied.
    """

    topic_id: str
    prompt: str
    context: str = ""
    resume_session: str = ""
    model: str = ""


class TurnResult(TypedDict):
    """Wire envelope for the structured outcome of one participant turn.

    This is the JSON shape posted to the bus. ``answer`` is the participant's reply;
    ``rationale`` is its disclosed reasoning when the provider streams it; ``abstained``
    and ``is_error`` separate "declined / produced nothing" from "the turn failed", each
    explained by ``reason``. ``session`` is the provider resume token for continuity and
    ``cost_usd`` the metered spend, which the conversation layer sums against a budget.
    ``model`` is the model the turn was attributed to and ``input_tokens`` / ``output_tokens``
    the provider-reported token split — carried so the Fabric can feed the opt-in usage
    accounting rather than discard the counts. ``rate_limit_utilisation`` is the provider's last
    reported rate-limit fraction (or ``None``), carried so a router can read a provider's headroom.
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
    model: str
    input_tokens: int
    output_tokens: int
    rate_limit_utilisation: float | None


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
            "model": request.model,
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
        model=str(raw.get("model", "")),
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
        The turn this result answers; supplies ``topic_id`` and the operator-declared ``model``.
    outcome : StreamOutcome
        Parsed provider output (answer, rationale, session, cost, error state).

    Returns
    -------
    TurnResult
        A turn declares ``abstained`` when it did not error yet produced no answer —
        a real "I have nothing to add", distinct from a failed turn. The recorded ``model`` is the
        request's; a driver that knows the model it actually used restamps it with
        :func:`stamp_model`.
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
        model=request.model,
        input_tokens=outcome.input_tokens,
        output_tokens=outcome.output_tokens,
        rate_limit_utilisation=outcome.rate_limit_utilisation,
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
        An envelope with ``is_error`` set and empty answer/rationale. The model is the request's;
        a driver restamps it with :func:`stamp_model`.
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
        model=request.model,
        input_tokens=0,
        output_tokens=0,
        rate_limit_utilisation=None,
    )


def stamp_model(result: TurnResult, model: str) -> TurnResult:
    """Return ``result`` with its model set to the driver's, when it knows one.

    A driver that knows the model it actually ran (e.g. a headless participant configured with a
    model) restamps its result so the recorded model is the true one rather than only the
    operator-declared request model. An empty ``model``, or a result that already carries one,
    is left unchanged, so the operator's declaration is never overwritten by a blank.

    Parameters
    ----------
    result : TurnResult
        The result to attribute.
    model : str
        The model the driver used; ignored when empty.

    Returns
    -------
    TurnResult
        ``result`` unchanged when it already names a model or ``model`` is empty; otherwise a copy
        carrying ``model``.
    """
    if result["model"] or not model:
        return result
    stamped = result.copy()
    stamped["model"] = model
    return stamped


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
        model=str(raw.get("model", "")),
        input_tokens=_as_int(raw.get("input_tokens", 0)),
        output_tokens=_as_int(raw.get("output_tokens", 0)),
        rate_limit_utilisation=_as_optional_float(raw.get("rate_limit_utilisation")),
    )


def _as_float(value: Any) -> float:
    """Return ``value`` as a finite float, defaulting to ``0.0`` on a bad input.

    A non-numeric, non-finite (``inf``/``nan``), or double-overflowing value defaults
    to zero rather than raising or admitting a non-finite cost into a usage note.
    """
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _as_int(value: Any) -> int:
    """Return ``value`` as a non-negative int, defaulting to ``0`` on a bad input.

    A token count off the wire is coerced and clamped at zero so a foreign, negative,
    or non-finite value cannot later make a usage note malformed — ``int()`` of a
    non-finite float raises, so ``inf``/``nan`` is treated as absent before conversion.
    """
    if isinstance(value, bool):
        return 0
    if isinstance(value, float) and not math.isfinite(value):
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def _as_optional_float(value: Any) -> float | None:
    """Return ``value`` as a finite float, or ``None`` when absent or unusable.

    Used for an optional signal (rate-limit utilisation) where a missing value is
    meaningful, so ``None`` is preserved rather than coerced to zero; a boolean, a
    non-finite value, or a double-overflowing integer is rejected.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None
