# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — parser for the Ollama REST generate response
"""Parse the JSON body returned by the Ollama REST ``/api/generate`` endpoint.

Unlike the streaming CLI parsers, the REST endpoint (called with ``stream=false``) returns one
JSON object. The schema this parser targets was captured from a real call (Ollama 0.20.2):

- ``response`` — the model's reply text.
- ``prompt_eval_count`` / ``eval_count`` — input and output token counts.
- ``done`` / ``done_reason`` — completion flag and reason (e.g. ``"stop"``).
- ``model`` — the model that answered.

A local model has no monetary cost, so ``cost_usd`` is always ``0.0``, and there is no provider
session token — continuity for an API seat comes from the conversation's fenced context, as for the
CLI driver. A body with no ``response`` text is reported as an error so an empty or malformed reply
never reads as a silent empty answer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from synapse_channel.participants.stream_json import StreamOutcome

if TYPE_CHECKING:
    from collections.abc import Mapping

NO_RESPONSE_SUBTYPE = "no_response"
"""Error subtype reported when the response body carries no answer text."""


def parse_ollama_api_response(body: Mapping[str, Any]) -> StreamOutcome:
    """Distil one Ollama REST ``/api/generate`` response into a :class:`StreamOutcome`.

    Parameters
    ----------
    body : collections.abc.Mapping[str, Any]
        The decoded JSON response object.

    Returns
    -------
    StreamOutcome
        ``answer`` is the trimmed ``response`` text; ``input_tokens`` / ``output_tokens`` come from
        ``prompt_eval_count`` / ``eval_count``; ``stop_reason`` from ``done_reason``. A body whose
        ``response`` is missing or blank is an error carrying :data:`NO_RESPONSE_SUBTYPE`.
    """
    response = body.get("response")
    answer = response.strip() if isinstance(response, str) else ""
    if not answer:
        return StreamOutcome(
            answer="",
            rationale="",
            session_id="",
            is_error=True,
            subtype=NO_RESPONSE_SUBTYPE,
            cost_usd=0.0,
            num_turns=0,
            stop_reason="",
        )
    done_reason = body.get("done_reason")
    return StreamOutcome(
        answer=answer,
        rationale="",
        session_id="",
        is_error=False,
        subtype="success",
        cost_usd=0.0,
        num_turns=0,
        stop_reason=done_reason if isinstance(done_reason, str) else "",
        input_tokens=_as_int(body.get("prompt_eval_count")),
        output_tokens=_as_int(body.get("eval_count")),
    )


def _as_int(value: Any) -> int:
    """Return a non-negative int from ``value``, or ``0`` when it is not a usable count."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value
