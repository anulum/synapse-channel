# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — parser for Antigravity CLI `--output-format stream-json` output
"""Parse the ``stream-json`` stream emitted by headless ``agy --print``.

Schema verification
-------------------
The event shape below was captured on 2026-11-17 from real turns of the
installed Antigravity CLI 1.5.10 (``agy --print <prompt> --output-format
stream-json --mode plan``), including one turn resumed with
``--conversation=<id>`` and one turn whose only command was auto-denied
because headless mode cannot prompt for it. One JSON object per line:

- ``{"event": "init", "conversation_id": …, "init": {"cwd": …, "tools": [...],
  "permission_mode": …, "expanded_commands": [...]}}`` — first event.
- ``{"event": "step_update", "step_update": {"conversation_id": …,
  "step_index": …, "state": "ACTIVE" | "DONE" | "ERROR", "step_type":
  "user_input" | "agent_response" | "tool" | "system_message", …}}`` — one per
  step change. An ``agent_response`` step may carry ``text_delta`` (streamed
  answer text) and ``usage``; a ``tool`` step carries ``tool_name`` and
  ``tool_info``.
- ``{"event": "result", "result": {"conversation_id": …, "status": "SUCCESS" |
  …, "response": …, "duration_seconds": …, "num_turns": …, "usage":
  {"input_tokens": …, "output_tokens": …, "thinking_tokens": …,
  "cache_read_tokens": …, "total_tokens": …}, "denied_actions"?: [{"action": …,
  "display_name": …}]}}`` — the single terminal event.

A ``SUCCESS`` result can carry an empty ``response``: the captured denied turn
ended so after its only command was auto-denied, and the CLI printed on
standard error that no output was produced. A turn with an empty response and
denied actions is an error here, never an empty answer. An empty response with
no denied action stays a success without an answer, which the turn envelope
records as an abstention.

Fixtures in ``tests/fixtures/agy_stream/``: the reply and resumed turns are the
captured bytes with only ``init.cwd`` replaced; the denied turn keeps every
event and field, with tool parameters, tool outputs and the tool error message
replaced, because they held workspace contents.

:data:`AGY_SCHEMA_VERIFIED` is therefore ``True``. If a later release changes
the wire shape, capture it again, update this parser, and verify again.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from synapse_channel.participants.stream_json import NO_RESULT_SUBTYPE, StreamOutcome

AGY_SCHEMA_VERIFIED = True
"""Whether the Antigravity CLI stream schema has been captured from real turns.

``True``: the envelope was captured from the installed Antigravity CLI 1.5.10
(see the module docstring for the commands and the fixtures).
"""

NO_ANSWER_SUBTYPE = "no_answer"
"""The subtype of a ``SUCCESS`` result with an empty response after denied actions."""


def parse_agy_stream(lines: Iterable[str]) -> StreamOutcome:
    """Parse Antigravity CLI ``--output-format stream-json`` lines into a :class:`StreamOutcome`.

    Parameters
    ----------
    lines : Iterable[str]
        The provider's stdout split into lines. Blank lines and non-JSON lines are
        skipped so a partial flush cannot abort an otherwise complete stream.

    Returns
    -------
    StreamOutcome
        The answer is the terminal ``result``'s ``response``. A stream without a
        ``result`` is an error carrying :data:`NO_RESULT_SUBTYPE` and the
        streamed ``text_delta`` text seen so far. A ``result`` whose ``status``
        is not ``SUCCESS`` is an error carrying that status in lower case. A
        ``SUCCESS`` with an empty response and denied actions is an error
        carrying :data:`NO_ANSWER_SUBTYPE`, and its stop reason names the denied
        actions.
    """
    deltas, session_id, result = _collect(lines)
    if result is None:
        return _error(answer="".join(deltas), session_id=session_id, subtype=NO_RESULT_SUBTYPE)
    return _result_outcome(result, _str_field(result, "conversation_id") or session_id)


def _collect(lines: Iterable[str]) -> tuple[list[str], str, dict[str, Any] | None]:
    """Return the streamed answer deltas, the ``init`` conversation id and the last ``result``."""
    deltas: list[str] = []
    session_id = ""
    result: dict[str, Any] | None = None
    for line in lines:
        event = _decode(line)
        if event is None:
            continue
        kind = event.get("event")
        if kind == "init":
            session_id = _str_field(event, "conversation_id") or session_id
        elif kind == "step_update":
            step = event.get("step_update")
            if isinstance(step, dict) and step.get("step_type") == "agent_response":
                delta = step.get("text_delta")
                if isinstance(delta, str):
                    deltas.append(delta)
        elif kind == "result" and isinstance(event.get("result"), dict):
            result = event["result"]
    return deltas, session_id, result


def _result_outcome(result: dict[str, Any], session_id: str) -> StreamOutcome:
    """Turn the terminal ``result`` event into an outcome, as :func:`parse_agy_stream` states."""
    response = result.get("response")
    answer = response if isinstance(response, str) else ""
    status = _str_field(result, "status")
    input_tokens, output_tokens = _tokens(result.get("usage"))
    turns = result.get("num_turns")
    num_turns = turns if isinstance(turns, int) and not isinstance(turns, bool) else 0
    if status != "SUCCESS":
        return _error(
            answer=answer,
            session_id=session_id,
            subtype=status.lower() or "error",
            tokens=(input_tokens, output_tokens),
        )
    denied = _denied_actions(result.get("denied_actions"))
    if not answer.strip() and denied:
        return _error(
            answer="",
            session_id=session_id,
            subtype=NO_ANSWER_SUBTYPE,
            stop_reason="the provider produced no answer; denied actions: " + ", ".join(denied),
            tokens=(input_tokens, output_tokens),
        )
    return StreamOutcome(
        answer=answer.strip(),
        rationale="",
        session_id=session_id,
        is_error=False,
        subtype="success",
        cost_usd=0.0,
        num_turns=num_turns,
        stop_reason="end_turn",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _error(
    *,
    answer: str,
    session_id: str,
    subtype: str,
    stop_reason: str = "",
    tokens: tuple[int, int] = (0, 0),
) -> StreamOutcome:
    """Return an error outcome that keeps any text and token counts the stream carried.

    A failed turn still spent its tokens, so usage accounting keeps them.
    """
    return StreamOutcome(
        answer=answer,
        rationale="",
        session_id=session_id,
        is_error=True,
        subtype=subtype,
        cost_usd=0.0,
        num_turns=0,
        stop_reason=stop_reason,
        input_tokens=tokens[0],
        output_tokens=tokens[1],
    )


def _denied_actions(denied: object) -> list[str]:
    """Return the sorted names of the actions headless mode denied."""
    if not isinstance(denied, list):
        return []
    return sorted(
        {
            item["action"]
            for item in denied
            if isinstance(item, dict) and isinstance(item.get("action"), str)
        }
    )


def _tokens(usage: object) -> tuple[int, int]:
    """Return the reported input and output token counts, ``0`` when absent."""
    if not isinstance(usage, dict):
        return 0, 0
    counts = []
    for key in ("input_tokens", "output_tokens"):
        value = usage.get(key)
        counts.append(value if isinstance(value, int) and not isinstance(value, bool) else 0)
    return counts[0], counts[1]


def _decode(line: str) -> dict[str, Any] | None:
    """Return one JSON object line, or ``None`` when the line is noise."""
    text = line.strip()
    if not text:
        return None
    try:
        decoded: object = json.loads(text)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _str_field(event: dict[str, Any], key: str) -> str:
    """Return ``key``'s value when it is a non-empty string, else ``""``."""
    value = event.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else ""
