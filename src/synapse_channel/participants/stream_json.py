# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — parser for Claude Code headless stream-json output
"""Parse the ``stream-json`` event stream emitted by headless Claude Code.

``claude -p … --output-format stream-json --verbose`` writes one JSON object per line.
The schema this parser targets was captured from a real invocation (Claude Code 2.1.x):

- ``{"type": "system", "subtype": "init", "session_id": …, "model": …}`` — first event.
- ``{"type": "system", "subtype": "thinking_tokens", …}`` — progress noise, ignored.
- ``{"type": "rate_limit_event", …}`` — usage telemetry, ignored here.
- ``{"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": …},
  {"type": "text", "text": …}]}}`` — streamed reasoning and reply blocks.
- ``{"type": "result", "subtype": "success", "is_error": false, "result": "<answer>",
  "session_id": …, "total_cost_usd": …, "num_turns": …, "stop_reason": …}`` — the single
  terminal event and the authoritative source of the answer.

The terminal ``result`` event is authoritative: its ``result`` field is the answer and its
``session_id`` is the resume token. Assistant ``thinking`` blocks are collected as the
disclosed rationale. A stream that ends without a ``result`` event is reported as an error
so a truncated or crashed turn never reads as a silent empty answer. The parser is pure and
provider-shaped; turning a :class:`StreamOutcome` into a bus envelope is the envelope
module's job.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

NO_RESULT_SUBTYPE = "no_result"
"""Error subtype reported when the stream ends before a terminal ``result`` event."""


@dataclass(frozen=True)
class StreamOutcome:
    """Provider-agnostic distillation of one headless turn's event stream.

    Attributes
    ----------
    answer : str
        The final reply text from the terminal ``result`` event.
    rationale : str
        Concatenated ``thinking`` blocks streamed before the answer (may be empty).
    session_id : str
        Provider session token for resuming this conversation on a later turn.
    is_error : bool
        True when the provider reported an error or the stream had no terminal result.
    subtype : str
        The ``result`` event's subtype (e.g. ``"success"``) or :data:`NO_RESULT_SUBTYPE`.
    cost_usd : float
        Metered cost of the turn from ``total_cost_usd`` (``0.0`` when absent).
    num_turns : int
        Provider-internal turn count from the result event.
    stop_reason : str
        Why generation stopped (e.g. ``"end_turn"``).
    input_tokens : int
        Prompt/input tokens the provider reported for this turn (``0`` when none reported).
        Captured so the Fabric can feed the opt-in usage accounting rather than discard it.
    output_tokens : int
        Completion/output tokens the provider reported for this turn (``0`` when none reported).
    rate_limit_utilisation : float or None
        The provider's last reported rate-limit utilisation for this turn, in ``[0, 1]``, or
        ``None`` when none was reported. Captured so a router can deprioritise a provider that is
        close to its limit rather than discarding the signal.
    """

    answer: str
    rationale: str
    session_id: str
    is_error: bool
    subtype: str
    cost_usd: float
    num_turns: int
    stop_reason: str
    input_tokens: int = 0
    output_tokens: int = 0
    rate_limit_utilisation: float | None = None


def parse_claude_stream(lines: Iterable[str]) -> StreamOutcome:
    """Parse Claude headless ``stream-json`` lines into a :class:`StreamOutcome`.

    Parameters
    ----------
    lines : Iterable[str]
        The provider's stdout split into lines. Blank lines and lines that are not
        valid JSON objects are skipped rather than raising, so a stray banner or a
        partially flushed line cannot abort parsing of an otherwise complete stream.

    Returns
    -------
    StreamOutcome
        Distilled from the terminal ``result`` event. When no such event is present,
        the outcome is an error carrying :data:`NO_RESULT_SUBTYPE`, with the last
        streamed assistant text as a best-effort answer.
    """
    rationale_parts: list[str] = []
    streamed_text_parts: list[str] = []
    session_id = ""
    rate_limit_utilisation: float | None = None
    result_event: dict[str, Any] | None = None

    for line in lines:
        event = _decode(line)
        if event is None:
            continue
        event_type = event.get("type")
        if isinstance(event.get("session_id"), str) and not session_id:
            session_id = event["session_id"]
        if event_type == "assistant":
            _collect_assistant_blocks(event, rationale_parts, streamed_text_parts)
        elif event_type == "result":
            result_event = event
        elif event_type == "rate_limit_event":
            utilisation = _rate_limit_utilisation(event)
            if utilisation is not None:
                rate_limit_utilisation = utilisation

    if result_event is None:
        return StreamOutcome(
            answer="".join(streamed_text_parts),
            rationale="\n".join(rationale_parts),
            session_id=session_id,
            is_error=True,
            subtype=NO_RESULT_SUBTYPE,
            cost_usd=0.0,
            num_turns=0,
            stop_reason="",
            rate_limit_utilisation=rate_limit_utilisation,
        )

    answer = result_event.get("result")
    input_tokens, output_tokens = _usage_tokens(result_event.get("usage"))
    return StreamOutcome(
        answer=answer if isinstance(answer, str) else "".join(streamed_text_parts),
        rationale="\n".join(rationale_parts),
        session_id=_str_or(result_event.get("session_id"), session_id),
        is_error=bool(result_event.get("is_error", False)),
        subtype=_str_or(result_event.get("subtype"), "success"),
        cost_usd=_float_or(result_event.get("total_cost_usd"), 0.0),
        num_turns=_int_or(result_event.get("num_turns"), 0),
        stop_reason=_str_or(result_event.get("stop_reason"), ""),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        rate_limit_utilisation=rate_limit_utilisation,
    )


def _decode(line: str) -> dict[str, Any] | None:
    """Decode one stream line to a JSON object, or ``None`` when it is not one."""
    stripped = line.strip()
    if not stripped:
        return None
    try:
        value: Any = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _collect_assistant_blocks(
    event: dict[str, Any],
    rationale_parts: list[str],
    streamed_text_parts: list[str],
) -> None:
    """Append an assistant event's thinking and text blocks to the running buffers."""
    message = event.get("message")
    if not isinstance(message, dict):
        return
    content = message.get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "thinking" and isinstance(block.get("thinking"), str):
            rationale_parts.append(block["thinking"])
        elif block.get("type") == "text" and isinstance(block.get("text"), str):
            streamed_text_parts.append(block["text"])


def _rate_limit_utilisation(event: dict[str, Any]) -> float | None:
    """Return the utilisation from a ``rate_limit_event``, or ``None`` when absent.

    The captured shape carries ``rate_limit_info.utilization`` (a fraction). A missing or
    non-numeric value yields ``None`` so an absent signal is never invented; a boolean is rejected
    because ``bool`` is a subclass of ``int``.
    """
    info = event.get("rate_limit_info")
    if not isinstance(info, dict):
        return None
    utilisation = info.get("utilization")
    if isinstance(utilisation, bool) or not isinstance(utilisation, (int, float)):
        return None
    return float(utilisation)


def _usage_tokens(usage: Any) -> tuple[int, int]:
    """Return ``(input_tokens, output_tokens)`` from a result event's ``usage`` block.

    The captured schema (Claude Code 2.1.x) places ``input_tokens`` and ``output_tokens`` at the
    top level of ``usage`` (alongside cache and tool counters, which are not summed here). A
    missing or malformed block yields ``(0, 0)`` so an absent counter is never invented.
    """
    if not isinstance(usage, dict):
        return 0, 0
    return _int_or(usage.get("input_tokens"), 0), _int_or(usage.get("output_tokens"), 0)


def _str_or(value: Any, fallback: str) -> str:
    """Return ``value`` when it is a non-empty string, else ``fallback``."""
    return value if isinstance(value, str) and value else fallback


def _float_or(value: Any, fallback: float) -> float:
    """Return ``value`` coerced to float, or ``fallback`` when it is not numeric."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return fallback
    return float(value)


def _int_or(value: Any, fallback: int) -> int:
    """Return ``value`` coerced to int, or ``fallback`` when it is not an integer."""
    if isinstance(value, bool) or not isinstance(value, int):
        return fallback
    return value
